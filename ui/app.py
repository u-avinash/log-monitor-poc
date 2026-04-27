"""Streamlit dashboard for Log Monitor POC."""
import difflib
import html
import os
import re
import sys
from datetime import datetime
from typing import List, Optional

import requests
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore
except Exception:  # pragma: no cover
    st_autorefresh = None

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_settings
from storage.database import get_db
from storage.incident_repository import IncidentRepository
from storage.models import IncidentResponse, IncidentStatus, Severity

# Page config
st.set_page_config(
    page_title="Log Monitor POC",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize settings
settings = get_settings()

# Auto-refresh configuration
AUTO_REFRESH_INTERVAL = 5  # seconds

# Cache TTLs (seconds) - keep low to feel "live" without heavy reruns
INCIDENTS_CACHE_TTL_SECONDS = 3
HEALTH_CACHE_TTL_SECONDS = 5

# Custom CSS
st.markdown(
    """
<style>
    .stMetric {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 5px;
    }
    .severity-critical {
        color: #ff0000;
        font-weight: bold;
    }
    .severity-high {
        color: #ff6600;
        font-weight: bold;
    }
    .severity-medium {
        color: #ffaa00;
        font-weight: bold;
    }
    .severity-low {
        color: #00aa00;
        font-weight: bold;
    }
    .workflow-step {
        display: inline-block;
        padding: 8px 12px;
        margin: 5px;
        border-radius: 5px;
        font-size: 14px;
        font-weight: 500;
    }
    .workflow-completed {
        background-color: #28a745;
        color: white;
    }
    .workflow-active {
        background-color: #ffc107;
        color: black;
        animation: pulse 2s infinite;
    }
    .workflow-pending {
        background-color: #e9ecef;
        color: #6c757d;
    }
    .workflow-failed {
        background-color: #dc3545;
        color: white;
    }
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.6; }
    }
    /* Sidebar status row styling */
    .status-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 0;
        border-bottom: 1px solid #f0f0f0;
    }
    .status-label {
        font-weight: 500;
        color: #31333f;
    }
    .status-value {
        font-weight: 600;
        text-align: right;
    }
    .status-online {
        color: #28a745;
    }
    .status-offline {
        color: #dc3545;
    }
    .status-enabled {
        color: #28a745;
    }
    .status-disabled {
        color: #dc3545;
    }

    /* Proposed Fix diff styles (GitHub-ish) */
    .ax-diff {
        background: #ffffff;
        border: 1px solid #d0d7de;
        border-radius: 8px;
        overflow: hidden;
        margin: 12px 0 18px 0;
        font-family: ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, Liberation Mono, monospace;
        font-size: 13px;
        line-height: 20px;
        color: #24292f;
    }
    .ax-diff-header {
        padding: 12px 14px;
        border-bottom: 1px solid #d0d7de;
        background: #f6f8fa;
        font-weight: 600;
    }
    .ax-diff-body {
        width: 100%;
        overflow-x: auto;
    }
    .ax-diff-table {
        border-collapse: collapse;
        width: 100%;
        min-width: 720px;
    }
    .ax-diff-table td {
        padding: 0;
        vertical-align: top;
    }
    .ax-diff-ln {
        width: 1%;
        white-space: nowrap;
        padding: 0 10px;
        text-align: right;
        color: #57606a;
        border-right: 1px solid #d0d7de;
        user-select: none;
    }
    .ax-diff-sign {
        width: 1%;
        white-space: nowrap;
        padding: 0 10px;
        text-align: center;
        border-right: 1px solid #d0d7de;
        user-select: none;
        font-weight: 700;
    }
    .ax-diff-code {
        padding: 0 12px;
        white-space: pre;
    }
    .ax-diff-row-add { background: #e6ffed; }
    .ax-diff-row-add .ax-diff-sign { color: #116329; }
    .ax-diff-row-del { background: #ffebe9; }
    .ax-diff-row-del .ax-diff-sign { color: #cf222e; }
    .ax-diff-row-ctx { background: #ffffff; }
</style>
""",
    unsafe_allow_html=True,
)


def get_severity_color(severity: str) -> str:
    """Get color class for severity."""
    colors = {
        "CRITICAL": "severity-critical",
        "HIGH": "severity-high",
        "MEDIUM": "severity-medium",
        "LOW": "severity-low",
    }
    return colors.get(severity, "")


def render_workflow_progress(incident):
    """Render workflow progress visualization for an incident."""
    # Define all possible workflow steps in CORRECT EXECUTION ORDER
    all_steps = [
        ("assess_severity", "🔍 Severity Assessment"),
        ("generate_rca", "📋 RCA Generation"),
        ("generate_fix", "🔧 Fix Generation"),
        ("generate_pdf", "📄 PDF Generation"),
        ("reflect", "🤔 Quality Review"),
        ("await_approval", "👤 Approval"),
        ("generate_patch", "📦 Patch Generation"),
        ("send_notifications", "🔔 Notifications"),
        ("create_jira", "🎫 Jira Creation"),
        ("create_pr", "🔀 PR Creation"),
        ("finalize", "✅ Finalize"),
    ]

    # Handle incidents that don't have workflow tracking fields (legacy data)
    completed_steps = getattr(incident, "workflow_completed_steps", None) or []
    current_node = getattr(incident, "current_workflow_node", None) or ""
    progress_pct = getattr(incident, "workflow_progress_pct", None) or 0.0

    # Normalize step names for matching (handle variations like "Create Jira Pr" vs "create_jira")
    def normalize_step_name(step_name):
        """Convert step names to lowercase with underscores for consistent matching."""
        return step_name.lower().replace(" ", "_").replace("__", "_")

    # Normalize completed steps for matching
    normalized_completed = [normalize_step_name(step) for step in completed_steps]

    # If no workflow data available, show message
    if not current_node and not completed_steps:
        st.info(
            "ℹ️ This incident was created before workflow tracking was enabled. Workflow progress not available."
        )
        return

    # Check if workflow failed
    is_failed = "failed" in current_node.lower()

    # Legacy workflow mapping: old workflow had "create_jira_pr" that did both Jira and PR
    # but only Jira was actually created (PR was not implemented in old workflow)
    has_legacy_jira_pr = "create_jira_pr" in normalized_completed

    # Build HTML for workflow steps
    workflow_html = '<div style="margin: 15px 0;">'

    for step_id, step_label in all_steps:
        # Check if this step is completed (using normalized names for matching)
        normalized_step_id = normalize_step_name(step_id)

        # Special handling for legacy "create_jira_pr" step
        if has_legacy_jira_pr:
            # Only map "create_jira_pr" to "create_jira", not "create_pr"
            # (since PR was never actually created in old workflow)
            if step_id == "create_jira":
                is_completed = True
            elif step_id == "create_pr":
                is_completed = False  # PR was never created in legacy workflow
            else:
                is_completed = normalized_step_id in normalized_completed or step_id in completed_steps
        else:
            # Normal matching for new workflow
            is_completed = normalized_step_id in normalized_completed or step_id in completed_steps

        if is_completed:
            css_class = "workflow-completed"
            icon = "✓"
        elif step_id in current_node or (is_failed and step_id in current_node):
            css_class = "workflow-failed" if is_failed else "workflow-active"
            icon = "❌" if is_failed else "⏳"
        else:
            css_class = "workflow-pending"
            icon = "○"

        workflow_html += f'<span class="workflow-step {css_class}">{icon} {step_label}</span>'

    workflow_html += "</div>"

    # Display workflow visualization
    st.markdown("### 🔄 Workflow Progress")
    st.markdown(workflow_html, unsafe_allow_html=True)

    # Progress bar with color
    if progress_pct > 0:
        # Normalize progress to 0.0-1.0 range (in case it's stored as 0-100)
        normalized_progress = progress_pct if progress_pct <= 1.0 else progress_pct / 100.0
        normalized_progress = min(normalized_progress, 1.0)  # Cap at 1.0
        progress_text = f"{int(normalized_progress * 100)}% Complete"
        st.progress(normalized_progress, text=progress_text)
    else:
        st.progress(0.0, text="0% Complete")

    # Current status message
    if current_node:
        if is_failed:
            st.error(f"❌ Workflow failed at: {current_node.replace('_', ' ').title()}")
        elif progress_pct >= 1.0:
            st.success("✅ Workflow completed successfully!")
        else:
            st.info(f"⏳ Currently processing: {current_node.replace('_', ' ').title()}")

    # Timeline
    if completed_steps:
        with st.expander("📊 Workflow Timeline", expanded=False):
            st.markdown(f"**Started:** {incident.created_at}")
            st.markdown(f"**Last Updated:** {incident.updated_at}")
            st.markdown(f"**Steps Completed:** {len(completed_steps)}/{len(all_steps)}")

            if completed_steps:
                st.markdown("**Completed Steps:**")
                # Sort completed steps by their order in the workflow
                normalized_completed = [normalize_step_name(step) for step in completed_steps]

                # Display completed steps in workflow order
                for step_id, step_label in all_steps:
                    normalized_step_id = normalize_step_name(step_id)

                    # Check if step is completed (handle legacy step names)
                    is_completed = False
                    if has_legacy_jira_pr and step_id == "create_jira":
                        is_completed = True
                    elif normalized_step_id in normalized_completed or step_id in completed_steps:
                        is_completed = True

                    if is_completed:
                        st.markdown(f"  ✓ {step_label}")


APPROVAL_REQUEST_TIMEOUT_SECONDS = 60


@st.cache_data(ttl=HEALTH_CACHE_TTL_SECONDS)
def _check_api_online(health_url: str) -> bool:
    """Return True if API health endpoint responds 200."""
    try:
        r = requests.get(health_url, timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@st.cache_data(ttl=INCIDENTS_CACHE_TTL_SECONDS)
def _get_all_incidents_cached(_refresh_key: int):
    """Fetch incidents from DB with a cache key to force refresh on demand."""
    db = next(get_db())
    repo = IncidentRepository(db)
    return repo.get_all(limit=1000)


def _extract_two_code_blocks(text: str) -> Optional[tuple[str, str]]:
    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)\n```", text or "", re.DOTALL)
    if len(code_blocks) < 2:
        return None
    return code_blocks[0].strip(), code_blocks[1].strip()


def _render_diff_view(proposed_fix: str):
    """
    Render GitHub-PR-like diff view for the Proposed Fix section.

    - Extracts first two fenced code blocks from proposed_fix (original, fixed)
    - Renders only changed lines with + / - coloring similar to GitHub PR
    - Properly escapes code to prevent HTML being interpreted
    """
    try:
        blocks = _extract_two_code_blocks(proposed_fix)
        if not blocks:
            st.markdown(proposed_fix)
            return

        original_code, fixed_code = blocks
        a_lines = original_code.splitlines()
        b_lines = fixed_code.splitlines()

        sm = difflib.SequenceMatcher(a=a_lines, b=b_lines)
        opcodes = sm.get_opcodes()

        rows: list[tuple[Optional[int], Optional[int], str, str]] = []

        a_ln = 1
        b_ln = 1
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                # Advance counters without emitting rows to keep view "only modified lines"
                a_ln += i2 - i1
                b_ln += j2 - j1
                continue

            if tag in ("replace", "delete"):
                for k in range(i1, i2):
                    rows.append((a_ln, None, "-", a_lines[k]))
                    a_ln += 1

            if tag in ("replace", "insert"):
                for k in range(j1, j2):
                    rows.append((None, b_ln, "+", b_lines[k]))
                    b_ln += 1

        if not rows:
            st.markdown(
                """
<div class="ax-diff">
  <div class="ax-diff-header">🔧 Code Changes Required</div>
  <div style="padding: 14px; color: #57606a;">No changes detected (original and fixed code are identical).</div>
</div>
""",
                unsafe_allow_html=True,
            )
            return

        table_rows_html = []
        for old_no, new_no, sign, text in rows:
            if sign == "+":
                row_class = "ax-diff-row-add"
            elif sign == "-":
                row_class = "ax-diff-row-del"
            else:
                row_class = "ax-diff-row-ctx"

            old_cell = "" if old_no is None else str(old_no)
            new_cell = "" if new_no is None else str(new_no)
            code_html = html.escape(text, quote=False)

            table_rows_html.append(
                f"""
<tr class="{row_class}">
  <td class="ax-diff-ln">{old_cell}</td>
  <td class="ax-diff-ln">{new_cell}</td>
  <td class="ax-diff-sign">{html.escape(sign)}</td>
  <td class="ax-diff-code">{code_html}</td>
</tr>
""".strip()
            )

        diff_html = f"""
<div class="ax-diff">
  <div class="ax-diff-header">🔧 Code Changes Required <span style="font-weight: 400; color: #57606a;">(only modified lines)</span></div>
  <div class="ax-diff-body">
    <table class="ax-diff-table">
      {''.join(table_rows_html)}
    </table>
  </div>
</div>
""".strip()

        st.markdown(diff_html, unsafe_allow_html=True)

    except Exception as e:
        # Fallback on error
        st.error(f"Error rendering diff view: {str(e)}")
        st.markdown(proposed_fix)


def approve_fix(incident_id: str, approved: bool, comment: str = ""):
    """Approve or reject a fix.

    Note: Approval can kick off post-approval automation (notifications/Jira/PR).
    Even though the API uses background tasks, it can still take longer than a few
    seconds under load, so we use a higher timeout and handle timeouts gracefully.
    """
    try:
        response = requests.post(
            f"http://localhost:{settings.ingestion_api_port}/incidents/{incident_id}/approve",
            json={"incident_id": incident_id, "approved": approved, "comment": comment},
            timeout=APPROVAL_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            return True

        st.error(f"Failed to submit approval: {response.text}")
        return False
    except requests.exceptions.ReadTimeout:
        # The backend may still have accepted the request but took too long to reply.
        st.warning(
            "Approval request timed out waiting for a response from the API. "
            "The backend may still be processing; refresh in a few seconds and check the incident status."
        )
        return True
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to the ingestion API on port 8000. Make sure the API is running.")
        return False
    except Exception as e:
        st.error(f"Error submitting approval: {str(e)}")
        return False


def main():
    """Main Streamlit application."""
    st.title("🔍 Log Monitor POC Dashboard")
    st.markdown("**AI-Powered Error Detection, RCA Generation & Auto-Fix**")

    # Local refresh key: changing this invalidates cached incident fetch
    if "incidents_refresh_key" not in st.session_state:
        st.session_state.incidents_refresh_key = 0

    # Fetch incidents first (needed for filters)
    all_incidents = _get_all_incidents_cached(st.session_state.incidents_refresh_key)

    # Sidebar - Configuration Panel
    with st.sidebar:
        st.header("⚙️ Configuration Panel")

        # === QUICK ACTIONS (Top Priority) ===
        st.markdown("### 🎬 Quick Actions")

        if st.button("🔄 Refresh Data", use_container_width=True, help="Reload dashboard data"):
            st.session_state.incidents_refresh_key += 1
            st.rerun()

        st.divider()

        # === SYSTEM STATUS ===
        st.markdown("### 🖥️ System Status")

        # Check API connectivity (cached to avoid blocking UI every rerun)
        health_url = f"http://localhost:{settings.ingestion_api_port}/health"
        api_online = _check_api_online(health_url)

        # System status
        api_status_class = "status-online" if api_online else "status-offline"
        api_status_text = "Online" if api_online else "Offline"
        api_icon = "🟢" if api_online else "🔴"

        autofix_class = "status-enabled" if settings.auto_fix_enabled else "status-disabled"
        autofix_text = "Enabled" if settings.auto_fix_enabled else "Disabled"
        autofix_icon = "🟢" if settings.auto_fix_enabled else "🔴"

        st.markdown(
            f"""
        <div style="background-color: #f8f9fa; padding: 12px; border-radius: 8px; margin-bottom: 10px;">
            <div class="status-row">
                <span class="status-label">API Service</span>
                <span class="status-value {api_status_class}">{api_icon} {api_status_text}</span>
            </div>
            <div class="status-row">
                <span class="status-label">LLM Provider</span>
                <span class="status-value" style="color: #0066cc;">{settings.llm_provider.upper()}</span>
            </div>
            <div class="status-row">
                <span class="status-label">Auto-Fix</span>
                <span class="status-value {autofix_class}">{autofix_icon} {autofix_text}</span>
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        st.divider()

        # === INTEGRATIONS STATUS ===
        st.markdown("### 🔌 Integrations")

        # Integration status
        github_configured = bool(settings.github_token)
        jira_configured = bool(settings.jira_api_token)
        slack_configured = bool(settings.slack_webhook_url)

        github_class = "status-enabled" if github_configured else "status-offline"
        github_text = "Configured" if github_configured else "Not Set"
        github_icon = "✅" if github_configured else "❌"

        jira_class = "status-enabled" if jira_configured else "status-offline"
        jira_text = "Configured" if jira_configured else "Not Set"
        jira_icon = "✅" if jira_configured else "❌"

        slack_text = "Configured" if slack_configured else "Optional"
        slack_icon = "✅" if slack_configured else "⚪"
        slack_class = "status-enabled" if slack_configured else ""

        st.markdown(
            f"""
        <div style="background-color: #f8f9fa; padding: 12px; border-radius: 8px; margin-bottom: 10px;">
            <div class="status-row">
                <span class="status-label">🔀 GitHub</span>
                <span class="status-value {github_class}">{github_icon} {github_text}</span>
            </div>
            <div class="status-row">
                <span class="status-label">🎫 Jira</span>
                <span class="status-value {jira_class}">{jira_icon} {jira_text}</span>
            </div>
            <div class="status-row">
                <span class="status-label">🔔 Slack</span>
                <span class="status-value {slack_class}">{slack_icon} {slack_text}</span>
            </div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        # Configuration tips
        if not github_configured or not jira_configured:
            st.info("💡 Configure missing integrations in your `.env` file to enable full workflow automation")

        st.divider()

        # === DATA FILTERS ===
        st.markdown("### 🔍 Data Filters")

        # Severity filter with better layout
        st.markdown("**Severity Levels**")
        filter_severity = st.multiselect(
            "Select severity levels",
            options=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
            default=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
            help="Filter incidents by severity level",
            label_visibility="collapsed",
        )

        # Status filter - Get actual status values from database
        st.markdown("**Incident Status**")

        # Get unique status values from incidents
        unique_statuses = sorted(list(set([i.status for i in all_incidents if i.status])))

        # If no incidents, use enum as fallback
        if not unique_statuses:
            unique_statuses = [status.value for status in IncidentStatus]

        filter_status = st.multiselect(
            "Select status",
            options=unique_statuses,
            default=unique_statuses,
            help="Filter by workflow processing status",
            label_visibility="collapsed",
        )

        # Application filter
        st.markdown("**Application**")
        filter_app = st.text_input(
            "Filter by application",
            "",
            placeholder="e.g., payment-api",
            help="Search by application name",
            label_visibility="collapsed",
        )

        # Filter summary
        filtered_count = len(
            [
                i
                for i in all_incidents
                if (not filter_severity or (i.severity and i.severity in filter_severity))
                and (not filter_status or i.status in filter_status)
                and (not filter_app or filter_app.lower() in i.app_name.lower())
            ]
        )

        if filtered_count != len(all_incidents):
            st.caption(f"📊 Showing {filtered_count} of {len(all_incidents)} incidents")

        st.divider()

        # === DANGER ZONE ===
        with st.expander("⚠️ Danger Zone", expanded=False):
            st.warning("⚠️ **Warning:** This action is permanent and cannot be undone!")
            if st.button("🗑️ Clear All Incidents", use_container_width=True, type="primary"):
                try:
                    from sqlalchemy import text

                    db = next(get_db())
                    repo = IncidentRepository(db)
                    db.execute(text("DELETE FROM incidents"))
                    db.commit()
                    st.success("✅ All incidents cleared successfully!")
                    st.session_state.incidents_refresh_key += 1
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Failed to clear incidents: {str(e)}")

    # Check if there are any incidents actively being processed
    active_incidents = [
        i
        for i in all_incidents
        if i.status in ["NEW", "ANALYZING", "FIX_PROPOSED"] and getattr(i, "workflow_progress_pct", 0.0) < 1.0
    ]

    # Auto-refresh if there are active incidents (but NOT for pending approval).
    # Uses streamlit-autorefresh if available; otherwise shows an indicator only.
    has_pending_approval = any(
        (
            (i.status or "").upper() == "PENDING_APPROVAL"
            or (i.status or "").lower() == "pending_approval"
            or getattr(i, "current_workflow_node", "") == "await_approval"
            or getattr(i, "approval_status", "") == "pending"
        )
        for i in all_incidents
    )

    if active_incidents:
        if st_autorefresh and not has_pending_approval:
            st_autorefresh(interval=AUTO_REFRESH_INTERVAL * 1000, key="auto_refresh_processing")
        st.info(
            f"🔄 Auto-refresh enabled: {len(active_incidents)} incident(s) processing... (refreshes every {AUTO_REFRESH_INTERVAL}s)"
        )

    # Main content tabs
    tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "📋 Incidents", "⚙️ Settings"])

    with tab1:
        # Dashboard metrics

        # Calculate metrics
        total_incidents = len(all_incidents)
        critical_count = len([i for i in all_incidents if i.severity == "CRITICAL"])
        high_count = len([i for i in all_incidents if i.severity == "HIGH"])
        pending_approval = len([i for i in all_incidents if i.status == "PENDING_APPROVAL"])
        fixes_created = len([i for i in all_incidents if i.pr_url is not None])

        # Display metrics
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Incidents", total_incidents)
        col2.metric("Critical", critical_count, delta=None, delta_color="inverse")
        col3.metric("High", high_count, delta=None, delta_color="inverse")
        col4.metric("Pending Approval", pending_approval)
        col5.metric("PRs Created", fixes_created)

        st.divider()

        # Recent incidents
        st.subheader("🚨 Recent Incidents")

        if not all_incidents:
            st.info(
                "No incidents yet. Generate test incidents using the MuleSoft error generator services or the OTLP ingestion endpoint."
            )
        else:
            for incident in all_incidents[:10]:  # Show last 10
                # Build integration status badges
                integration_badges = []

                # Add occurrence count badge if > 1
                occurrence_count = getattr(incident, "occurrence_count", 1)
                if occurrence_count > 1:
                    integration_badges.append(f"🔄 {occurrence_count}x Occurred")

                if incident.jira_ticket_url:
                    integration_badges.append("🎫 Jira")
                if incident.pr_url:
                    integration_badges.append("🔀 GitHub PR")
                if incident.pdf_path and os.path.exists(incident.pdf_path):
                    integration_badges.append("📄 PDF")

                badge_text = " | ".join(integration_badges) if integration_badges else "No integrations"

                with st.expander(
                    f"#{incident.incident_id} - {incident.error_title} ({incident.severity or 'UNKNOWN'}) - {badge_text}",
                    expanded=False,
                ):
                    # Integration Links Section (Prominent)
                    if incident.jira_ticket_url or incident.pr_url:
                        st.markdown("### 🔗 Integration Links")
                        link_col1, link_col2, link_col3 = st.columns(3)

                        with link_col1:
                            if incident.jira_ticket_url:
                                st.markdown(
                                    f"""
                                <a href="{incident.jira_ticket_url}" target="_blank" style="
                                    display: inline-block;
                                    padding: 10px 20px;
                                    background-color: #0052CC;
                                    color: white;
                                    text-decoration: none;
                                    border-radius: 5px;
                                    font-weight: bold;
                                ">🎫 Open Jira Ticket</a>
                                """,
                                    unsafe_allow_html=True,
                                )

                        with link_col2:
                            if incident.pr_url:
                                st.markdown(
                                    f"""
                                <a href="{incident.pr_url}" target="_blank" style="
                                    display: inline-block;
                                    padding: 10px 20px;
                                    background-color: #238636;
                                    color: white;
                                    text-decoration: none;
                                    border-radius: 5px;
                                    font-weight: bold;
                                ">🔀 View GitHub PR</a>
                                """,
                                    unsafe_allow_html=True,
                                )

                        with link_col3:
                            if incident.pdf_path and os.path.exists(incident.pdf_path):
                                with open(incident.pdf_path, "rb") as f:
                                    pdf_data = f.read()
                                    st.download_button(
                                        "📄 Download RCA Report",
                                        pdf_data,
                                        file_name=os.path.basename(incident.pdf_path),
                                        mime="application/pdf",
                                        key=f"pdf_download_{incident.incident_id}",
                                        use_container_width=True,
                                    )

                        st.divider()

                    col1, col2 = st.columns([2, 1])

                    with col1:
                        st.markdown(f"**Application:** {incident.app_name}")
                        st.markdown(f"**Environment:** {incident.environment}")
                        st.markdown(f"**Status:** {incident.status}")
                        st.markdown(f"**Created:** {incident.created_at}")

                        if incident.error_description:
                            st.markdown("**Description:**")
                            st.text(incident.error_description[:300])

                    with col2:
                        # Additional actions
                        st.markdown("**Actions:**")

                        if incident.patch_path and os.path.exists(incident.patch_path):
                            with open(incident.patch_path, "r", encoding="utf-8") as f:
                                patch_data = f.read()
                                st.download_button(
                                    "📦 Download Patch",
                                    patch_data,
                                    file_name=os.path.basename(incident.patch_path),
                                    mime="text/plain",
                                    key=f"patch_download_{incident.incident_id}",
                                    use_container_width=True,
                                )

    with tab2:
        st.subheader("📋 All Incidents")

        # Apply filters
        filtered_incidents = [
            i
            for i in all_incidents
            if (not filter_severity or (i.severity and i.severity in filter_severity))
            and (not filter_status or i.status in filter_status)
            and (not filter_app or filter_app.lower() in i.app_name.lower())
        ]

        if not filtered_incidents:
            st.info("No incidents match the filters.")
        else:
            for incident in filtered_incidents:
                with st.container():
                    col1, col2, col3, col4, col5 = st.columns([3, 1, 1, 1, 2])

                    with col1:
                        st.markdown(f"**#{incident.incident_id}** - {incident.error_title}")
                        st.caption(f"{incident.app_name} | {incident.environment}")

                    with col2:
                        if incident.severity:
                            st.markdown(
                                f"<span class='{get_severity_color(incident.severity)}'>{incident.severity}</span>",
                                unsafe_allow_html=True,
                            )

                    with col3:
                        st.text(incident.status)

                    with col4:
                        # Integration indicators
                        indicators = []
                        if incident.jira_ticket_url:
                            indicators.append("🎫")
                        if incident.pr_url:
                            indicators.append("🔀")
                        if incident.pdf_path:
                            indicators.append("📄")
                        st.text(" ".join(indicators) if indicators else "—")

                    with col5:
                        # Approval section - check both uppercase and lowercase
                        # Show approval buttons if status indicates pending approval OR if in await_approval node
                        is_pending_approval = (
                            incident.status.upper() == "PENDING_APPROVAL"
                            or incident.status.lower() == "pending_approval"
                            or getattr(incident, "current_workflow_node", "") == "await_approval"
                            or getattr(incident, "approval_status", "") == "pending"
                        )

                        # Show buttons if pending approval (even if proposed_fix is missing - it might be in RCA)
                        if is_pending_approval:
                            col_a, col_b = st.columns(2)
                            with col_a:
                                if st.button(
                                    "✅ Approve",
                                    key=f"approve_{incident.incident_id}",
                                    use_container_width=True,
                                ):
                                    if approve_fix(incident.incident_id, True):
                                        st.success("Approved!")
                                        st.session_state.incidents_refresh_key += 1
                                        st.rerun()
                            with col_b:
                                if st.button(
                                    "❌ Reject",
                                    key=f"reject_{incident.incident_id}",
                                    use_container_width=True,
                                ):
                                    if approve_fix(incident.incident_id, False):
                                        st.warning("Rejected!")
                                        st.session_state.incidents_refresh_key += 1
                                        st.rerun()

                    # Expandable details
                    with st.expander("View Details"):
                        # Workflow Progress Visualization
                        render_workflow_progress(incident)

                        st.divider()

                        # Integration Status Section (NEW)
                        st.markdown("### 🔌 Integration Status")
                        int_col1, int_col2, int_col3 = st.columns(3)

                        with int_col1:
                            st.markdown("**🎫 Jira Ticket**")
                            jira_error = getattr(incident, "jira_error", None)
                            is_pending_approval = incident.status == "PENDING_APPROVAL"

                            if incident.jira_ticket_url:
                                jira_key = getattr(incident, "jira_ticket_key", "N/A")
                                st.success(f"✅ Created: {jira_key}")
                                st.markdown(f"[Open in Jira →]({incident.jira_ticket_url})")
                            elif jira_error:
                                st.error("❌ Failed to create")
                                st.caption(f"⚠️ {jira_error}")
                            elif is_pending_approval:
                                # Jira ticket creation pending until approval decision
                                st.info("⏳ Pending approval")
                            else:
                                st.info("⏳ Not created yet")

                        with int_col2:
                            st.markdown("**🔔 Slack Notification**")
                            slack_sent = getattr(incident, "slack_notification_sent", None)
                            notif_errors = getattr(incident, "notification_errors", [])

                            # Check if incident is still in workflow before notifications
                            is_pending_approval = incident.status == "PENDING_APPROVAL"

                            if slack_sent is True:
                                st.success("✅ Sent successfully")
                            elif slack_sent is False and notif_errors:
                                # Only show failed if there are actual errors
                                st.warning("❌ Failed to send")
                                for err in notif_errors:
                                    st.caption(f"⚠️ {err}")
                            elif is_pending_approval:
                                # Notification pending until approval decision
                                st.info("⏳ Pending approval")
                            elif slack_sent is None:
                                # Not sent yet or not configured
                                st.info("⏳ Not configured")
                            else:
                                st.info("⏳ Pending")

                        with int_col3:
                            st.markdown("**🔀 GitHub PR**")
                            if incident.pr_url:
                                pr_num = getattr(incident, "pr_number", "N/A")
                                st.success(f"✅ Created: #{pr_num}")
                                st.markdown(f"[View PR →]({incident.pr_url})")
                            else:
                                st.info("⏳ Not created yet")

                        st.divider()

                        # Quick Links (Prominent at top)
                        if (
                            incident.jira_ticket_url
                            or incident.pr_url
                            or (incident.pdf_path and os.path.exists(incident.pdf_path))
                            or (incident.patch_path and os.path.exists(incident.patch_path))
                        ):
                            st.markdown("### 🔗 Quick Links")
                            link_cols = st.columns(4)

                            with link_cols[0]:
                                if incident.jira_ticket_url:
                                    st.markdown(
                                        f"""
                                    <a href="{incident.jira_ticket_url}" target="_blank" style="
                                        display: inline-block;
                                        padding: 10px 20px;
                                        background-color: #0052CC;
                                        color: white;
                                        text-decoration: none;
                                        border-radius: 5px;
                                        font-weight: bold;
                                        text-align: center;
                                        width: 100%;
                                    ">🎫 Jira Ticket</a>
                                    """,
                                        unsafe_allow_html=True,
                                    )

                            with link_cols[1]:
                                if incident.pr_url:
                                    st.markdown(
                                        f"""
                                    <a href="{incident.pr_url}" target="_blank" style="
                                        display: inline-block;
                                        padding: 10px 20px;
                                        background-color: #238636;
                                        color: white;
                                        text-decoration: none;
                                        border-radius: 5px;
                                        font-weight: bold;
                                        text-align: center;
                                        width: 100%;
                                    ">🔀 GitHub PR</a>
                                    """,
                                        unsafe_allow_html=True,
                                    )

                            with link_cols[2]:
                                if incident.pdf_path and os.path.exists(incident.pdf_path):
                                    with open(incident.pdf_path, "rb") as f:
                                        st.download_button(
                                            "📄 RCA Report",
                                            f.read(),
                                            file_name=os.path.basename(incident.pdf_path),
                                            mime="application/pdf",
                                            key=f"pdf_det_{incident.incident_id}",
                                            use_container_width=True,
                                            type="primary",
                                        )

                            with link_cols[3]:
                                if incident.patch_path and os.path.exists(incident.patch_path):
                                    with open(incident.patch_path, "r", encoding="utf-8") as f:
                                        st.download_button(
                                            "📦 Code Patch",
                                            f.read(),
                                            file_name=os.path.basename(incident.patch_path),
                                            mime="text/plain",
                                            key=f"patch_det_{incident.incident_id}",
                                            use_container_width=True,
                                            type="primary",
                                        )

                            st.divider()

                        st.markdown("**Error Description:**")
                        st.text(incident.error_description)

                        st.markdown("**Stack Trace:**")
                        st.code(incident.stack_trace, language="text")

                        if incident.rca_text:
                            st.markdown("**Root Cause Analysis:**")
                            st.markdown(incident.rca_text)
                            if incident.rca_confidence:
                                st.progress(incident.rca_confidence, text=f"Confidence: {incident.rca_confidence:.1%}")

                        if incident.proposed_fix:
                            st.markdown("**Proposed Fix:**")
                            _render_diff_view(incident.proposed_fix)
                            if incident.fix_explanation:
                                st.markdown("**Fix Explanation:**")
                                st.markdown(incident.fix_explanation)

                    st.divider()

    with tab3:
        st.subheader("⚙️ System Settings")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### LLM Configuration")
            st.text(f"Provider: {settings.llm_provider}")
            st.text(f"Model: {settings.llm_model}")
            st.text(f"Temperature: {settings.llm_temperature}")
            st.text(f"Max Tokens: {settings.llm_max_tokens}")

            st.markdown("### Storage")
            st.text(f"Database: {settings.database_path}")
            st.text(f"PDF Output: {settings.pdf_output_dir}")
            st.text(f"Patch Output: {settings.patch_output_dir}")

        with col2:
            st.markdown("### Auto-Fix Configuration")
            st.text(f"Enabled: {settings.auto_fix_enabled}")
            st.text(f"Severity Threshold: {settings.auto_fix_severity_threshold}")
            st.text(f"Requires Approval: {settings.auto_fix_requires_approval}")
            st.text(f"Auto PR: {settings.auto_pr_create}")

            st.markdown("### Integrations")
            st.text(f"GitHub: {'✅' if settings.github_token else '❌'}")
            st.text(f"Jira: {'✅' if settings.jira_api_token else '❌'}")
            st.text(f"Slack: {'✅' if settings.slack_webhook_url else '❌'}")

        st.divider()

        st.markdown("### Database Statistics")
        try:
            db_size = os.path.getsize(settings.database_path) / 1024  # KB
            st.metric("Database Size", f"{db_size:.2f} KB")
        except:
            st.text("Database not initialized")

        if st.button("🗑️ Clear All Data (Dangerous!)", use_container_width=True):
            st.warning("This will delete all incidents from the database!")


if __name__ == "__main__":
    main()
