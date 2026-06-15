"""FastAPI dashboard server for Prism — Prism UI."""
from __future__ import annotations

import asyncio
import difflib
import html as html_module
import json
import logging
import mimetypes
import os
import re
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, List, Optional

import requests
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from integrations.llm_provider import LLMProvider

# ── Path bootstrap ──────────────────────────────────────────────────────────
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_settings
from storage.customer_store import (
    add_environment,
    add_team_member,
    create_customer,
    delete_customer,
    get_customer,
    list_customers,
    remove_environment,
    remove_team_member,
    update_customer,
    update_integration,
    update_team_member,
)
from storage.database import get_db, get_session as get_db_session, init_database
from storage.incident_repository import IncidentRepository
from storage.models import ApprovalRequest, IncidentResponse, IncidentStatus, Severity
from storage.telemetry_repository import TelemetryLogRepository

try:
    from storage.auth_store import (
        authenticate_user,
        create_session,
        delete_session,
        get_session,
        is_first_run,
        create_project,
        update_project,
        delete_project,
        create_team,
        list_projects,
        get_project,
        get_project_config,
        update_project_config,
        clear_project_config,
        list_users,
        create_user,
        update_user,
        delete_user,
        get_user_by_id,
        assign_user_to_project,
        remove_user_from_project,
        get_user_projects,
        get_project_users,
        set_user_features,
        ALL_FEATURES,
    )
    _AUTH_AVAILABLE = True
except Exception as _auth_err:
    _AUTH_AVAILABLE = False
    ALL_FEATURES = []

    def _auth_unavailable(*args, **kwargs):
        raise RuntimeError("Authentication store is not available")

    authenticate_user = _auth_unavailable
    create_session = _auth_unavailable
    delete_session = _auth_unavailable
    get_session = _auth_unavailable
    is_first_run = _auth_unavailable
    create_project = _auth_unavailable
    update_project = _auth_unavailable
    delete_project = _auth_unavailable
    create_team = _auth_unavailable
    list_projects = lambda: []
    get_project = _auth_unavailable
    get_project_config = _auth_unavailable
    update_project_config = _auth_unavailable
    clear_project_config = _auth_unavailable
    list_users = lambda: []
    create_user = _auth_unavailable
    update_user = _auth_unavailable
    delete_user = _auth_unavailable
    get_user_by_id = _auth_unavailable
    assign_user_to_project = _auth_unavailable
    remove_user_from_project = _auth_unavailable
    get_user_projects = lambda user_id="": []
    get_project_users = lambda project_id="": []
    set_user_features = _auth_unavailable

# ── Logging ─────────────────────────────────────────────────────────────────

class _PaddedLevelFormatter(logging.Formatter):
    """Format log records so the level prefix is padded to match uvicorn's
    alignment style: ``INFO:     name:message`` (levelname+colon padded to
    9 chars, matching uvicorn's ``" " * (8 - len(levelname))`` separator)."""

    def format(self, record: logging.LogRecord) -> str:
        spaces = " " * (8 - len(record.levelname))
        record.levelprefix = f"{record.levelname}:{spaces}"
        return super().format(record)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        _PaddedLevelFormatter("%(levelprefix)s %(name)s:%(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(handler)
    else:
        for h in root.handlers:
            h.setFormatter(_PaddedLevelFormatter("%(levelprefix)s %(name)s:%(message)s"))


_configure_logging()
logger = logging.getLogger(__name__)

# ── App & static mounts ─────────────────────────────────────────────────────
app = FastAPI(title="Prism UI", version="1.0.0")

_HERE = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(_HERE, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))

settings = get_settings()

# Initialize SQLite schema once for the UI process instead of on each request path.
init_database()

# ── SSE broadcast helpers ────────────────────────────────────────────────────
_sse_subscribers: list[asyncio.Queue] = []


async def _broadcast(event: str, data: dict) -> None:
    """Push an SSE event to all connected clients."""
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(_sse_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


# ── Jinja2 template filters / globals ───────────────────────────────────────
def _severity_badge_class(severity: Optional[str]) -> str:
    mapping = {
        "CRITICAL": "badge-critical",
        "HIGH": "badge-high",
        "MEDIUM": "badge-medium",
        "LOW": "badge-low",
    }
    return mapping.get((severity or "").upper(), "badge-low")


def _status_badge_class(status: Optional[str]) -> str:
    mapping = {
        "COMPLETED": "badge-success",
        "PR_CREATED": "badge-success",
        "APPROVED": "badge-success",
        "PENDING_APPROVAL": "badge-warning",
        "ANALYZING": "badge-info",
        "DETECTED": "badge-info",
        "RCA_COMPLETE": "badge-info",
        "FIX_GENERATED": "badge-info",
        "PATCH_GENERATING": "badge-info",
        "CREATING_JIRA_PR": "badge-info",
        "REJECTED": "badge-error",
        "FAILED": "badge-error",
    }
    return mapping.get((status or "").upper(), "badge-info")


def _fmt_dt(dt) -> str:
    if dt is None:
        return "—"
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d %H:%M")


def _extract_two_code_blocks(text: str) -> Optional[tuple]:
    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)\n```", text or "", re.DOTALL)
    if len(code_blocks) < 2:
        return None
    return code_blocks[0].strip(), code_blocks[1].strip()


def _build_diff_rows(proposed_fix: str) -> Optional[list]:
    """Return list of (old_ln, new_ln, sign, text) for diff rendering."""
    blocks = _extract_two_code_blocks(proposed_fix)
    if not blocks:
        return None
    original, fixed = blocks
    a_lines = original.splitlines()
    b_lines = fixed.splitlines()
    sm = difflib.SequenceMatcher(a=a_lines, b=b_lines)
    rows = []
    a_ln = b_ln = 1
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            a_ln += i2 - i1
            b_ln += j2 - j1
            continue
        if tag in ("replace", "delete"):
            for k in range(i1, i2):
                rows.append((a_ln, None, "-", html_module.escape(a_lines[k])))
                a_ln += 1
        if tag in ("replace", "insert"):
            for k in range(j1, j2):
                rows.append((None, b_ln, "+", html_module.escape(b_lines[k])))
                b_ln += 1
    return rows or None


def _safe_generated_file(path_value: Optional[str], allowed_suffixes: tuple[str, ...]) -> Optional[Path]:
    if not path_value:
        return None
    try:
        candidate = Path(path_value).expanduser().resolve()
    except Exception:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    if candidate.suffix.lower() not in allowed_suffixes:
        return None
    return candidate


def _build_artifact_info(path_value: Optional[str], kind: str, incident_id: str) -> Optional[dict]:
    suffixes = {
        "pdf": (".pdf",),
        "patch": (".patch", ".diff", ".txt", ".md"),
    }[kind]
    artifact_path = _safe_generated_file(path_value, suffixes)
    if not artifact_path:
        return None

    route_base = f"/incidents/{incident_id}/artifacts/{kind}"
    return {
        "kind": kind,
        "name": artifact_path.name,
        "path": str(artifact_path),
        "size_bytes": artifact_path.stat().st_size,
        "preview_url": route_base if kind == "pdf" else None,
        "download_url": f"{route_base}?download=1",
        "exists": True,
    }


def _github_repo_url(repo_full_name: Optional[str]) -> Optional[str]:
    if not repo_full_name:
        return None
    return f"https://github.com/{repo_full_name}"


def _github_branch_url(repo_full_name: Optional[str], branch_name: Optional[str]) -> Optional[str]:
    if not repo_full_name or not branch_name:
        return None
    return f"https://github.com/{repo_full_name}/tree/{branch_name}"


def _github_file_url(repo_full_name: Optional[str], file_path: Optional[str], line_number: Optional[int] = None) -> Optional[str]:
    if not repo_full_name or not file_path:
        return None
    url = f"https://github.com/{repo_full_name}/blob/main/{file_path.lstrip('/')}"
    if line_number:
        url += f"#L{line_number}"
    return url


def _build_incident_links(inc: Any, pdf_artifact: Optional[dict], patch_artifact: Optional[dict]) -> list[dict]:
    links: list[dict] = []

    def _add(label: str, url: Optional[str], kind: str) -> None:
        if url:
            links.append({"label": label, "url": url, "kind": kind})

    _add("GitHub Repository", _github_repo_url(getattr(inc, "repo_full_name", None)), "github")
    _add("Error Source", _github_file_url(getattr(inc, "repo_full_name", None), getattr(inc, "error_file_path", None), getattr(inc, "error_line_number", None)), "github")
    _add("Fix Branch", _github_branch_url(getattr(inc, "repo_full_name", None), getattr(inc, "fix_branch", None)), "github")
    _add("Commit", getattr(inc, "commit_url", None), "github")
    _add("Pull Request", getattr(inc, "pr_url", None), "github")
    _add("Jira Ticket", getattr(inc, "jira_ticket_url", None), "jira")
    if pdf_artifact:
        _add("RCA PDF", pdf_artifact["download_url"], "artifact")
    if patch_artifact:
        _add("Patch File", patch_artifact["download_url"], "artifact")
    return links


def _build_incident_timeline(inc: Any, pdf_artifact: Optional[dict], patch_artifact: Optional[dict]) -> list[dict]:
    timeline: list[dict] = []

    def _push(source: str, title: str, details: str, status: str = "success", timestamp: Any = None, url: Optional[str] = None, preview: Optional[str] = None) -> None:
        timeline.append(
            {
                "source": source,
                "title": title,
                "details": details,
                "status": status,
                "timestamp": _fmt_dt(timestamp),
                "url": url,
                "preview": preview,
            }
        )

    created_at = getattr(inc, "created_at", None)
    updated_at = getattr(inc, "updated_at", None)

    # ── 1. Incident detected ────────────────────────────────────────────────
    _push(
        "workflow",
        "Incident detected",
        getattr(inc, "error_title", None) or "Incident created from telemetry/event stream",
        "success",
        created_at,
    )

    # ── 2. Workflow steps in canonical execution order ──────────────────────
    # Canonical order matches the workflow progress tracker shown in the UI.
    _canonical_step_order = [
        "assess_severity",
        "generate_rca",
        "generate_fix",
        "generate_pdf",
        "reflect",
        "generate_patch",
        "await_approval",
        "send_notifications",
        "create_jira",
        "create_pr",
        "finalize",
    ]
    _canonical_labels = {
        "assess_severity": "Assess Severity",
        "generate_rca": "Generate Rca",
        "generate_fix": "Generate Fix",
        "generate_pdf": "Generate Pdf",
        "reflect": "Reflect",
        "generate_patch": "Generate Patch",
        "await_approval": "Await Approval",
        "send_notifications": "Send Notifications",
        "create_jira": "Create Jira",
        "create_pr": "Create Pr",
        "finalize": "Finalize",
    }
    _completed_steps_set = set(list(getattr(inc, "workflow_completed_steps", None) or []))
    # Push only steps that are in both the canonical list and the completed set,
    # preserving canonical workflow order so the audit trail always reads top-to-bottom
    # in the same sequence as the Workflow Progress tracker.
    for _step_id in _canonical_step_order:
        if _step_id in _completed_steps_set:
            _label = _canonical_labels.get(_step_id, _step_id.replace("_", " ").title())
            _push(
                "workflow",
                _label,
                f"Workflow step `{_step_id}` completed.",
                "success",
                updated_at,
            )

    # ── 3. Approval decision ────────────────────────────────────────────────
    _inc_appr_status = getattr(inc, "approval_status", None) or ""
    _inc_status_val = getattr(inc, "status", None) or ""
    _post_approval = _inc_appr_status == "approved" or _inc_status_val in ("APPROVED", "COMPLETED", "PR_CREATED")

    if _inc_appr_status == "pending":
        _push("workflow", "Awaiting approval", "Patch file is already available for testing. Human approval is required before Jira creation, PR creation, and notifications.", "warning", updated_at)
    elif _inc_appr_status == "approved":
        _push("workflow", "Fix approved", getattr(inc, "approval_notes", None) or "Fix approved for downstream execution.", "success", getattr(inc, "approved_at", None))
    elif _inc_appr_status == "rejected":
        _push("workflow", "Fix rejected", getattr(inc, "approval_notes", None) or "Fix rejected during human review.", "error", getattr(inc, "approved_at", None))

    # ── 4. Notifications (send_notifications step) ─────────────────────────
    if getattr(inc, "slack_notification_sent", False):
        slack_preview = (
            f"Incident {getattr(inc, 'incident_id', '')}: {getattr(inc, 'error_title', '')}\n"
            f"Severity {getattr(inc, 'severity', 'UNKNOWN')} · {getattr(inc, 'status', 'UNKNOWN').replace('_', ' ')}"
        )
        _push("slack", "Slack notification sent", "Slack delivery completed.", "success", updated_at, preview=slack_preview)
    elif _post_approval:
        _push("slack", "Slack notification pending", "Slack alert will be sent as part of the post-approval workflow.", "warning", updated_at)

    if getattr(inc, "teams_notification_sent", False):
        teams_preview = (
            f"Incident {getattr(inc, 'incident_id', '')}\n"
            f"{getattr(inc, 'app_name', '')} / {getattr(inc, 'environment', '')}\n"
            f"{getattr(inc, 'error_title', '')}"
        )
        _push("teams", "Teams notification sent", "Microsoft Teams delivery completed.", "success", updated_at, preview=teams_preview)

    for error in list(getattr(inc, "notification_errors", None) or []):
        _push("notification", "Notification issue", str(error), "error", updated_at)

    # ── 5. Jira (create_jira step) ──────────────────────────────────────────
    if getattr(inc, "jira_ticket_key", None) or getattr(inc, "jira_ticket_url", None):
        _push(
            "jira",
            "Jira ticket created",
            str(getattr(inc, "jira_ticket_key", None) or "Jira issue available for tracking"),
            "success",
            updated_at,
            getattr(inc, "jira_ticket_url", None),
        )
    elif getattr(inc, "jira_error", None):
        _push("jira", "Jira creation failed", str(getattr(inc, "jira_error", None) or ""), "error", updated_at)
    elif _post_approval:
        _push("jira", "Jira ticket pending", "Jira ticket will be created as part of the post-approval workflow.", "warning", updated_at)

    # ── 6. GitHub PR (create_pr step) ──────────────────────────────────────
    repo_full_name = getattr(inc, "repo_full_name", None)
    if getattr(inc, "fix_branch", None):
        _push(
            "github",
            "Fix branch created",
            str(getattr(inc, "fix_branch", None) or ""),
            "success",
            updated_at,
            _github_branch_url(repo_full_name, getattr(inc, "fix_branch", None)),
        )

    if getattr(inc, "commit_sha", None):
        _push(
            "github",
            "Commit created",
            str(getattr(inc, "commit_sha", None) or ""),
            "success",
            updated_at,
            getattr(inc, "commit_url", None),
        )

    if getattr(inc, "pr_url", None):
        _push(
            "github",
            "Pull request created",
            f"PR #{getattr(inc, 'pr_number', '—')} is available for review.",
            "success",
            updated_at,
            getattr(inc, "pr_url", None),
        )
    elif getattr(inc, "status", None) == "PR_CREATED":
        _push("github", "Pull request status updated", "PR workflow step completed.", "success", updated_at)
    elif "create_pr" in (getattr(inc, "current_workflow_node", None) or "") and _inc_status_val.upper() == "JIRA_CREATED":
        _push("github", "Pull request creation failed", "PR could not be created — check fixed_file_content, repo_full_name, and error_file_path in the workflow state.", "error", updated_at)
    elif "create_pr" in (getattr(inc, "current_workflow_node", None) or ""):
        _push("github", "Pull request in progress", "PR creation is currently in progress.", "warning", updated_at)
    elif _post_approval:
        _push("github", "GitHub PR pending", "Pull request will be created as part of the post-approval workflow.", "warning", updated_at)

    # ── 7. Artifacts ────────────────────────────────────────────────────────
    if pdf_artifact:
        _push("artifact", "RCA report generated", f"{pdf_artifact['name']} is available for preview and download.", "success", updated_at, pdf_artifact["download_url"])
    else:
        _push("artifact", "RCA report pending", "PDF output is not yet available.", "warning", updated_at)

    if patch_artifact:
        _push("artifact", "Patch generated", f"{patch_artifact['name']} is ready for download.", "success", updated_at, patch_artifact["download_url"])
    else:
        _push("artifact", "Patch pending", "Patch file has not been generated yet.", "warning", updated_at)

    # No sort — insertion order matches canonical workflow execution order.
    return timeline


def _workflow_improvements(inc: Any, pdf_artifact: Optional[dict], patch_artifact: Optional[dict]) -> list[str]:
    suggestions: list[str] = []

    if getattr(inc, "rca_text", None) and not pdf_artifact:
        suggestions.append("Persist RCA PDF generation status with timestamp and error details when the report is unavailable.")
    if getattr(inc, "proposed_fix", None) and not patch_artifact:
        suggestions.append("Generate the patch immediately after fix creation or show the exact blocker preventing patch output.")
    if getattr(inc, "jira_ticket_url", None) and not getattr(inc, "pr_url", None):
        suggestions.append("Surface why PR creation has not happened after Jira creation to reduce operator ambiguity.")
    if list(getattr(inc, "notification_errors", None) or []):
        suggestions.append("Expose notification destinations and retry state so failed Slack/Teams deliveries can be remediated quickly.")
    if not getattr(inc, "fetched_code", None):
        suggestions.append("Persist fetched GitHub code context for RCA transparency and easier auditability.")
    if not suggestions:
        suggestions.append("Workflow looks healthy; the next improvement would be adding timestamps for each workflow node transition.")
    return suggestions


templates.env.filters["severity_badge"] = _severity_badge_class
templates.env.filters["status_badge"] = _status_badge_class
templates.env.filters["fmt_dt"] = _fmt_dt
templates.env.globals["build_diff_rows"] = _build_diff_rows
templates.env.globals["settings"] = settings
# Stub out Flask-style get_flashed_messages so templates don't error
templates.env.globals["get_flashed_messages"] = lambda with_categories=False: []


# ── DB dependency ────────────────────────────────────────────────────────────
def get_repository(db: Session = Depends(get_db)) -> IncidentRepository:
    return IncidentRepository(db)


# ── Helper: serialize incident to dict ───────────────────────────────────────
def _incident_to_dict(inc) -> dict:
    d = {}
    for col in inc.__table__.columns:
        val = getattr(inc, col.name, None)
        if isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, (list, dict)):
            pass
        d[col.name] = val
    # computed extras
    d["severity_badge"] = _severity_badge_class(d.get("severity"))
    d["status_badge"] = _status_badge_class(d.get("status"))
    return d


# ── Dashboard stats helper ────────────────────────────────────────────────────
def _compute_stats(incidents: list) -> dict:
    total = len(incidents)
    by_severity = {}
    for inc in incidents:
        s = (inc.severity or "UNKNOWN").upper()
        by_severity[s] = by_severity.get(s, 0) + 1

    by_status = {}
    for inc in incidents:
        st = (inc.status or "UNKNOWN").upper()
        by_status[st] = by_status.get(st, 0) + 1

    pending_approval = sum(
        1 for i in incidents
        if (i.status or "").upper() == "PENDING_APPROVAL"
        or getattr(i, "approval_status", "") == "pending"
    )
    prs_created = sum(1 for i in incidents if i.pr_url)
    jira_created = sum(1 for i in incidents if i.jira_ticket_url)
    completed = by_status.get("COMPLETED", 0) + by_status.get("PR_CREATED", 0)

    return {
        "total": total,
        "critical": by_severity.get("CRITICAL", 0),
        "high": by_severity.get("HIGH", 0),
        "medium": by_severity.get("MEDIUM", 0),
        "low": by_severity.get("LOW", 0),
        "pending_approval": pending_approval,
        "prs_created": prs_created,
        "jira_created": jira_created,
        "completed": completed,
        "by_severity": by_severity,
        "by_status": by_status,
    }


def _get_accessible_projects_for_user(user: Optional[dict]) -> list:
    if not (_AUTH_AVAILABLE and user):
        return []
    user_id = user.get("user_id") or user.get("id") or ""
    if not user_id:
        return []
    return get_user_projects(user_id)


def _get_selected_project(request: Request, user: Optional[dict]) -> Optional[dict]:
    my_projects = _get_accessible_projects_with_fallback(user)
    if not my_projects:
        return None
    active_project_id = request.cookies.get("active_project", "")
    selected_project = next((p for p in my_projects if p.get("id") == active_project_id), None)
    return selected_project or my_projects[0]


def _get_active_project_id(request: Request, user: Optional[dict]) -> str:
    selected_project = _get_selected_project(request, user)
    return str((selected_project or {}).get("id") or "")


def _get_user_id(user: Optional[dict]) -> str:
    if not user:
        return ""
    return str(user.get("user_id") or user.get("id") or "")


def _get_accessible_projects_with_fallback(user: Optional[dict]) -> list:
    if not (_AUTH_AVAILABLE and user):
        return []

    user_id = _get_user_id(user)
    role = str(user.get("role") or "").strip().lower()

    projects = get_user_projects(user_id) if user_id else []
    if projects or role == "admin":
        return projects

    username = str(user.get("username") or "").strip().lower()
    email = str(user.get("email") or "").strip().lower()

    fallback_matches = []
    for project in list_projects():
        team_admin_id = str(project.get("team_admin_id") or "").strip()
        team_admin_email = str(project.get("team_admin_email") or "").strip().lower()

        if user_id and team_admin_id == user_id:
            fallback_matches.append(project)
            continue

        if email and team_admin_email and email == team_admin_email:
            fallback_matches.append(project)
            continue

        if username and team_admin_email and username == team_admin_email.split("@")[0].lower():
            fallback_matches.append(project)

    return fallback_matches


def _normalize_project_token(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _load_app_repo_mapping() -> dict:
    try:
        import yaml

        with open("config/app_repo_mapping.yaml", "r", encoding="utf-8") as mapping_file:
            mapping_config = yaml.safe_load(mapping_file) or {}
        return mapping_config.get("app_mappings") or {}
    except Exception as exc:
        logger.debug("Could not load app repo mapping: %s", exc)
        return {}


def _project_candidate_tokens(project: Optional[dict], incident_app_name: Optional[str] = None) -> set[str]:
    if not project:
        return set()

    project_repo_url = (project.get("repo_url") or "").strip()
    project_app_names = project.get("app_names") or []
    project_id = (project.get("id") or "").strip()

    candidate_tokens: set[str] = set()

    if project_repo_url:
        tok = _normalize_project_token(project_repo_url)
        if tok:
            candidate_tokens.add(tok)
        repo_leaf = _normalize_project_token(project_repo_url.rstrip("/").split("/")[-1])
        if repo_leaf:
            candidate_tokens.add(repo_leaf)

    for app_alias in project_app_names:
        normalized_alias = _normalize_project_token(app_alias)
        if normalized_alias:
            candidate_tokens.add(normalized_alias)

    if project_id and _AUTH_AVAILABLE:
        try:
            from storage.auth_store import get_app_repo_mapping as _get_db_mapping
            db_mappings = _get_db_mapping(project_id) or {}
            for mapped_app, mapping_val in db_mappings.items():
                app_tok = _normalize_project_token(mapped_app)
                if app_tok:
                    candidate_tokens.add(app_tok)
                mapped_repo = (mapping_val.get("repo") or "") if isinstance(mapping_val, dict) else ""
                if mapped_repo:
                    mapped_repo_token = _normalize_project_token(mapped_repo)
                    if mapped_repo_token:
                        candidate_tokens.add(mapped_repo_token)
                    leaf = _normalize_project_token(mapped_repo.split("/")[-1])
                    if leaf:
                        candidate_tokens.add(leaf)
        except Exception:
            pass

    if incident_app_name:
        mapped_repo = (_load_app_repo_mapping().get(incident_app_name) or {}).get("repo") or ""
        if mapped_repo:
            mapped_repo_token = _normalize_project_token(mapped_repo)
            if mapped_repo_token:
                candidate_tokens.add(mapped_repo_token)
            mapped_repo_leaf = _normalize_project_token(mapped_repo.split("/")[-1])
            if mapped_repo_leaf:
                candidate_tokens.add(mapped_repo_leaf)

    return {token for token in candidate_tokens if token}


def _incident_matches_project(incident: Any, project: Optional[dict]) -> bool:
    if not project:
        return True

    incident_app_name = (getattr(incident, "app_name", None) or "").strip()
    normalized_incident_app = _normalize_project_token(incident_app_name)
    candidate_tokens = _project_candidate_tokens(project, incident_app_name)

    if candidate_tokens:
        return (
            not normalized_incident_app
            or normalized_incident_app in candidate_tokens
            or any(
                normalized_incident_app in candidate_token or candidate_token in normalized_incident_app
                for candidate_token in candidate_tokens
            )
        )

    project_id = (project.get("id") or "").strip()
    if project_id and _AUTH_AVAILABLE:
        try:
            from agents.workflow import _resolve_project_id_for_incident

            resolved_project_id = _resolve_project_id_for_incident(
                None,
                incident_app_name,
                getattr(incident, "environment", None),
            )
            if resolved_project_id:
                return resolved_project_id == project_id
        except Exception:
            pass

    return True


def _configured_integrations(project_config: Optional[dict]) -> dict:
    config = project_config or {}

    def _section_is_configured(section_name: str) -> bool:
        section = config.get(section_name) or {}
        if not isinstance(section, dict):
            return bool(section)
        for field_name, field_value in section.items():
            if field_name.endswith("_configured"):
                if field_value:
                    return True
                continue
            if field_value not in (None, "", [], {}, False):
                return True
        return False

    return {
        key: _section_is_configured(key)
        for key in ["llm", "jira", "github", "anypoint", "slack", "teams"]
    }


def _get_project_integration_status(project: Optional[dict]) -> dict:
    if not (_AUTH_AVAILABLE and project):
        return {}
    return _configured_integrations(get_project_config(project["id"]) or {})


def _get_project_integration_summary(project: Optional[dict]) -> list[dict]:
    if not (_AUTH_AVAILABLE and project):
        return []

    project_config = get_project_config(project["id"], mask_secrets=True) or {}
    enabled_map = _configured_integrations(project_config)
    secret_map = _masked_secret_status(project_config)

    labels = {
        "llm": "LLM",
        "jira": "Jira",
        "github": "GitHub",
        "anypoint": "Anypoint",
        "slack": "Slack",
        "teams": "Teams",
    }

    llm_config = project_config.get("llm") or {}
    llm_model = (
        llm_config.get("model")
        or llm_config.get("model_name")
        or llm_config.get("deployment_name")
        or llm_config.get("selected_model")
        or ""
    )
    llm_provider = llm_config.get("provider") or llm_config.get("vendor") or ""

    integrations: list[dict] = []
    for key, label in labels.items():
        configured = enabled_map.get(key, False)
        live = configured and secret_map.get(key, False)

        meta = ""
        if key == "llm":
            meta_parts = [part for part in [llm_model, llm_provider] if part]
            meta = " · ".join(meta_parts[:2])

        integrations.append(
            {
                "key": key,
                "label": label,
                "configured": configured,
                "live": live,
                "status_label": "Connected" if live else "Configured",
                "badge_class": "badge-success" if live else "badge-warning",
                "status_tone": "is-live" if live else "is-configured",
                "meta": meta,
            }
        )

    return [item for item in integrations if item["configured"]]


def _masked_secret_status(project_config: Optional[dict]) -> dict:
    config = project_config or {}
    statuses: dict[str, bool] = {}
    for section, key in {
        "llm": "api_key_configured",
        "jira": "api_token_configured",
        "github": "token_configured",
        "anypoint": "client_secret_configured",
        "slack": "webhook_url_configured",
        "teams": "webhook_url_configured",
    }.items():
        statuses[section] = bool((config.get(section) or {}).get(key))
    return statuses


def _build_demo_readiness(project: Optional[dict]) -> dict:
    project_config = get_project_config(project["id"], mask_secrets=True) if (_AUTH_AVAILABLE and project) else {}
    integration_enabled = _configured_integrations(project_config)
    integration_secrets = _masked_secret_status(project_config)

    try:
        db_path = Path(settings.database_path).expanduser().resolve()
        database_ready = db_path.exists()
    except Exception:
        database_ready = False

    ingestion_online = False
    try:
        response = requests.get(
            f"http://localhost:{settings.ingestion_api_port}/health",
            timeout=2,
        )
        ingestion_online = response.status_code == 200
    except Exception:
        ingestion_online = False

    runtime_config = (project_config or {}).get("runtime", {}) if project_config else {}
    has_runtime_config = all(
        runtime_config.get(key)
        for key in ("otlp_collector_port", "database_path", "pdf_output_dir", "patch_output_dir")
    )

    checks = [
        {
            "label": "Database file available",
            "ok": database_ready,
            "detail": str(Path(settings.database_path).expanduser()),
        },
        {
            "label": "Ingestion API reachable",
            "ok": ingestion_online,
            "detail": f"http://localhost:{settings.ingestion_api_port}/health",
        },
        {
            "label": "Project selected",
            "ok": project is not None,
            "detail": (project or {}).get("name", "No active project"),
        },
        {
            "label": "Runtime configuration saved",
            "ok": has_runtime_config,
            "detail": "Project runtime paths and OTLP port",
        },
    ]

    for section, label in [
        ("llm", "LLM"),
        ("jira", "Jira"),
        ("github", "GitHub"),
        ("anypoint", "Anypoint"),
        ("slack", "Slack"),
        ("teams", "Teams"),
    ]:
        enabled = integration_enabled.get(section, False)
        secret_ready = integration_secrets.get(section, False)
        ok = (not enabled) or secret_ready
        detail = "Configured" if secret_ready else ("Enabled in project config but secret missing" if enabled else "Optional / not configured")
        checks.append({"label": f"{label} credentials", "ok": ok, "detail": detail})

    passed = sum(1 for item in checks if item["ok"])
    return {
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "is_ready": passed == len(checks),
        "integration_enabled": integration_enabled,
        "integration_secrets": integration_secrets,
    }


def _incident_rca_text(incident: Any) -> Optional[str]:
    return getattr(incident, "rca_text", None) or getattr(incident, "root_cause_analysis", None)


def _load_live_observability_data(selected_project: Optional[dict]) -> dict:
    project_name = (selected_project or {}).get("name")
    project_env = (selected_project or {}).get("environment")

    with get_db_session() as session:
        telemetry_repo = TelemetryLogRepository(session)
        incident_repo = IncidentRepository(session)

        telemetry_logs = telemetry_repo.get_all(limit=500)
        incidents = incident_repo.get_all(limit=500)

    if project_name:
        telemetry_logs = [log for log in telemetry_logs if getattr(log, "app_name", None) == project_name]
        incidents = [inc for inc in incidents if getattr(inc, "app_name", None) == project_name]
    if project_env:
        telemetry_logs = [log for log in telemetry_logs if getattr(log, "environment", None) == project_env]
        incidents = [inc for inc in incidents if getattr(inc, "environment", None) == project_env]

    envs = sorted(
        {
            env for env in (
                [getattr(log, "environment", None) for log in telemetry_logs]
                + [getattr(inc, "environment", None) for inc in incidents]
            ) if env
        }
    )

    trace_groups: dict[tuple[str, str, str], list[Any]] = {}
    for log in telemetry_logs:
        key = (
            getattr(log, "trace_id", None) or getattr(log, "log_id", ""),
            getattr(log, "app_name", None) or "unknown-app",
            getattr(log, "environment", None) or "unknown",
        )
        trace_groups.setdefault(key, []).append(log)

    traces: list[dict] = []
    for (correlation_id, app_name, environment), logs in trace_groups.items():
        severities = {(getattr(item, "severity", "") or "").upper() for item in logs}
        status = "error" if {"CRITICAL", "HIGH"} & severities else "healthy"
        if status == "healthy" and len(logs) >= 3:
            status = "slow"
        latency_ms = 150 + (len(logs) * 120)
        traces.append(
            {
                "app": app_name,
                "api_name": app_name,
                "flow": getattr(logs[0], "flow_name", None) or getattr(logs[0], "logger_name", None) or "unknown-flow",
                "environment": environment,
                "status": status,
                "latency_ms": latency_ms,
                "span_count": len(logs),
                "correlation_id": correlation_id,
            }
        )
    traces.sort(key=lambda item: (0 if item["status"] == "error" else 1, -item["latency_ms"], item["app"]))

    latency_values = [item["latency_ms"] for item in traces]
    trace_summary = {
        "total_traces": len(traces),
        "error_traces": sum(1 for item in traces if item["status"] == "error"),
        "slow_traces": sum(1 for item in traces if item["status"] == "slow"),
        "median_latency_ms": int(statistics.median(latency_values)) if latency_values else 0,
        "environments": envs,
    }

    flow_counter = Counter(
        getattr(log, "flow_name", None) or getattr(log, "logger_name", None) or "unknown-flow"
        for log in telemetry_logs
        if (getattr(log, "severity", "") or "").upper() in {"CRITICAL", "HIGH"}
    )
    failed_spans = [
        {"name": name, "count": count, "system": "OpenTelemetry"}
        for name, count in flow_counter.most_common(5)
    ]

    app_groups: dict[tuple[str, str], list[Any]] = {}
    for log in telemetry_logs:
        key = (
            getattr(log, "app_name", None) or "unknown-app",
            getattr(log, "environment", None) or "unknown",
        )
        app_groups.setdefault(key, []).append(log)

    metrics_rows: list[dict] = []
    for (app_name, environment), logs in app_groups.items():
        total = len(logs)
        error_count = sum(1 for log in logs if (getattr(log, "severity", "") or "").upper() in {"CRITICAL", "HIGH"})
        medium_count = sum(1 for log in logs if (getattr(log, "severity", "") or "").upper() == "MEDIUM")
        requests_per_min = total
        error_rate = round((error_count / total) * 100, 1) if total else 0.0
        p95_latency_ms = 200 + error_count * 180 + medium_count * 60
        cpu = min(95, 35 + total * 3 + error_count * 4)
        memory = min(95, 30 + total * 2 + medium_count * 3)
        metrics_rows.append(
            {
                "app": app_name,
                "api_name": app_name,
                "environment": environment,
                "requests_per_min": requests_per_min,
                "error_rate": error_rate,
                "p95_latency_ms": p95_latency_ms,
                "cpu": cpu,
                "memory": memory,
            }
        )
    metrics_rows.sort(key=lambda item: (-item["error_rate"], -item["requests_per_min"], item["app"]))

    metrics_summary = {
        "requests_per_min": sum(item["requests_per_min"] for item in metrics_rows),
        "error_rate": round(
            sum(item["error_rate"] for item in metrics_rows) / len(metrics_rows), 1
        ) if metrics_rows else 0.0,
        "p95_latency_ms": max((item["p95_latency_ms"] for item in metrics_rows), default=0),
        "worker_cpu": round(
            sum(item["cpu"] for item in metrics_rows) / len(metrics_rows)
        ) if metrics_rows else 0,
        "environments": envs,
    }
    top_error_apps = metrics_rows[:3]

    analytics_rows = [
        {
            "api_name": item["api_name"],
            "app": item["app"],
            "environment": item["environment"],
            "requests": item["requests_per_min"] * 60,
            "success_rate": round(max(0.0, 100.0 - item["error_rate"]), 1),
            "p95_latency_ms": item["p95_latency_ms"],
            "policy_hits": sum(
                1
                for log in app_groups[(item["app"], item["environment"])]
                if getattr(log, "incident_created", False)
            ),
            "consumer_count": len(
                {
                    getattr(log, "flow_name", None) or getattr(log, "logger_name", None) or getattr(log, "log_id", None)
                    for log in app_groups[(item["app"], item["environment"])]
                }
            ),
        }
        for item in metrics_rows
    ]

    analytics_summary = {
        "managed_apis": len(analytics_rows),
        "policy_violations": sum(item["policy_hits"] for item in analytics_rows),
        "server_errors": sum(
            1
            for log in telemetry_logs
            if (getattr(log, "severity", "") or "").upper() in {"CRITICAL", "HIGH"}
        ),
        "avg_latency_ms": round(
            sum(item["p95_latency_ms"] for item in analytics_rows) / len(analytics_rows)
        ) if analytics_rows else 0,
        "environments": envs,
    }
    policy_hotspots = sorted(analytics_rows, key=lambda item: item["policy_hits"], reverse=True)[:3]

    audit_rows: list[dict] = []
    for incident in incidents[:20]:
        outcome = "failure" if (getattr(incident, "status", "") or "").upper() == "FAILED" else "success"
        audit_rows.append(
            {
                "timestamp": _fmt_dt(getattr(incident, "updated_at", None) or getattr(incident, "created_at", None)),
                "source": "Incident Workflow",
                "app": getattr(incident, "app_name", None) or "unknown-app",
                "environment": getattr(incident, "environment", None) or "unknown",
                "event": getattr(incident, "status", "UNKNOWN").replace("_", " ").title(),
                "details": getattr(incident, "error_title", "Incident update"),
                "outcome": outcome,
            }
        )

    for log in telemetry_logs[:20]:
        audit_rows.append(
            {
                "timestamp": _fmt_dt(getattr(log, "timestamp", None)),
                "source": "OpenTelemetry",
                "app": getattr(log, "app_name", None) or "unknown-app",
                "environment": getattr(log, "environment", None) or "unknown",
                "event": "Telemetry log received",
                "details": getattr(log, "message", "")[:120],
                "outcome": "warning" if getattr(log, "incident_created", False) else "success",
            }
        )

    audit_rows.sort(key=lambda item: item["timestamp"], reverse=True)
    audit_rows = audit_rows[:25]
    audit_summary = {
        "total_events": len(audit_rows),
        "failures": sum(1 for item in audit_rows if item["outcome"] == "failure"),
        "warnings": sum(1 for item in audit_rows if item["outcome"] == "warning"),
        "sources": sorted({item["source"] for item in audit_rows}),
    }
    failed_integrations = [item for item in audit_rows if item["outcome"] == "failure"][:3]

    return {
        "telemetry_logs": telemetry_logs,
        "incidents": incidents,
        "trace_summary": trace_summary,
        "traces": traces,
        "failed_spans": failed_spans,
        "metrics_summary": metrics_summary,
        "metrics_rows": metrics_rows,
        "top_error_apps": top_error_apps,
        "analytics_summary": analytics_summary,
        "analytics_rows": analytics_rows,
        "policy_hotspots": policy_hotspots,
        "audit_summary": audit_summary,
        "audit_rows": audit_rows,
        "failed_integrations": failed_integrations,
    }


def _sample_trace_rows(selected_project: Optional[dict]) -> list[dict]:
    project_name = (selected_project or {}).get("name", "Mule Order APIs")
    environment = (selected_project or {}).get("environment", "prod")
    return [
        {
            "app": project_name,
            "api_name": "orders-experience-api",
            "flow": "orders-main-flow",
            "environment": environment,
            "status": "error",
            "latency_ms": 2840,
            "span_count": 18,
            "correlation_id": "corr-8fd21a0",
        },
        {
            "app": project_name,
            "api_name": "inventory-process-api",
            "flow": "inventory-reconcile-flow",
            "environment": "qa",
            "status": "slow",
            "latency_ms": 1680,
            "span_count": 12,
            "correlation_id": "corr-0fa91bd",
        },
        {
            "app": project_name,
            "api_name": "customer-system-api",
            "flow": "customer-lookup-flow",
            "environment": environment,
            "status": "healthy",
            "latency_ms": 320,
            "span_count": 9,
            "correlation_id": "corr-44cc992",
        },
        {
            "app": "billing-process-api",
            "api_name": "billing-process-api",
            "flow": "invoice-dispatch-flow",
            "environment": "sandbox",
            "status": "error",
            "latency_ms": 1950,
            "span_count": 16,
            "correlation_id": "corr-1d93ac7",
        },
    ]


def _sample_metrics_rows(selected_project: Optional[dict]) -> list[dict]:
    project_name = (selected_project or {}).get("name", "Mule Order APIs")
    environment = (selected_project or {}).get("environment", "prod")
    return [
        {
            "app": project_name,
            "api_name": "orders-experience-api",
            "environment": environment,
            "requests_per_min": 1280,
            "error_rate": 2.8,
            "p95_latency_ms": 640,
            "cpu": 71,
            "memory": 68,
        },
        {
            "app": "inventory-process-api",
            "api_name": "inventory-process-api",
            "environment": "qa",
            "requests_per_min": 820,
            "error_rate": 1.2,
            "p95_latency_ms": 590,
            "cpu": 62,
            "memory": 58,
        },
        {
            "app": "customer-system-api",
            "api_name": "customer-system-api",
            "environment": environment,
            "requests_per_min": 1540,
            "error_rate": 0.6,
            "p95_latency_ms": 410,
            "cpu": 55,
            "memory": 52,
        },
        {
            "app": "billing-process-api",
            "api_name": "billing-process-api",
            "environment": "sandbox",
            "requests_per_min": 430,
            "error_rate": 4.4,
            "p95_latency_ms": 980,
            "cpu": 77,
            "memory": 73,
        },
    ]


def _sample_api_analytics_rows(selected_project: Optional[dict]) -> list[dict]:
    project_name = (selected_project or {}).get("name", "Mule Order APIs")
    environment = (selected_project or {}).get("environment", "prod")
    return [
        {
            "api_name": "Orders Experience API",
            "app": project_name,
            "environment": environment,
            "requests": 48210,
            "success_rate": 98.7,
            "p95_latency_ms": 420,
            "policy_hits": 124,
            "consumer_count": 18,
        },
        {
            "api_name": "Inventory Process API",
            "app": "inventory-process-api",
            "environment": "qa",
            "requests": 21440,
            "success_rate": 97.9,
            "p95_latency_ms": 510,
            "policy_hits": 88,
            "consumer_count": 9,
        },
        {
            "api_name": "Customer System API",
            "app": "customer-system-api",
            "environment": environment,
            "requests": 52990,
            "success_rate": 99.2,
            "p95_latency_ms": 315,
            "policy_hits": 61,
            "consumer_count": 22,
        },
    ]


def _sample_audit_rows(selected_project: Optional[dict]) -> list[dict]:
    project_name = (selected_project or {}).get("name", "Mule Order APIs")
    environment = (selected_project or {}).get("environment", "prod")
    return [
        {
            "timestamp": "2026-05-22 12:10",
            "source": "Jira",
            "app": project_name,
            "environment": environment,
            "event": "Incident ticket created",
            "details": "OPS-1842 created for order sync failure",
            "outcome": "success",
        },
        {
            "timestamp": "2026-05-22 12:22",
            "source": "GitHub",
            "app": project_name,
            "environment": environment,
            "event": "PR creation failed",
            "details": "Branch protection prevented auto-create",
            "outcome": "failure",
        },
        {
            "timestamp": "2026-05-22 12:35",
            "source": "Slack",
            "app": "billing-process-api",
            "environment": "sandbox",
            "event": "Alert delivered",
            "details": "#mule-ops notified for connector timeout",
            "outcome": "success",
        },
        {
            "timestamp": "2026-05-22 12:42",
            "source": "MuleSoft",
            "app": project_name,
            "environment": environment,
            "event": "Application sync warning",
            "details": "CloudHub worker metadata partially unavailable",
            "outcome": "warning",
        },
        {
            "timestamp": "2026-05-22 12:48",
            "source": "Teams",
            "app": "inventory-process-api",
            "environment": "qa",
            "event": "Notification failed",
            "details": "Webhook returned HTTP 410",
            "outcome": "failure",
        },
    ]


# ════════════════════════════════════════════════════════════════════════════
# HTML PAGE ROUTES
# ════════════════════════════════════════════════════════════════════════════

# ── Auth helpers ─────────────────────────────────────────────────────────────

def _get_current_user(request: Request) -> Optional[dict]:
    if not _AUTH_AVAILABLE:
        return {"id": "USR-ADMIN", "username": "admin", "role": "admin", "features": []}
    token = request.cookies.get("session")
    if not token:
        return None
    return get_session(token)


def _require_auth(request: Request) -> Optional[RedirectResponse]:
    """Return a redirect if not authenticated, else None."""
    if not _AUTH_AVAILABLE:
        return None
    user = _get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return None


def _require_role(request: Request, *roles: str) -> Optional[RedirectResponse]:
    """Require the current user to have one of the given roles."""
    if not _AUTH_AVAILABLE:
        return None
    user = _get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.get("role") not in roles:
        return RedirectResponse(url="/dashboard", status_code=302)
    return None


def _role_home(role: str) -> str:
    """Return the landing URL for a given role."""
    if role == "admin":
        return "/admin/dashboard"
    if role == "team_admin":
        return "/team-admin/dashboard"
    return "/incidents"


# ════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _AUTH_AVAILABLE:
        user = _get_current_user(request)
        if user:
            role = (user or {}).get("role", "user")
            return RedirectResponse(url=_role_home(role), status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
):
    if not _AUTH_AVAILABLE:
        return RedirectResponse(url="/dashboard", status_code=302)
    user = authenticate_user(username, password)
    if not user:
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password"}
        )
    token = create_session(user)
    home = _role_home(user.get("role", "user"))
    resp = RedirectResponse(url=home, status_code=302)
    resp.set_cookie("session", token, httponly=True, max_age=86400 * 7)
    return resp


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    if token and _AUTH_AVAILABLE:
        delete_session(token)
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("session")
    return resp


# ════════════════════════════════════════════════════════════════════════════
# ONBOARDING ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request, step: int = 1):
    return templates.TemplateResponse(
        request,
        "onboarding.html",
        {"step": step, "error": None, "form": {}, "summary": {}},
    )


@app.post("/onboarding/step1", response_class=HTMLResponse)
async def onboarding_step1(
    request: Request,
    name: str = Form(...),
    description: str = Form(default=""),
    repo_url: str = Form(default=""),
    stack: str = Form(default=""),
    environment: str = Form(default="production"),
):
    if not name.strip():
        return templates.TemplateResponse(
            request,
            "onboarding.html",
            {
                "step": 1,
                "error": "Project name is required.",
                "form": {"name": name, "description": description, "repo_url": repo_url, "stack": stack, "environment": environment},
                "summary": {},
            },
        )
    if _AUTH_AVAILABLE:
        try:
            create_project({"name": name, "description": description, "repo_url": repo_url, "stack": stack, "environment": environment})
        except Exception as exc:
            logger.warning("onboarding create_project: %s", exc)

    # Store in session cookie so step2 knows the project name
    resp = RedirectResponse(url="/onboarding?step=2", status_code=302)
    resp.set_cookie("ob_project", name, httponly=True, max_age=3600)
    return resp


@app.post("/onboarding/step2", response_class=HTMLResponse)
async def onboarding_step2(
    request: Request,
    team_name: str = Form(...),
    team_description: str = Form(default=""),
):
    if not team_name.strip():
        return templates.TemplateResponse(
            request,
            "onboarding.html",
            {
                "step": 2,
                "error": "Team name is required.",
                "form": {"team_name": team_name, "team_description": team_description},
                "summary": {},
            },
        )

    form_data = await request.form()
    names = form_data.getlist("m_name[]")
    emails = form_data.getlist("m_email[]")
    roles = form_data.getlist("m_role[]")
    members = [
        {"name": n, "email": e, "role": r}
        for n, e, r in zip(names, emails, roles)
        if e.strip()
    ]

    if _AUTH_AVAILABLE:
        try:
            create_team({"name": team_name, "description": team_description, "members": members})
        except Exception as exc:
            logger.warning("onboarding create_team: %s", exc)

    project_name = request.cookies.get("ob_project", "")
    summary = {"project": project_name, "team": team_name, "members": len(members)}

    resp = templates.TemplateResponse(
        request,
        "onboarding.html",
        {"step": 3, "error": None, "form": {}, "summary": summary},
    )
    resp.delete_cookie("ob_project")
    return resp


# ════════════════════════════════════════════════════════════════════════════
# MAIN PAGE ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    role = (user or {}).get("role", "user")
    return RedirectResponse(url=_role_home(role), status_code=302)


# ── Admin Landing Dashboard ──────────────────────────────────────────────────

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    redir = _require_role(request, "admin")
    if redir:
        return redir
    user = _get_current_user(request)
    projects = list_projects() if _AUTH_AVAILABLE else []
    users = list_users() if _AUTH_AVAILABLE else []
    team_admins = [u for u in users if u.get("role") == "team_admin"]
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "current_user": user,
            "projects": projects,
            "team_admins": team_admins,
            "users": users,
            "page": "admin_dashboard",
        },
    )


# ── Team Admin Landing Dashboard ──────────────────────────────────────────────

@app.get("/team-admin/dashboard", response_class=HTMLResponse)
async def team_admin_dashboard(request: Request, project_id: str = Query(default="")):
    redir = _require_role(request, "team_admin")
    if redir:
        return redir
    user = _get_current_user(request)
    my_projects = _get_accessible_projects_with_fallback(user)
    active_project_id = project_id or request.cookies.get("active_project", "")
    selected_project = next((p for p in my_projects if p.get("id") == active_project_id), None)
    if not selected_project and my_projects:
        selected_project = my_projects[0]

    all_users = list_users() if _AUTH_AVAILABLE else []
    team_users = []
    if _AUTH_AVAILABLE:
        if selected_project:
            project_user_ids = {u.get("id") for u in get_project_users(selected_project["id"])}
            team_users = [u for u in all_users if u.get("role") == "user" and u.get("id") in project_user_ids]
        else:
            team_users = [u for u in all_users if u.get("role") == "user"]

    integration_config = get_project_config(selected_project["id"]) if (_AUTH_AVAILABLE and selected_project) else {}
    integration_status = _configured_integrations(integration_config)
    runtime_config = (integration_config or {}).get("runtime", {}) if integration_config else {}

    return templates.TemplateResponse(
        request,
        "team_admin_dashboard.html",
        {
            "current_user": user,
            "projects": my_projects,
            "selected_project": selected_project,
            "team_users": team_users,
            "integration_status": integration_status,
            "integrations_configured": sum(1 for configured in integration_status.values() if configured),
            "runtime_config": runtime_config,
            "settings_defaults": {
                "otlp_collector_port": settings.otlp_collector_port,
                "database_path": settings.database_path,
                "pdf_output_dir": settings.pdf_output_dir,
                "patch_output_dir": settings.patch_output_dir,
            },
            "page": "team_admin_dashboard",
        },
    )


# ── Regular User Dashboard ──────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    role = (user or {}).get("role", "user")
    if role == "admin":
        return RedirectResponse(url="/admin/dashboard", status_code=302)
    if role == "team_admin":
        return RedirectResponse(url="/team-admin/dashboard", status_code=302)
    return RedirectResponse(url="/incidents", status_code=302)


# ════════════════════════════════════════════════════════════════════════════
# ADMIN ONBOARDING — Project creation + Team Admin assignment
# ════════════════════════════════════════════════════════════════════════════

@app.get("/admin/onboarding", response_class=HTMLResponse)
async def admin_onboarding_page(request: Request):
    redir = _require_role(request, "admin")
    if redir:
        return redir
    user = _get_current_user(request)
    all_users = list_users() if _AUTH_AVAILABLE else []
    team_admins = [u for u in all_users if u.get("role") == "team_admin"]
    return templates.TemplateResponse(
        request,
        "admin_onboarding.html",
        {"current_user": user, "team_admins": team_admins, "error": None, "success": None, "page": "admin_onboarding"},
    )


@app.post("/admin/onboarding", response_class=HTMLResponse)
async def admin_onboarding_post(
    request: Request,
    project_name: str = Form(...),
    project_description: str = Form(default=""),
    repo_url: str = Form(default=""),
    stack: str = Form(default=""),
    environment: str = Form(default="production"),
    team_admin_email: str = Form(...),
    team_admin_name: str = Form(default=""),
):
    redir = _require_role(request, "admin")
    if redir:
        return redir
    user = _get_current_user(request)
    all_users = list_users() if _AUTH_AVAILABLE else []
    team_admins = [u for u in all_users if u.get("role") == "team_admin"]
    error = None
    success = None

    if not project_name.strip():
        error = "Project name is required."
    elif not team_admin_email.strip():
        error = "Team Admin email is required."
    else:
        try:
            # Find or create Team Admin user
            ta_user = next((u for u in all_users if u.get("email", "").lower() == team_admin_email.lower()), None)
            if not ta_user:
                import secrets as _secrets
                username = team_admin_email.split("@")[0].replace(".", "_")
                # Generate a strong random temporary password — admin must share it securely
                temp_password = _secrets.token_urlsafe(16)
                ta_user = create_user({
                    "username": username,
                    "email": team_admin_email,
                    "password": temp_password,
                    "role": "team_admin",
                })
                success = (
                    f"Project '{project_name}' created and assigned to {team_admin_email}. "
                    f"Temporary password (share securely): {temp_password}"
                )
            project = create_project({
                "name": project_name,
                "description": project_description,
                "repo_url": repo_url,
                "stack": stack,
                "environment": environment,
                "team_admin_id": ta_user["id"],
                "team_admin_email": team_admin_email,
            })
            assign_user_to_project(ta_user["id"], project["id"])
            success = f"Project '{project_name}' created and assigned to {team_admin_email}."
        except Exception as exc:
            error = f"Error: {exc}"

    return templates.TemplateResponse(
        request,
        "admin_onboarding.html",
        {"current_user": user, "team_admins": team_admins, "error": error, "success": success, "page": "admin_onboarding"},
    )


# ════════════════════════════════════════════════════════════════════════════
# TEAM ADMIN ONBOARDING — Integrations + User management
# ════════════════════════════════════════════════════════════════════════════

@app.get("/team-admin/onboarding", response_class=HTMLResponse)
async def team_admin_onboarding_page(request: Request, project_id: str = Query(default="")):
    redir = _require_role(request, "team_admin")
    if redir:
        return redir
    user = _get_current_user(request)
    uid = _get_user_id(user)
    my_projects = _get_accessible_projects_with_fallback(user)
    active_project_id = project_id or request.cookies.get("active_project", "")
    selected_project = get_project(active_project_id) if (_AUTH_AVAILABLE and active_project_id) else None
    if selected_project and my_projects and selected_project.get("id") not in [p.get("id") for p in my_projects]:
        selected_project = None
    if not selected_project and my_projects:
        selected_project = my_projects[0]

    config = get_project_config(selected_project["id"], mask_secrets=True) if (_AUTH_AVAILABLE and selected_project) else {}
    all_users = list_users() if _AUTH_AVAILABLE else []
    team_users = [u for u in all_users if u.get("role") == "user"]
    members = get_project_users(selected_project["id"]) if (selected_project and _AUTH_AVAILABLE) else []
    available_users = [u for u in team_users if not selected_project or u.get("id") not in {m.get("id") for m in members}]
    return templates.TemplateResponse(
        request,
        "team_admin_onboarding.html",
        {
            "current_user": user,
            "projects": my_projects,
            "selected_project": selected_project,
            "integration_config": config,
            "team_members": members,
            "available_users": available_users,
            "all_features": ALL_FEATURES if _AUTH_AVAILABLE else [],
            "error": None,
            "success": None,
            "page": "team_admin_onboarding",
        },
    )


@app.post("/team-admin/onboarding", response_class=HTMLResponse)
async def team_admin_onboarding_post(request: Request):
    redir = _require_role(request, "team_admin")
    if redir:
        return redir
    user = _get_current_user(request)
    form = await request.form()
    project_id = str(form.get("project_id", "") or "")
    section = str(form.get("section", "") or "")
    values = {
        str(k): v
        for k, v in form.items()
        if k not in ("project_id", "section")
    }
    error = None
    success = None

    if _AUTH_AVAILABLE and project_id and section:
        try:
            update_project_config(project_id, section, values)
            success = f"'{section}' configuration saved."
        except Exception as exc:
            error = f"Error: {exc}"

    uid = _get_user_id(user)
    my_projects = _get_accessible_projects_with_fallback(user)
    selected_project = get_project(project_id) if (_AUTH_AVAILABLE and project_id) else None
    config = get_project_config(project_id) if (_AUTH_AVAILABLE and project_id) else {}
    all_users = list_users() if _AUTH_AVAILABLE else []
    if not selected_project and my_projects:
        selected_project = my_projects[0]
    members = get_project_users(selected_project["id"]) if (selected_project and _AUTH_AVAILABLE) else []
    return templates.TemplateResponse(
        request,
        "team_admin_onboarding.html",
        {
            "current_user": user,
            "projects": my_projects,
            "selected_project": selected_project,
            "integration_config": config,
            "team_members": members,
            "all_features": ALL_FEATURES if _AUTH_AVAILABLE else [],
            "error": error,
            "success": success,
            "page": "team_admin_onboarding",
        },
    )


# ════════════════════════════════════════════════════════════════════════════
# TEAM ADMIN — Member & Integration API (used by team_admin_onboarding.html JS)
# ════════════════════════════════════════════════════════════════════════════

@app.post("/api/team-admin/add-member", status_code=201)
async def api_team_admin_add_member(request: Request):
    """Add a new user to the team and assign them to projects with feature access."""
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    username = (body.get("username") or "").strip()
    email = (body.get("email") or "").strip()
    password = (body.get("password") or "").strip()
    full_name = (body.get("full_name") or "").strip()
    features = body.get("feature_access") or []
    project_ids = body.get("project_ids") or []

    if not username or not email or not password:
        return JSONResponse({"error": "username, email and password are required"}, status_code=400)

    try:
        all_users = list_users()
        existing = next((u for u in all_users if u.get("email", "").lower() == email.lower()), None)
        if existing:
            new_user = existing
        else:
            new_user = create_user({
                "username": username,
                "email": email,
                "password": password,
                "role": "user",
                "full_name": full_name,
            })
        for pid in project_ids:
            if pid:
                assign_user_to_project(new_user["id"], pid)
        if features:
            set_user_features(new_user["id"], features)
        return JSONResponse({"success": True, "user": new_user}, status_code=201)
    except Exception as exc:
        logger.error("add-member error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/team-admin/integration/{int_type}")
async def api_team_admin_save_integration(int_type: str, request: Request):
    """Save integration config for a project (LLM, Jira, GitHub, Anypoint, Slack, Teams)."""
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    # Determine project_id from the current user's first project if not supplied
    project_id = (body.get("project_id") or "").strip()
    if not project_id:
        user = _get_current_user(request)
        project_id = _get_active_project_id(request, user)

    if not project_id:
        return JSONResponse({"error": "No project found for this user"}, status_code=400)

    try:
        config_data = {k: v for k, v in body.items() if k != "project_id"}
        update_project_config(project_id, int_type, config_data)
        return JSONResponse({"success": True, "type": int_type, "project_id": project_id})
    except Exception as exc:
        logger.error("save-integration error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.delete("/api/team-admin/integration/{int_type}")
async def api_team_admin_delete_integration(int_type: str, request: Request):
    """Delete integration config for a project/category."""
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        body = {}

    project_id = (body.get("project_id") or "").strip()
    if not project_id:
        user = _get_current_user(request)
        project_id = _get_active_project_id(request, user)

    if not project_id:
        return JSONResponse({"error": "No project found for this user"}, status_code=400)

    try:
        clear_project_config(project_id, int_type)
        return JSONResponse({"success": True, "type": int_type, "project_id": project_id})
    except Exception as exc:
        logger.error("delete-integration error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/team-admin/repo-mappings")
async def api_get_repo_mappings(request: Request, project_id: str = Query(default="")):
    """Return the app→repo mappings for a project."""
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE or not project_id:
        return JSONResponse({})
    try:
        from storage.auth_store import get_app_repo_mapping
        return JSONResponse(get_app_repo_mapping(project_id))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/team-admin/repo-mappings")
async def api_save_repo_mappings(request: Request):
    """Save (overwrite) the app→repo mappings for a project."""
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    project_id = (body.get("project_id") or "").strip()
    if not project_id:
        user = _get_current_user(request)
        project_id = _get_active_project_id(request, user)
    if not project_id:
        return JSONResponse({"error": "No project found for this user"}, status_code=400)

    mappings = body.get("mappings") or {}
    if not isinstance(mappings, dict):
        return JSONResponse({"error": "mappings must be a JSON object"}, status_code=400)

    # Normalize: ensure every repo value is in "org/repo" format.
    # If a bare repo name is provided (no slash), auto-prepend the project's configured GitHub org.
    try:
        from storage.auth_store import get_project_config as _get_proj_cfg
        _proj_cfg = _get_proj_cfg(project_id) or {}
        _github_org = (_proj_cfg.get("github") or {}).get("org", "").strip()
    except Exception:
        _github_org = ""

    normalized_mappings: dict = {}
    for app_name, mapping_val in mappings.items():
        if not isinstance(mapping_val, dict):
            continue
        repo_val = (mapping_val.get("repo") or "").strip()
        if repo_val and "/" not in repo_val:
            if _github_org:
                repo_val = f"{_github_org}/{repo_val}"
                logger.info(
                    "Repo mapping normalized: '%s' → '%s' for app '%s'",
                    mapping_val.get("repo"), repo_val, app_name,
                )
            else:
                logger.warning(
                    "Repo mapping for app '%s' has no org prefix and no GitHub org configured: '%s'",
                    app_name, repo_val,
                )
        normalized_mappings[app_name] = {**mapping_val, "repo": repo_val}

    try:
        from storage.auth_store import set_app_repo_mapping
        set_app_repo_mapping(project_id, normalized_mappings)
        return JSONResponse({"success": True, "project_id": project_id, "count": len(normalized_mappings)})
    except Exception as exc:
        logger.error("save-repo-mappings error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/team-admin/runtime-config")
async def api_team_admin_save_runtime_config(request: Request):
    """Save project-scoped runtime configuration for the active project."""
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    project_id = (body.get("project_id") or "").strip()
    if not project_id:
        user = _get_current_user(request)
        project_id = _get_active_project_id(request, user)

    if not project_id:
        return JSONResponse({"error": "No project found for this user"}, status_code=400)

    description = str(body.get("description") or "").strip()
    otlp_collector_port = str(body.get("otlp_collector_port") or "").strip()
    database_path = str(body.get("database_path") or "").strip()
    pdf_output_dir = str(body.get("pdf_output_dir") or "").strip()
    patch_output_dir = str(body.get("patch_output_dir") or "").strip()

    if not otlp_collector_port:
        return JSONResponse({"error": "OpenTelemetry collector port is required."}, status_code=400)
    if not otlp_collector_port.isdigit():
        return JSONResponse({"error": "OpenTelemetry collector port must be numeric."}, status_code=400)

    otlp_port_value = int(otlp_collector_port)
    if otlp_port_value < 1 or otlp_port_value > 65535:
        return JSONResponse({"error": "OpenTelemetry collector port must be between 1 and 65535."}, status_code=400)

    if not database_path:
        return JSONResponse({"error": "Database path is required."}, status_code=400)
    if not pdf_output_dir:
        return JSONResponse({"error": "PDF output directory is required."}, status_code=400)
    if not patch_output_dir:
        return JSONResponse({"error": "Patch output directory is required."}, status_code=400)

    runtime_config = {
        "otlp_collector_port": otlp_port_value,
        "database_path": database_path,
        "pdf_output_dir": pdf_output_dir,
        "patch_output_dir": patch_output_dir,
    }

    try:
        update_project(project_id, {"description": description})
        update_project_config(project_id, "runtime", runtime_config)
        return JSONResponse({"success": True, "project_id": project_id, "runtime": runtime_config, "description": description})
    except Exception as exc:
        logger.error("save-runtime-config error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/team-admin/integration/{int_type}/test")
async def api_team_admin_test_integration(int_type: str, request: Request):
    """Test connectivity for a given integration type."""
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        body = {}

    _type_labels = {
        "llm": "LLM provider",
        "jira": "Jira",
        "github": "GitHub",
        "anypoint": "Anypoint Platform",
        "slack": "Slack",
        "teams": "Microsoft Teams",
    }

    if int_type == "llm":
        provider = (body.get("provider") or "").strip().lower()
        model = (body.get("model") or "").strip()
        api_key = (body.get("api_key") or "").strip()
        base_url = (body.get("base_url") or "").strip()
        temperature_raw = body.get("temperature")
        max_tokens_raw = body.get("max_tokens")

        if not provider:
            return JSONResponse({"success": False, "error": "LLM provider is required."}, status_code=400)
        if not model:
            return JSONResponse({"success": False, "error": "LLM model is required."}, status_code=400)

        local_providers = {"ollama"}
        if provider not in local_providers and not api_key:
            return JSONResponse({"success": False, "error": "API key is required for the selected LLM provider."}, status_code=400)
        if provider == "azure_openai" and not base_url:
            return JSONResponse({"success": False, "error": "Base URL is required for Azure OpenAI."}, status_code=400)

        try:
            temperature = float(temperature_raw) if temperature_raw not in (None, "") else None
        except (TypeError, ValueError):
            return JSONResponse({"success": False, "error": "Temperature must be a valid number."}, status_code=400)

        if temperature is not None and not (0 <= temperature <= 2):
            return JSONResponse({"success": False, "error": "Temperature must be between 0 and 2."}, status_code=400)

        try:
            max_tokens = int(max_tokens_raw) if max_tokens_raw not in (None, "") else None
        except (TypeError, ValueError):
            return JSONResponse({"success": False, "error": "Max tokens must be a valid integer."}, status_code=400)

        if max_tokens is not None and max_tokens < 1:
            return JSONResponse({"success": False, "error": "Max tokens must be greater than 0."}, status_code=400)

        try:
            api_key = api_key.replace("\r", "").replace("\n", "").strip()

            llm = LLMProvider(
                provider=provider,
                model=model,
                api_key=api_key or None,
                base_url=base_url or None,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            ok, message = llm.test_connection_fast(timeout_seconds=10.0)
            status = 200 if ok else 400

            if not ok:
                sanitized_message = message
                if api_key:
                    sanitized_message = sanitized_message.replace(api_key, "[REDACTED_API_KEY]")
                sanitized_message = re.sub(
                    r"(Incorrect API key provided:\s*)([^\s,}]+)",
                    r"\1[REDACTED_API_KEY]",
                    sanitized_message,
                    flags=re.IGNORECASE,
                )
                return JSONResponse(
                    {"success": False, "message": None, "error": sanitized_message},
                    status_code=status,
                )

            return JSONResponse({"success": True, "message": message, "error": None}, status_code=status)
        except Exception as exc:
            error_message = str(exc)
            if api_key:
                error_message = error_message.replace(api_key, "[REDACTED_API_KEY]")
            error_message = re.sub(
                r"(Incorrect API key provided:\s*)([^\s,}]+)",
                r"\1[REDACTED_API_KEY]",
                error_message,
                flags=re.IGNORECASE,
            )
            return JSONResponse({"success": False, "error": error_message}, status_code=400)

    if int_type == "jira":
        base_url = (body.get("base_url") or "").strip().rstrip("/")
        username = (body.get("username") or "").strip()
        api_token = (body.get("api_token") or "").strip()

        if not base_url:
            return JSONResponse({"success": False, "error": "Jira base URL is required."}, status_code=400)
        if not username:
            return JSONResponse({"success": False, "error": "Jira username is required."}, status_code=400)
        if not api_token:
            return JSONResponse({"success": False, "error": "Jira API token is required."}, status_code=400)

        try:
            response = requests.get(
                f"{base_url}/rest/api/2/myself",
                auth=(username, api_token),
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if response.status_code == 200:
                user_data = response.json()
                display_name = user_data.get("displayName") or user_data.get("name") or username
                return JSONResponse(
                    {"success": True, "message": f"Jira connection verified for {display_name}."},
                    status_code=200,
                )
            if response.status_code in (401, 403):
                return JSONResponse(
                    {"success": False, "error": "Jira authentication failed. Check URL, username, and API token."},
                    status_code=400,
                )
            return JSONResponse(
                {"success": False, "error": f"Jira verification failed with status {response.status_code}."},
                status_code=400,
            )
        except requests.RequestException as exc:
            return JSONResponse({"success": False, "error": f"Unable to reach Jira: {exc}"}, status_code=400)

    if int_type == "github":
        token = (body.get("token") or "").strip()
        org = (body.get("org") or "").strip()

        if not token:
            return JSONResponse({"success": False, "error": "GitHub token is required."}, status_code=400)

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            user_response = requests.get("https://api.github.com/user", headers=headers, timeout=10)
            if user_response.status_code == 200:
                user_data = user_response.json()
                login = user_data.get("login") or "GitHub user"

                if org:
                    org_response = requests.get(
                        f"https://api.github.com/orgs/{org}",
                        headers=headers,
                        timeout=10,
                    )
                    if org_response.status_code == 404:
                        owner_response = requests.get(
                            f"https://api.github.com/users/{org}",
                            headers=headers,
                            timeout=10,
                        )
                        if owner_response.status_code != 200:
                            return JSONResponse(
                                {"success": False, "error": f'GitHub owner/organization "{org}" was not found.'},
                                status_code=400,
                            )
                    elif org_response.status_code != 200:
                        return JSONResponse(
                            {"success": False, "error": f'Unable to validate GitHub owner/organization "{org}".'},
                            status_code=400,
                        )

                message = f"GitHub connection verified for {login}."
                if org:
                    message += f" Owner/organization '{org}' is reachable."
                return JSONResponse({"success": True, "message": message}, status_code=200)

            if user_response.status_code in (401, 403):
                return JSONResponse(
                    {"success": False, "error": "GitHub authentication failed. Check the personal access token."},
                    status_code=400,
                )

            return JSONResponse(
                {"success": False, "error": f"GitHub verification failed with status {user_response.status_code}."},
                status_code=400,
            )
        except requests.RequestException as exc:
            return JSONResponse({"success": False, "error": f"Unable to reach GitHub: {exc}"}, status_code=400)

    if int_type == "slack":
        webhook_url = (body.get("webhook_url") or "").strip()

        if not webhook_url:
            return JSONResponse({"success": False, "error": "Slack webhook URL is required."}, status_code=400)

        try:
            response = requests.post(
                webhook_url,
                json={"text": "Prism integration verification"},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if response.status_code == 200 and response.text.strip().lower() == "ok":
                return JSONResponse(
                    {"success": True, "message": "Slack webhook verified successfully."},
                    status_code=200,
                )
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Slack verification failed with status {response.status_code}: {response.text.strip() or 'Unknown response'}",
                },
                status_code=400,
            )
        except requests.RequestException as exc:
            return JSONResponse({"success": False, "error": f"Unable to reach Slack: {exc}"}, status_code=400)

    if int_type == "teams":
        webhook_url = (body.get("webhook_url") or "").strip()

        if not webhook_url:
            return JSONResponse({"success": False, "error": "Microsoft Teams webhook URL is required."}, status_code=400)

        try:
            response = requests.post(
                webhook_url,
                json={"text": "Prism integration verification"},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if 200 <= response.status_code < 300:
                return JSONResponse(
                    {"success": True, "message": "Microsoft Teams webhook verified successfully."},
                    status_code=200,
                )
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Microsoft Teams verification failed with status {response.status_code}: {response.text.strip() or 'Unknown response'}",
                },
                status_code=400,
            )
        except requests.RequestException as exc:
            return JSONResponse({"success": False, "error": f"Unable to reach Microsoft Teams: {exc}"}, status_code=400)

    if int_type == "anypoint":
        client_id = (body.get("client_id") or "").strip()
        client_secret = (body.get("client_secret") or "").strip()
        org_id = (body.get("org_id") or "").strip()

        if not client_id:
            return JSONResponse({"success": False, "error": "Anypoint client ID is required."}, status_code=400)
        if not client_secret:
            return JSONResponse({"success": False, "error": "Anypoint client secret is required."}, status_code=400)

        token_urls = [
            "https://anypoint.mulesoft.com/accounts/api/v2/oauth2/token",
            "https://anypoint.mulesoft.com/accounts/api/v2/oauth2/token/",
            "https://anypoint.mulesoft.com/accounts/login",
        ]

        token_response = None
        last_exception = None

        for token_url in token_urls:
            try:
                token_response = requests.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    timeout=10,
                )
                if token_response.status_code in (200, 201):
                    break
            except requests.RequestException as exc:
                last_exception = exc

        if token_response is None:
            return JSONResponse(
                {"success": False, "error": f"Unable to reach Anypoint Platform: {last_exception}"},
                status_code=400,
            )

        if token_response.status_code in (401, 403):
            detail = token_response.text.strip()
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Anypoint authentication failed. Check client ID and client secret.{(' Response: ' + detail) if detail else ''}",
                },
                status_code=400,
            )

        if token_response.status_code not in (200, 201):
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Anypoint token request failed with status {token_response.status_code}: {token_response.text.strip() or 'Unknown response'}",
                },
                status_code=400,
            )

        try:
            token_data = token_response.json()
        except ValueError:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Anypoint returned a non-JSON token response: {token_response.text.strip() or 'Empty response'}",
                },
                status_code=400,
            )

        access_token = token_data.get("access_token")
        if not access_token:
            return JSONResponse({"success": False, "error": "Anypoint did not return an access token."}, status_code=400)

        if org_id:
            try:
                org_response = requests.get(
                    f"https://anypoint.mulesoft.com/accounts/api/organizations/{org_id}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    timeout=10,
                )
            except requests.RequestException as exc:
                return JSONResponse({"success": False, "error": f"Unable to validate Anypoint organization: {exc}"}, status_code=400)

            if org_response.status_code == 404:
                return JSONResponse(
                    {"success": False, "error": f'Anypoint organization "{org_id}" was not found.'},
                    status_code=400,
                )
            if org_response.status_code not in (200, 204):
                return JSONResponse(
                    {
                        "success": False,
                        "error": f"Anypoint organization validation failed with status {org_response.status_code}: {org_response.text.strip() or 'Unknown response'}",
                    },
                    status_code=400,
                )

        message = "Anypoint Platform connection verified successfully."
        if org_id:
            message += f" Organization '{org_id}' is reachable."
        return JSONResponse({"success": True, "message": message}, status_code=200)

    label = _type_labels.get(int_type, int_type.upper())
    return JSONResponse({"success": True, "message": f"{label} connection test succeeded (stub)."})


@app.post("/api/team/members", status_code=201)
async def api_team_add_member(request: Request):
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    project_id = body.get("project_id", "")
    username = body.get("username", "").strip()
    email = body.get("email", "").strip()
    password = body.get("password", "")
    features = body.get("features", [])
    if not username or not email or not password:
        return JSONResponse({"error": "username, email and password are required"}, status_code=400)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        all_users = list_users()
        existing = next((u for u in all_users if u.get("email", "").lower() == email.lower()), None)
        if existing:
            new_user = existing
        else:
            new_user = create_user({"username": username, "email": email, "password": password, "role": "user"})
        assign_user_to_project(new_user["id"], project_id)
        if features:
            set_user_features(new_user["id"], features)
        return JSONResponse({"success": True, "user": new_user}, status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.delete("/api/team/members/{user_id}")
async def api_team_remove_member(user_id: str, request: Request):
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    project_id = body.get("project_id", "")
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        remove_user_from_project(user_id, project_id)
        return JSONResponse({"success": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.put("/api/team/members/{user_id}/features")
async def api_team_update_features(user_id: str, request: Request):
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    features = body.get("features", [])
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        updated = set_user_features(user_id, features)
        return JSONResponse({"success": True, "user": updated})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/team/integrations")
async def api_team_save_integration(request: Request):
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    project_id = body.get("project_id", "")
    int_type = body.get("type", "")
    if not project_id or not int_type:
        return JSONResponse({"error": "project_id and type required"}, status_code=400)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        config_data = {k: v for k, v in body.items() if k not in ("project_id", "type")}
        update_project_config(project_id, int_type, config_data)
        return JSONResponse({"success": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/team/environments")
async def api_team_get_environments(request: Request, project_id: str = Query(default="")):
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE or not project_id:
        return JSONResponse([])
    try:
        config = get_project_config(project_id) or {}
        envs = config.get("environments", [])
        return JSONResponse(envs)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/team/environments", status_code=201)
async def api_team_add_environment(request: Request):
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    project_id = body.get("project_id", "")
    env_name = body.get("name", "").strip()
    env_type = body.get("type", "").strip()
    if not project_id or not env_name:
        return JSONResponse({"error": "project_id and name are required"}, status_code=400)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        from utils.id_generator import generate_incident_id
        config = get_project_config(project_id) or {}
        envs = list(config.get("environments", []))
        new_env = {"id": "ENV-" + generate_incident_id(), "name": env_name, "type": env_type, "url": body.get("url", ""), "description": body.get("description", "")}
        envs.append(new_env)
        update_project_config(project_id, "environments", {"items": envs})
        return JSONResponse({"success": True, "environment": new_env}, status_code=201)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.delete("/api/team/environments/{env_id}")
async def api_team_delete_environment(env_id: str, request: Request):
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    project_id = body.get("project_id", "")
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        config = get_project_config(project_id) or {}
        envs = [e for e in config.get("environments", []) if e.get("id") != env_id]
        update_project_config(project_id, "environments", {"items": envs})
        return JSONResponse({"success": True})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/api/team-admin/assign-user")
async def api_assign_user_to_project(request: Request):
    redir = _require_role(request, "team_admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    user_id = body.get("user_id")
    project_id = body.get("project_id")
    if not user_id or not project_id:
        raise HTTPException(400, "user_id and project_id required")
    if _AUTH_AVAILABLE:
        assign_user_to_project(user_id, project_id)
    return {"ok": True}


@app.post("/api/team-admin/remove-user")
async def api_remove_user_from_project(request: Request):
    redir = _require_role(request, "team_admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    user_id = body.get("user_id")
    project_id = body.get("project_id")
    if not user_id or not project_id:
        raise HTTPException(400, "user_id and project_id required")
    if _AUTH_AVAILABLE:
        remove_user_from_project(user_id, project_id)
    return {"ok": True}


@app.post("/api/team-admin/set-features")
async def api_set_user_features(request: Request):
    redir = _require_role(request, "team_admin", "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    body = await request.json()
    user_id = body.get("user_id")
    features = body.get("features", [])
    if not user_id:
        raise HTTPException(400, "user_id required")
    result = set_user_features(user_id, features) if _AUTH_AVAILABLE else None
    return {"ok": True, "user": result}


@app.post("/api/admin/users", status_code=201)
async def api_create_user(request: Request):
    redir = _require_role(request, "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip()
    password = (payload.get("password") or "").strip()
    role = (payload.get("role") or "user").strip()

    if not username:
        return JSONResponse({"error": "Username is required"}, status_code=400)
    if not email:
        return JSONResponse({"error": "Email is required"}, status_code=400)
    if not password:
        return JSONResponse({"error": "Password is required"}, status_code=400)
    if role not in {"admin", "team_admin", "user"}:
        return JSONResponse({"error": "Valid role is required"}, status_code=400)

    all_existing = list_users()
    username_conflict = next((u for u in all_existing if u.get("username", "").lower() == username.lower()), None)
    email_conflict = next((u for u in all_existing if u.get("email", "").lower() == email.lower()), None)
    if username_conflict:
        return JSONResponse(
            {"error": f"Username '{username}' is already taken. Please choose a different username."},
            status_code=400,
        )
    if email_conflict:
        return JSONResponse(
            {"error": f"Email '{email}' is already registered (role: {email_conflict.get('role', 'user')}). Use a different email or log in with that account."},
            status_code=400,
        )

    user = create_user({
        "username": username,
        "email": email,
        "password": password,
        "role": role,
    })
    return user


@app.get("/api/admin/users")
async def api_list_users(request: Request):
    redir = _require_role(request, "admin", "team_admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    return list_users() if _AUTH_AVAILABLE else []


@app.put("/api/admin/users/{user_id}")
async def api_update_admin_user(user_id: str, request: Request):
    redir = _require_role(request, "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip()
    role = (payload.get("role") or "").strip()
    password = (payload.get("password") or "").strip()

    if not username:
        return JSONResponse({"error": "Username is required"}, status_code=400)
    if not email:
        return JSONResponse({"error": "Email is required"}, status_code=400)
    if role not in {"admin", "team_admin", "user"}:
        return JSONResponse({"error": "Valid role is required"}, status_code=400)

    existing_user = get_user_by_id(user_id)
    if not existing_user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    duplicate = next(
        (
            u for u in list_users()
            if u.get("id") != user_id and (
                u.get("username", "").lower() == username.lower()
                or u.get("email", "").lower() == email.lower()
            )
        ),
        None,
    )
    if duplicate:
        return JSONResponse({"error": "Another user already exists with the same username or email"}, status_code=400)

    update_payload = {
        "username": username,
        "email": email,
        "role": role,
    }
    if password:
        update_payload["password"] = password

    updated = update_user(user_id, update_payload)
    if not updated:
        return JSONResponse({"error": "User not found"}, status_code=404)

    return {"ok": True, "user": updated}


@app.delete("/api/admin/users/{user_id}")
async def api_delete_user(user_id: str, request: Request):
    redir = _require_role(request, "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    ok = delete_user(user_id) if _AUTH_AVAILABLE else False
    return {"ok": ok}


@app.post("/api/admin/onboard-project", status_code=201)
async def api_admin_onboard_project(request: Request):
    """Admin API: create a project shell and assign / create a Team Admin user."""
    redir = _require_role(request, "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    project_name = (body.get("name") or "").strip()
    team_admin_email = (body.get("team_admin_email") or "").strip()
    ta_username = (body.get("ta_username") or "").strip()
    ta_password = (body.get("ta_password") or "").strip()
    description = (body.get("description") or "").strip()
    stack = (body.get("stack") or "").strip()

    if not project_name:
        return JSONResponse({"error": "Project name is required"}, status_code=400)
    if not team_admin_email:
        return JSONResponse({"error": "Team Admin email is required"}, status_code=400)
    if not ta_username:
        return JSONResponse({"error": "Team Admin username is required"}, status_code=400)
    if not ta_password:
        return JSONResponse({"error": "Team Admin password is required"}, status_code=400)

    try:
        # Find or create Team Admin user
        all_users = list_users()
        ta_user = next(
            (u for u in all_users if u.get("email", "").lower() == team_admin_email.lower()),
            None,
        )
        if ta_user:
            # Update username if different
            if ta_user.get("username") != ta_username:
                update_user(ta_user["id"], {"username": ta_username})
        else:
            ta_user = create_user({
                "username": ta_username,
                "email": team_admin_email,
                "password": ta_password,
                "role": "team_admin",
            })

        # Create the project
        project = create_project({
            "name": project_name,
            "description": description,
            "stack": stack,
            "team_admin_id": ta_user["id"],
            "team_admin_email": team_admin_email,
        })

        # Assign Team Admin to project
        assign_user_to_project(ta_user["id"], project["id"])

        return JSONResponse(
            {
                "success": True,
                "project": project,
                "team_admin": {
                    "id": ta_user["id"],
                    "username": ta_user.get("username"),
                    "email": ta_user.get("email"),
                },
            },
            status_code=201,
        )
    except Exception as exc:
        logger.error("onboard-project error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.put("/api/admin/projects/{project_id}")
async def api_admin_update_project(project_id: str, request: Request):
    """Admin API: update project details."""
    redir = _require_role(request, "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    project_name = (body.get("name") or "").strip()
    if not project_name:
        return JSONResponse({"error": "Project name is required"}, status_code=400)

    try:
        updated = update_project(project_id, {
            "name": project_name,
            "stack": (body.get("stack") or "").strip(),
            "environment": (body.get("environment") or "production").strip(),
            "team_admin_email": (body.get("team_admin_email") or "").strip(),
        })
        if not updated:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        return JSONResponse({"success": True, "project": updated})
    except Exception as exc:
        logger.error("update-project error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.delete("/api/admin/projects/{project_id}")
async def api_admin_delete_project(project_id: str, request: Request):
    """Admin API: delete a project and unassign all members."""
    redir = _require_role(request, "admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    if not _AUTH_AVAILABLE:
        return JSONResponse({"error": "Auth not available"}, status_code=503)
    try:
        ok = delete_project(project_id)
        if not ok:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        return JSONResponse({"success": True, "deleted": project_id})
    except Exception as exc:
        logger.error("delete-project error: %s", exc, exc_info=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/projects/{project_id}/users")
async def api_get_project_users(project_id: str):
    return get_project_users(project_id) if _AUTH_AVAILABLE else []


@app.get("/api/me/projects")
async def api_my_projects(request: Request):
    user = _get_current_user(request)
    if not user:
        return []
    return get_user_projects(user.get("user_id", "")) if _AUTH_AVAILABLE else []


# ════════════════════════════════════════════════════════════════════════════
# INCIDENTS PAGE
# ════════════════════════════════════════════════════════════════════════════

@app.get("/incidents", response_class=HTMLResponse)
async def incidents_page(
    request: Request,
    severity: Optional[List[str]] = Query(default=None),
    status: Optional[List[str]] = Query(default=None),
    app_name: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    repo: IncidentRepository = Depends(get_repository),
):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    all_incidents = repo.get_all(limit=1000, search=search or None)

    if selected_project:
        project_scoped_incidents = [
            incident for incident in all_incidents
            if _incident_matches_project(incident, selected_project)
        ]
        if project_scoped_incidents:
            all_incidents = project_scoped_incidents
        else:
            logger.info(
                "No project-scoped incident matches found for project '%s' after app/repo/app_names mapping; showing all incidents instead",
                selected_project.get("name"),
            )

    # Available filter options
    all_severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    all_statuses = sorted({i.status for i in all_incidents if i.status})

    # Apply severity/status filters (app_name already handled by DB-level ilike)
    filtered = all_incidents
    if severity:
        filtered = [i for i in filtered if (i.severity or "") in severity]
    if status:
        filtered = [i for i in filtered if i.status in status]
    if app_name and not search:
        # Only apply Python-side app_name filter when not already done via search
        filtered = [i for i in filtered if app_name.lower() in (i.app_name or "").lower()]

    integration_summary = _get_project_integration_summary(selected_project)
    integration_status = _get_project_integration_status(selected_project)
    available_projects = _get_accessible_projects_with_fallback(user)

    return templates.TemplateResponse(
        request,
        "incidents.html",
        {
            "current_user": user,
            "selected_project": selected_project,
            "available_projects": available_projects,
            "integration_summary": integration_summary,
            "integration_status": integration_status,
            "incidents": filtered,
            "total": len(all_incidents),
            "all_severities": all_severities,
            "all_statuses": all_statuses,
            "filter_severity": severity or [],
            "filter_status": status or [],
            "filter_app": app_name or "",
            "filter_search": search or "",
            "page": "incidents",
        },
    )


@app.get("/incidents/{incident_id}", response_class=HTMLResponse)
async def incident_detail(
    request: Request,
    incident_id: str,
    repo: IncidentRepository = Depends(get_repository),
):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    rca_text = _incident_rca_text(inc)
    fix_text = getattr(inc, "proposed_fix", None) or getattr(inc, "fix_explanation", None)

    diff_rows = None
    proposed_fix = getattr(inc, "proposed_fix", None)
    if isinstance(proposed_fix, str) and proposed_fix:
        diff_rows = _build_diff_rows(proposed_fix)

    # Workflow steps definition
    # Patch generation now runs BEFORE approval so the reviewer can download
    # and test the patch before making their approval decision.
    workflow_steps = [
        ("assess_severity", "Severity Assessment"),
        ("generate_rca", "RCA Generation"),
        ("generate_fix", "Fix Generation"),
        ("generate_pdf", "PDF Generation"),
        ("reflect", "Quality Review"),
        ("generate_patch", "Patch Generation"),
        ("await_approval", "Approval"),
        ("send_notifications", "Notifications"),
        ("create_jira", "Jira Creation"),
        ("create_pr", "PR Creation"),
        ("finalize", "Finalize"),
    ]

    completed_steps = list(getattr(inc, "workflow_completed_steps", None) or [])
    current_node = getattr(inc, "current_workflow_node", None) or ""
    progress_pct = float(getattr(inc, "workflow_progress_pct", None) or 0.0)

    # Derive a more accurate "current node" display label from the incident's
    # runtime state.  The DB value (current_workflow_node) may say "finalize"
    # even when the workflow is actually paused at approval or stalled at PR
    # creation — override it here so the hero card shows the right step.
    _status_upper = (getattr(inc, "status", None) or "").upper()
    _appr_lower = (getattr(inc, "approval_status", None) or "").lower()
    if _status_upper == "PENDING_APPROVAL" or (_appr_lower == "pending" and not _status_upper.startswith("PR") and _status_upper != "COMPLETED"):
        current_node = "await_approval"
    elif _status_upper == "JIRA_CREATED" and not getattr(inc, "pr_url", None):
        current_node = "create_pr"
    elif _status_upper == "CREATING_JIRA_PR":
        current_node = "create_jira_pr"
    elif _status_upper == "PATCH_GENERATING":
        current_node = "generate_patch"
    if progress_pct > 1.0:
        progress_pct = progress_pct / 100.0
    progress_pct = min(progress_pct, 1.0)

    def _norm(s: str) -> str:
        return s.lower().replace(" ", "_").replace("__", "_")

    normalized_completed = [_norm(s) for s in completed_steps]
    has_legacy = "create_jira_pr" in normalized_completed
    inc_approval_status = getattr(inc, "approval_status", None) or "pending"

    # ── Infer completed steps from actual incident state ──────────────────
    # The DB field `workflow_completed_steps` can be stale or incomplete when
    # the workflow progresses without persisting every step transition.  We
    # augment it by inspecting the real artifacts / fields on the incident so
    # the Workflow Progress tracker always reflects reality.
    _inferred_set: set[str] = set(normalized_completed)

    # Severity assessed → severity field is populated
    if getattr(inc, "severity", None):
        _inferred_set.add("assess_severity")

    # RCA generated → rca_text is populated
    if getattr(inc, "rca_text", None):
        _inferred_set.add("assess_severity")
        _inferred_set.add("generate_rca")

    # Fix generated → proposed_fix or fix_explanation is populated
    _has_fix = bool(getattr(inc, "proposed_fix", None) or getattr(inc, "fix_explanation", None))
    if _has_fix:
        _inferred_set.add("generate_fix")

    # PDF generated → pdf_path is populated
    if getattr(inc, "pdf_path", None):
        _inferred_set.add("generate_pdf")

    # Quality review (reflect) → runs right after fix generation; infer from fix + quality score
    if _has_fix:
        _inferred_set.add("reflect")

    # Patch generated → patch_path is populated
    if getattr(inc, "patch_path", None):
        _inferred_set.add("generate_patch")

    # Approval done → approval_status recorded
    if inc_approval_status in ("approved", "rejected"):
        _inferred_set.add("await_approval")

    # Notifications sent → slack/teams flags, or status indicates we're past that point
    _post_approval_statuses = {"APPROVED", "JIRA_CREATED", "CREATING_JIRA_PR", "PR_CREATED", "COMPLETED"}
    if (
        getattr(inc, "slack_notification_sent", False)
        or getattr(inc, "teams_notification_sent", False)
        or _status_upper in _post_approval_statuses
    ):
        _inferred_set.add("send_notifications")

    # Jira created → jira_ticket_url / jira_ticket_key is populated, or status is JIRA_CREATED+
    _has_jira = bool(getattr(inc, "jira_ticket_url", None) or getattr(inc, "jira_ticket_key", None))
    if _has_jira or _status_upper in {"JIRA_CREATED", "PR_CREATED", "COMPLETED"}:
        _inferred_set.add("create_jira")

    # PR created → pr_url populated, or status is PR_CREATED/COMPLETED
    _has_pr = bool(getattr(inc, "pr_url", None))
    if _has_pr or _status_upper in {"PR_CREATED", "COMPLETED"}:
        _inferred_set.add("create_pr")

    # Finalized → status is COMPLETED
    if _status_upper == "COMPLETED":
        _inferred_set.add("finalize")

    # ── Node display label map ────────────────────────────────────────────
    _node_label_map = {
        "assess_severity": "Severity Assessment",
        "generate_rca": "RCA Generation",
        "generate_fix": "Fix Generation",
        "generate_pdf": "PDF Generation",
        "reflect": "Quality Review",
        "generate_patch": "Patch Generation",
        "await_approval": "Awaiting Approval",
        "send_notifications": "Notifications",
        "create_jira": "Jira Creation",
        "create_pr": "PR Creation",
        "create_jira_pr": "Jira & PR Creation",
        "finalize": "Finalization",
    }
    current_node_label = _node_label_map.get(current_node, current_node.replace("_", " ").title() if current_node else "Waiting")

    # Build enriched steps with strict sequential ordering.
    #
    # Rule: a step is only shown as "completed" when every preceding step in the
    # canonical sequence is also completed.  This prevents stale / accumulated DB
    # data from showing later steps as complete while earlier ones are still pending.
    #
    # The `await_approval` step is special: it pauses the workflow.  While waiting
    # for a human decision it is shown as "active", and all post-approval steps are
    # forced to "pending" until the decision is recorded.  Once approved/rejected
    # the chain continues and post-approval steps are evaluated normally.
    enriched_steps = []
    sequential_chain_broken = False

    for step_id, step_label in workflow_steps:
        norm_id = _norm(step_id)

        # Determine whether this step appears in the completed list.
        # Use the inferred set which augments stale DB data with actual state.
        # When the combined create_jira_pr node has run, treat both display
        # sub-steps (create_jira and create_pr) as individually completed.
        if has_legacy:
            in_completed = True if step_id in ("create_jira", "create_pr") else norm_id in _inferred_set
        else:
            in_completed = norm_id in _inferred_set

        is_failed = "failed" in current_node.lower()

        if sequential_chain_broken:
            # All steps after a gap or a pause must remain pending.
            step_state = "pending"

        elif step_id == "await_approval":
            # Approval is a pause-point: it can be in completed_steps before a
            # human has acted (the handler adds it immediately to track progress).
            if in_completed:
                if inc_approval_status in ("approved", "rejected"):
                    step_state = "completed"
                    # Chain continues — post-approval steps may have run.
                else:
                    step_state = "active"  # Waiting for human decision.
                    sequential_chain_broken = True  # Post-approval steps not yet started.
            else:
                step_state = "pending"
                sequential_chain_broken = True

        elif in_completed:
            # Step completed and the sequential chain is intact.
            step_state = "completed"

        else:
            # Step has not completed yet — mark active if it is the current node,
            # otherwise pending.  Either way, break the sequential chain so that
            # no later step can be shown as completed.
            is_active = step_id in current_node
            # PR Creation stuck: JIRA_CREATED status means the workflow ran through
            # create_pr but could not create a PR (missing content, repo, etc.).
            # Show as "failed" rather than "active" so the operator can diagnose.
            pr_creation_stuck = (
                step_id == "create_pr"
                and _status_upper == "JIRA_CREATED"
                and not getattr(inc, "pr_url", None)
            )
            if is_active and (is_failed or pr_creation_stuck):
                step_state = "failed"
            elif is_active:
                step_state = "active"
            else:
                step_state = "pending"
            sequential_chain_broken = True

        enriched_steps.append(
            {"id": step_id, "label": step_label, "state": step_state}
        )

    pdf_artifact = _build_artifact_info(getattr(inc, "pdf_path", None), "pdf", incident_id)
    patch_artifact = _build_artifact_info(getattr(inc, "patch_path", None), "patch", incident_id)
    incident_links = _build_incident_links(inc, pdf_artifact, patch_artifact)
    timeline_events = _build_incident_timeline(inc, pdf_artifact, patch_artifact)
    workflow_improvements = _workflow_improvements(inc, pdf_artifact, patch_artifact)

    # Integration status for the active project — used to conditionally show
    # delivery channels (e.g. Teams) only when they are actually configured.
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    integration_status = _get_project_integration_status(selected_project)

    return templates.TemplateResponse(
        request,
        "incident_detail.html",
        {
            "inc": inc,
            "rca_text": rca_text,
            "fix_text": fix_text,
            "diff_rows": diff_rows,
            "workflow_steps": enriched_steps,
            "progress_pct": int(progress_pct * 100),
            "current_node": current_node,
            "current_node_label": current_node_label,
            "is_failed": "failed" in current_node.lower(),
            "pdf_artifact": pdf_artifact,
            "patch_artifact": patch_artifact,
            "incident_links": incident_links,
            "timeline_events": timeline_events,
            "workflow_improvements": workflow_improvements,
            "teams_configured": integration_status.get("teams", False),
            "slack_configured": integration_status.get("slack", False),
            "page": "incidents",
        },
    )


@app.get("/incidents/{incident_id}/artifacts/{artifact_kind}")
async def incident_artifact(
    request: Request,
    incident_id: str,
    artifact_kind: str,
    download: int = Query(default=0),
    repo: IncidentRepository = Depends(get_repository),
):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect

    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    if artifact_kind == "pdf":
        artifact = _build_artifact_info(getattr(inc, "pdf_path", None), "pdf", incident_id)
        media_type = "application/pdf"
    elif artifact_kind == "patch":
        artifact = _build_artifact_info(getattr(inc, "patch_path", None), "patch", incident_id)
        media_type = mimetypes.guess_type(getattr(inc, "patch_path", "") or "")[0] or "text/plain"
    else:
        raise HTTPException(status_code=404, detail="Artifact type not supported")

    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not available")

    return FileResponse(
        path=artifact["path"],
        media_type=media_type,
        filename=artifact["name"] if download else None,
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    repo: IncidentRepository = Depends(get_repository),
):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    role = (user or {}).get("role", "user")
    # Redirect admin to admin dashboard — admin has no project-level settings
    if role == "admin":
        return RedirectResponse(url="/admin/dashboard", status_code=302)
    # Redirect team_admin to their project configure page instead
    if role == "team_admin":
        my_projects = _get_accessible_projects_with_fallback(user)
        if my_projects:
            return RedirectResponse(url=f"/team-admin/onboarding?project_id={my_projects[0]['id']}", status_code=302)
        return RedirectResponse(url="/team-admin/dashboard", status_code=302)
    # DB size
    db_size_kb = None
    try:
        db_path = os.path.abspath(settings.database_path)
        if os.path.exists(db_path):
            db_size_kb = round(os.path.getsize(db_path) / 1024, 1)
    except Exception:
        pass

    incidents = repo.get_all(limit=1000)
    stats = _compute_stats(incidents)

    # Convert Pydantic model to a plain dict so the template can use .get()
    try:
        settings_dict = settings.model_dump()          # Pydantic v2
    except AttributeError:
        settings_dict = settings.dict()                # Pydantic v1

    # Add aliases the template references that differ from the model field names
    settings_dict.setdefault("db_url", settings_dict.get("database_path", ""))
    settings_dict.setdefault("default_branch", settings_dict.get("github_base_branch", "main"))
    settings_dict.setdefault("email_recipients", settings_dict.get("email_to", ""))
    settings_dict.setdefault("approval_mode", "manual" if settings_dict.get("auto_fix_requires_approval", True) else "auto")
    settings_dict.setdefault("llm_api_key", settings_dict.get("openai_api_key", "") or settings_dict.get("anthropic_api_key", "") or "")
    settings_dict.setdefault("llm_base_url", settings_dict.get("ollama_base_url", ""))
    settings_dict.setdefault("slack_channel", "")

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "current_user": user,
            "settings": settings_dict,
            "db_size_kb": db_size_kb,
            "stats": stats,
            "page": "settings",
        },
    )


# ════════════════════════════════════════════════════════════════════════════
# JSON API ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/health")
async def api_health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/stats")
async def api_stats(repo: IncidentRepository = Depends(get_repository)):
    """Return aggregate statistics using efficient SQL COUNT queries."""
    try:
        stats = repo.get_stats()
        # Normalise keys for backward compat with the existing dashboard template
        return {
            "total": stats["total_incidents"],
            "total_incidents": stats["total_incidents"],
            "critical": stats["by_severity"].get("CRITICAL", 0),
            "high": stats["by_severity"].get("HIGH", 0),
            "medium": stats["by_severity"].get("MEDIUM", 0),
            "low": stats["by_severity"].get("LOW", 0),
            "pending_approval": stats["pending_approval"],
            "prs_created": stats["prs_created"],
            "jira_created": stats["jira_created"],
            "completed": stats["completed"],
            "with_rca": stats["with_rca"],
            "by_severity": stats["by_severity"],
            "by_status": stats["by_status"],
        }
    except Exception:
        # Fallback to Python aggregation if SQL method fails
        incidents = repo.get_all(limit=1000)
        return _compute_stats(incidents)


@app.get("/api/incidents")
async def api_incidents(
    request: Request,
    severity: Optional[List[str]] = Query(default=None),
    status: Optional[List[str]] = Query(default=None),
    app_name: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    repo: IncidentRepository = Depends(get_repository),
):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)

    incidents = repo.get_all(
        limit=max(limit + offset, 1000),
        search=search or None,
    )

    if selected_project:
        project_scoped_incidents = [
            incident for incident in incidents
            if _incident_matches_project(incident, selected_project)
        ]
        if project_scoped_incidents:
            incidents = project_scoped_incidents

    if severity:
        severity_set = {value for value in severity if value}
        incidents = [incident for incident in incidents if (incident.severity or "") in severity_set]

    if status:
        status_set = {value for value in status if value}
        incidents = [incident for incident in incidents if (incident.status or "") in status_set]

    if app_name and not search:
        incidents = [incident for incident in incidents if app_name.lower() in (incident.app_name or "").lower()]

    incidents = incidents[offset: offset + limit]
    return [_incident_to_dict(i) for i in incidents]


# ── CSV Export (must be defined BEFORE /{incident_id} to avoid route conflict) ─

@app.get("/api/incidents/export.csv")
async def api_export_incidents_csv(
    request: Request,
    severity: Optional[List[str]] = Query(default=None),
    status: Optional[List[str]] = Query(default=None),
    app_name: Optional[str] = None,
    search: Optional[str] = None,
    repo: IncidentRepository = Depends(get_repository),
):
    """
    Stream all matching incidents as a CSV file.

    Supports the same filters as the incidents list page:
    severity, status, app_name, search.
    """
    import csv
    import io

    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect

    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)

    incidents = repo.get_all(limit=10000, search=search)

    if selected_project:
        project_scoped_incidents = [
            incident for incident in incidents
            if _incident_matches_project(incident, selected_project)
        ]
        if project_scoped_incidents:
            incidents = project_scoped_incidents

    if severity:
        severity_set = {value for value in severity if value}
        incidents = [incident for incident in incidents if (incident.severity or "") in severity_set]

    if status:
        status_set = {value for value in status if value}
        incidents = [incident for incident in incidents if (incident.status or "") in status_set]

    if app_name and not search:
        incidents = [incident for incident in incidents if app_name.lower() in (incident.app_name or "").lower()]

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "incident_id", "app_name", "environment", "severity", "status",
        "error_title", "rca_confidence", "fix_quality_score",
        "approval_status", "jira_ticket_key", "jira_ticket_url",
        "pr_url", "fix_branch", "created_at", "updated_at",
    ])

    for inc in incidents:
        writer.writerow([
            getattr(inc, "incident_id", ""),
            getattr(inc, "app_name", ""),
            getattr(inc, "environment", ""),
            getattr(inc, "severity", ""),
            getattr(inc, "status", ""),
            getattr(inc, "error_title", ""),
            getattr(inc, "rca_confidence", ""),
            getattr(inc, "fix_quality_score", ""),
            getattr(inc, "approval_status", ""),
            getattr(inc, "jira_ticket_key", ""),
            getattr(inc, "jira_ticket_url", ""),
            getattr(inc, "pr_url", ""),
            getattr(inc, "fix_branch", ""),
            _fmt_dt(getattr(inc, "created_at", None)),
            _fmt_dt(getattr(inc, "updated_at", None)),
        ])

    csv_content = output.getvalue()
    filename = f"prism_incidents_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/incidents/{incident_id}")
async def api_incident(
    incident_id: str,
    repo: IncidentRepository = Depends(get_repository),
):
    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _incident_to_dict(inc)


@app.post("/api/incidents/{incident_id}/approve")
async def api_approve(
    incident_id: str,
    action: str = Form(...),
    notes: str = Form(default=""),
    repo: IncidentRepository = Depends(get_repository),
):
    """Approve or reject an incident fix — called from both form POST and JS fetch."""
    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    action = action.strip().lower()
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    updates: dict = {"approval_notes": notes or None}
    if action == "approve":
        updates["approval_status"] = "approved"
        updates["approved_at"] = datetime.utcnow()
        updates["status"] = IncidentStatus.APPROVED.value
    else:
        updates["approval_status"] = "rejected"
        updates["approved_at"] = datetime.utcnow()
        updates["status"] = IncidentStatus.REJECTED.value

    repo.update(incident_id, **updates)

    # Notify SSE subscribers
    await _broadcast(
        "approval",
        {"incident_id": incident_id, "action": action, "notes": notes},
    )

    return JSONResponse({"ok": True, "action": action, "incident_id": incident_id})


@app.post("/incidents/{incident_id}/approve", response_class=RedirectResponse)
async def form_approve(
    incident_id: str,
    action: str = Form(...),
    notes: str = Form(default=""),
    repo: IncidentRepository = Depends(get_repository),
):
    """Form-based approve/reject — redirects back to detail page."""
    await api_approve(incident_id=incident_id, action=action, notes=notes, repo=repo)
    return RedirectResponse(url=f"/incidents/{incident_id}", status_code=303)


@app.post("/api/incidents/{incident_id}/trigger")
async def api_trigger_workflow(
    request: Request,
    incident_id: str,
    repo: IncidentRepository = Depends(get_repository),
):
    """Trigger / re-trigger the LangGraph workflow for an incident.

    Blocked for incidents that are already approved, completed, or have a PR
    created — re-running the full workflow from scratch on such incidents would
    overwrite the approval decision and reset the status to PENDING_APPROVAL.
    """
    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Guard: refuse to re-run the full workflow on approved / terminal incidents
    _protected_statuses = {"APPROVED", "PR_CREATED", "COMPLETED", "JIRA_CREATED", "REJECTED"}
    _protected_approval = {"approved", "rejected"}
    current_status = str(getattr(inc, "status", "") or "").upper()
    current_approval = str(getattr(inc, "approval_status", "") or "").lower()

    if current_status in _protected_statuses or current_approval in _protected_approval:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Incident {incident_id} is in a protected state "
                f"(status={current_status}, approval_status={current_approval}). "
                "Re-triggering the full workflow would erase the approval decision. "
                "Use the /continue-post-approval endpoint to resume post-approval steps."
            ),
        )

    try:
        from agents.workflow import run_workflow_for_incident

        user = _get_current_user(request)
        selected_project = _get_selected_project(request, user)
        selected_project_id = selected_project.get("id") if selected_project else None

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, run_workflow_for_incident, incident_id, selected_project_id)
        return {"ok": True, "message": f"Workflow triggered for {incident_id}"}
    except ImportError:
        raise HTTPException(status_code=501, detail="Workflow module not available")
    except Exception as exc:
        logger.error("Workflow trigger error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/incidents/{incident_id}/continue-post-approval")
async def api_continue_post_approval(
    request: Request,
    incident_id: str,
    repo: IncidentRepository = Depends(get_repository),
):
    """
    Continue post-approval workflow for an approved incident.
    Called automatically from the UI after the user clicks 'Approve'.
    Runs: patch generation → notifications → Jira → PR → finalize.
    """
    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    approval_status = getattr(inc, "approval_status", None)
    if approval_status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Incident is not approved (approval_status={approval_status}). Approve first.",
        )

    try:
        from agents.workflow import run_post_approval_workflow

        user = _get_current_user(request)
        selected_project = _get_selected_project(request, user)
        selected_project_id = selected_project.get("id") if selected_project else None

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, run_post_approval_workflow, incident_id, selected_project_id)

        logger.info("Post-approval workflow started for incident %s", incident_id)
        return {"ok": True, "message": f"Post-approval workflow started for {incident_id}"}
    except ImportError:
        raise HTTPException(status_code=501, detail="Workflow module not available")
    except Exception as exc:
        logger.error("Post-approval workflow error for %s: %s", incident_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/incidents")
async def api_clear_incidents(repo: IncidentRepository = Depends(get_repository)):
    """Delete ALL incidents — use with caution (dev/debug only)."""
    db = repo.db
    from storage.database import Incident as IncidentModel

    count = db.query(IncidentModel).count()
    db.query(IncidentModel).delete()
    db.commit()
    return {"ok": True, "deleted": count}


# ── Bulk Actions ──────────────────────────────────────────────────────────────

@app.post("/api/incidents/bulk-approve")
async def api_bulk_approve(
    request: Request,
    repo: IncidentRepository = Depends(get_repository),
):
    """
    Bulk approve or reject multiple incidents in a single request.

    Request body:
    {
      "incident_ids": ["XXXX", "YYYY"],
      "action": "approve" | "reject",
      "notes": "optional reviewer comment"
    }

    Returns per-incident success/failure results.
    """
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    incident_ids = body.get("incident_ids") or []
    action = (body.get("action") or "").strip().lower()
    notes = (body.get("notes") or "").strip()

    if not incident_ids:
        return JSONResponse({"error": "incident_ids is required"}, status_code=400)
    if action not in ("approve", "reject"):
        return JSONResponse({"error": "action must be 'approve' or 'reject'"}, status_code=400)
    if len(incident_ids) > 100:
        return JSONResponse({"error": "Maximum 100 incidents per bulk operation"}, status_code=400)

    results: list[dict] = []
    approved_at = datetime.utcnow()

    for incident_id in incident_ids:
        try:
            inc = repo.get_by_id(str(incident_id))
            if not inc:
                results.append({"incident_id": incident_id, "ok": False, "error": "Not found"})
                continue

            updates: dict = {"approval_notes": notes or None, "approved_at": approved_at}
            if action == "approve":
                updates["approval_status"] = "approved"
                updates["status"] = IncidentStatus.APPROVED.value
            else:
                updates["approval_status"] = "rejected"
                updates["status"] = IncidentStatus.REJECTED.value

            repo.update(str(incident_id), **updates)

            # Broadcast SSE event
            await _broadcast("approval", {"incident_id": incident_id, "action": action, "notes": notes})
            results.append({"incident_id": incident_id, "ok": True, "action": action})

        except Exception as exc:
            logger.error("bulk-approve error for %s: %s", incident_id, exc)
            results.append({"incident_id": incident_id, "ok": False, "error": str(exc)})

    succeeded = sum(1 for r in results if r["ok"])
    failed = len(results) - succeeded

    return JSONResponse({
        "ok": True,
        "action": action,
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    })


# ── Analytics — Trend Data ────────────────────────────────────────────────────

@app.get("/api/analytics/trends")
async def api_analytics_trends(
    days: int = Query(default=14, ge=1, le=90),
    repo: IncidentRepository = Depends(get_repository),
):
    """
    Return daily incident trend data and MTTR stats for the last N days.

    Response shape:
    {
      "trends": [{"date": "2026-06-01", "total": 5, "approved": 3, "rejected": 1, "acceptance_rate": 60.0}, ...],
      "mttr": {"mttr_minutes": 12.4, "sample_size": 18},
      "summary": {"total_incidents": 42, "by_severity": {...}, ...}
    }
    """
    trends = repo.get_daily_trends(days=days)
    mttr = repo.get_mttr_stats()
    stats = repo.get_stats()

    return JSONResponse({
        "trends": trends,
        "mttr": mttr,
        "summary": stats,
        "days": days,
    })


# ── Fix Re-generation with Reviewer Feedback ─────────────────────────────────

@app.post("/api/incidents/{incident_id}/regenerate-fix")
async def api_regenerate_fix(
    request: Request,
    incident_id: str,
    repo: IncidentRepository = Depends(get_repository),
):
    """
    Re-generate the fix for a rejected incident, injecting the reviewer's
    rejection notes as additional context into the fix generation prompt.

    Triggers the full fix → reflect loop in the background.

    Request body (optional):
    { "feedback": "The fix doesn't handle the null case in line 42" }
    """
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    try:
        body = await request.json()
    except Exception:
        body = {}

    feedback = (body.get("feedback") or getattr(inc, "approval_notes", "") or "").strip()

    # Store the feedback as a new regeneration note
    existing_notes = getattr(inc, "approval_notes", "") or ""
    regen_note = f"[Re-generation requested] {feedback}" if feedback else "[Re-generation requested]"
    combined_notes = f"{existing_notes}\n{regen_note}".strip() if existing_notes else regen_note

    # Reset approval status so fix flows back through approval
    repo.update(
        incident_id,
        approval_status="pending",
        approval_notes=combined_notes,
        proposed_fix=None,
        fix_explanation=None,
        patch_path=None,
    )

    # Run the fix regeneration in the background via executor
    async def _run_regen():
        try:
            from agents.nodes.fix_generator import generate_fix_node
            from agents.nodes.reflector import reflect_on_fix_node
            from agents.state import create_initial_state
            from storage.database import get_session as _get_sess

            with _get_sess() as session:
                fresh_repo = IncidentRepository(session)
                fresh_inc = fresh_repo.get_by_id(incident_id)
                if not fresh_inc:
                    return

                state = create_initial_state(
                    incident_id=incident_id,
                    app_name=str(fresh_inc.app_name or ""),
                    environment=str(fresh_inc.environment or ""),
                    error_title=str(fresh_inc.error_title or ""),
                    error_description=str(fresh_inc.error_description or ""),
                    stack_trace=str(fresh_inc.stack_trace or ""),
                    raw_log=str(fresh_inc.raw_log or ""),
                    fingerprint=str(getattr(fresh_inc, "error_fingerprint", "") or ""),
                    severity=str(fresh_inc.severity or "HIGH"),
                    is_duplicate=False,
                    created_at=str(getattr(fresh_inc, "created_at", "") or ""),
                    metadata=getattr(fresh_inc, "incident_metadata", None),
                )

            # Inject existing RCA and code context
            with _get_sess() as session:
                fresh_repo = IncidentRepository(session)
                fresh_inc = fresh_repo.get_by_id(incident_id)
                if fresh_inc:
                    state["rca_text"] = getattr(fresh_inc, "rca_text", None)
                    state["repo_full_name"] = getattr(fresh_inc, "repo_full_name", None)
                    state["error_file_path"] = getattr(fresh_inc, "error_file_path", None)
                    state["error_line_number"] = getattr(fresh_inc, "error_line_number", None)
                    state["error_file_type"] = getattr(fresh_inc, "error_file_type", None)
                    state["original_code"] = getattr(fresh_inc, "fetched_code", None)

            # Inject reviewer feedback into RCA context so fix generator sees it
            if feedback:
                existing_rca = state.get("rca_text") or ""
                state["rca_text"] = (
                    existing_rca
                    + f"\n\n---\n**Reviewer Feedback for Re-generation:**\n{feedback}\n---"
                )

            # Determine project_id
            from agents.workflow import _resolve_project_id_for_incident
            project_id = _resolve_project_id_for_incident(
                None,
                state.get("app_name"),
                state.get("environment"),
            )
            state["project_id"] = project_id

            # Re-run fix generation and reflection
            state = generate_fix_node(state)
            state = reflect_on_fix_node(state)

            logger.info("[Regen] Fix regenerated for incident %s", incident_id)

        except Exception as exc:
            logger.error("[Regen] Fix regeneration failed for %s: %s", incident_id, exc)

    loop = asyncio.get_event_loop()
    loop.create_task(_run_regen())

    return JSONResponse({
        "ok": True,
        "incident_id": incident_id,
        "message": "Fix re-generation started. Check the incident page for updated results.",
        "feedback_applied": bool(feedback),
    })


# ── Incident Comment Thread ───────────────────────────────────────────────────

@app.get("/api/incidents/{incident_id}/comments")
async def api_get_comments(
    request: Request,
    incident_id: str,
    repo: IncidentRepository = Depends(get_repository),
):
    """
    Return all comments for an incident thread, ordered oldest-first.

    Response:
    {
      "incident_id": "XXXX",
      "comments": [{"comment_id": "...", "author": "...", "content": "...", ...}],
      "count": 3
    }
    """
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    comments = repo.get_comments(incident_id)
    return JSONResponse({
        "incident_id": incident_id,
        "comments": [repo.serialize_comment(c) for c in comments],
        "count": len(comments),
    })


@app.post("/api/incidents/{incident_id}/comments", status_code=201)
async def api_add_comment(
    request: Request,
    incident_id: str,
    repo: IncidentRepository = Depends(get_repository),
):
    """
    Add a comment to an incident thread.

    Request body:
    { "content": "This looks like a connection pool exhaustion issue." }

    Returns the created comment.
    """
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    inc = repo.get_by_id(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    content = (body.get("content") or "").strip()
    if not content:
        return JSONResponse({"error": "content is required"}, status_code=400)
    if len(content) > 4000:
        return JSONResponse({"error": "Comment must be ≤ 4000 characters"}, status_code=400)

    # Get author from the current session user
    user = _get_current_user(request)
    author = (user or {}).get("username") or (user or {}).get("email") or "user"

    comment = repo.add_comment(
        incident_id=incident_id,
        content=content,
        author=author,
        comment_type="comment",
    )

    # Broadcast SSE so the incident detail page can refresh comments live
    await _broadcast("comment", {
        "incident_id": incident_id,
        "comment_id": comment.comment_id,
        "author": comment.author,
    })

    return JSONResponse(repo.serialize_comment(comment), status_code=201)


@app.delete("/api/incidents/{incident_id}/comments/{comment_id}")
async def api_delete_comment(
    request: Request,
    incident_id: str,
    comment_id: str,
    repo: IncidentRepository = Depends(get_repository),
):
    """Delete a specific comment (only for admin / team_admin roles)."""
    redir = _require_role(request, "admin", "team_admin")
    if redir:
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    ok = repo.delete_comment(comment_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Comment not found")

    return JSONResponse({"ok": True, "deleted": comment_id})


# ════════════════════════════════════════════════════════════════════════════
# SERVER-SENT EVENTS
# ════════════════════════════════════════════════════════════════════════════

@app.get("/events")
async def sse_stream(request: Request):
    """SSE endpoint — streams incident updates to the browser."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_subscribers.append(queue)

    async def _generator() -> AsyncGenerator[str, None]:
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_subscribers.remove(queue)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ════════════════════════════════════════════════════════════════════════════
# NEW UI PAGES — Observability, Log Viewer, Admin sub-pages
# ════════════════════════════════════════════════════════════════════════════

@app.get("/app-health", response_class=HTMLResponse)
async def app_health_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    integration_status = _get_project_integration_status(selected_project)
    observability_data = _load_live_observability_data(selected_project)
    metrics_rows = observability_data["metrics_rows"]
    incidents = observability_data["incidents"]

    service_health_rows = []
    for metric in metrics_rows:
        related_incidents = [
            inc for inc in incidents
            if getattr(inc, "app_name", None) == metric["app"]
            and getattr(inc, "environment", None) == metric["environment"]
        ]
        error_rate = float(metric["error_rate"])
        if error_rate >= 5:
            status = "down"
        elif error_rate >= 1:
            status = "degraded"
        else:
            status = "healthy"

        latency_ms = int(metric["p95_latency_ms"])
        uptime_pct = round(max(0.0, min(99.99, 100.0 - (error_rate * 1.8))), 1)
        latest_incident = None
        if related_incidents:
            latest_incident = max(
                related_incidents,
                key=lambda inc: getattr(inc, "updated_at", None) or getattr(inc, "created_at", None) or datetime.min,
            )

        service_health_rows.append(
            {
                "name": metric["app"],
                "environment": metric["environment"],
                "status": status,
                "uptime": uptime_pct,
                "latency": latency_ms,
                "error_rate": error_rate,
                "incidents": len(related_incidents),
                "last_incident": _fmt_dt(
                    getattr(latest_incident, "updated_at", None) or getattr(latest_incident, "created_at", None)
                ) if latest_incident else "—",
            }
        )

    if not service_health_rows and selected_project:
        service_health_rows.append(
            {
                "name": selected_project.get("name", "selected-project"),
                "environment": selected_project.get("environment", "unknown"),
                "status": "healthy",
                "uptime": 100.0,
                "latency": 0,
                "error_rate": 0.0,
                "incidents": 0,
                "last_incident": "—",
            }
        )

    readiness = _build_demo_readiness(selected_project)

    return templates.TemplateResponse(
        request,
        "app_health.html",
        {
            "current_user": user,
            "selected_project": selected_project,
            "integration_status": integration_status,
            "service_health_rows": service_health_rows,
            "health_summary": {
                "healthy": sum(1 for row in service_health_rows if row["status"] == "healthy"),
                "degraded": sum(1 for row in service_health_rows if row["status"] == "degraded"),
                "down": sum(1 for row in service_health_rows if row["status"] == "down"),
                "avg_latency": round(sum(row["latency"] for row in service_health_rows) / len(service_health_rows)) if service_health_rows else 0,
            },
            "readiness": readiness,
            "page": "app_health",
        },
    )


@app.get("/observability", response_class=HTMLResponse)
async def observability_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    integration_status = _get_project_integration_status(selected_project)
    return templates.TemplateResponse(
        request,
        "observability.html",
        {
            "current_user": user,
            "selected_project": selected_project,
            "integration_status": integration_status,
            "page": "observability",
        },
    )


@app.get("/logs", response_class=HTMLResponse)
async def log_viewer_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    integration_status = _get_project_integration_status(selected_project)
    return templates.TemplateResponse(
        request,
        "log_viewer.html",
        {
            "current_user": user,
            "selected_project": selected_project,
            "integration_status": integration_status,
            "page": "log_viewer",
        },
    )


@app.get("/traces", response_class=HTMLResponse)
async def traces_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    integration_status = _get_project_integration_status(selected_project)
    observability_data = _load_live_observability_data(selected_project)
    return templates.TemplateResponse(
        request,
        "traces.html",
        {
            "current_user": user,
            "selected_project": selected_project,
            "integration_status": integration_status,
            "trace_summary": observability_data["trace_summary"],
            "traces": observability_data["traces"],
            "failed_spans": observability_data["failed_spans"],
            "page": "traces",
        },
    )


@app.get("/metrics", response_class=HTMLResponse)
async def metrics_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    integration_status = _get_project_integration_status(selected_project)
    observability_data = _load_live_observability_data(selected_project)
    return templates.TemplateResponse(
        request,
        "metrics.html",
        {
            "current_user": user,
            "selected_project": selected_project,
            "integration_status": integration_status,
            "metrics_summary": observability_data["metrics_summary"],
            "metrics_rows": observability_data["metrics_rows"],
            "top_error_apps": observability_data["top_error_apps"],
            "page": "metrics",
        },
    )


@app.get("/api-analytics", response_class=HTMLResponse)
async def api_analytics_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    integration_status = _get_project_integration_status(selected_project)
    observability_data = _load_live_observability_data(selected_project)
    return templates.TemplateResponse(
        request,
        "api_analytics.html",
        {
            "current_user": user,
            "selected_project": selected_project,
            "integration_status": integration_status,
            "analytics_summary": observability_data["analytics_summary"],
            "analytics_rows": observability_data["analytics_rows"],
            "policy_hotspots": observability_data["policy_hotspots"],
            "page": "api_analytics",
        },
    )


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    user = _get_current_user(request)
    selected_project = _get_selected_project(request, user)
    integration_status = _get_project_integration_status(selected_project)
    observability_data = _load_live_observability_data(selected_project)
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "current_user": user,
            "selected_project": selected_project,
            "integration_status": integration_status,
            "audit_summary": observability_data["audit_summary"],
            "audit_rows": observability_data["audit_rows"],
            "failed_integrations": observability_data["failed_integrations"],
            "page": "audit",
        },
    )


@app.get("/admin/projects", response_class=HTMLResponse)
async def admin_projects_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse(
        request,
        "admin_projects.html",
        {"page": "admin_projects"},
    )


@app.get("/admin/teams", response_class=HTMLResponse)
async def admin_teams_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    return templates.TemplateResponse(
        request,
        "admin_teams.html",
        {"page": "admin_teams"},
    )


# ════════════════════════════════════════════════════════════════════════════
# ADMIN — CUSTOMER MANAGEMENT (HTML PAGES)
# ════════════════════════════════════════════════════════════════════════════

@app.get("/admin/customers", response_class=HTMLResponse)
async def admin_customers_page(request: Request):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    customers = list_customers()
    return templates.TemplateResponse(
        request,
        "admin_customers.html",
        {
            "customers": customers,
            "page": "admin_customers",
        },
    )


@app.get("/admin/customers/{customer_id}", response_class=HTMLResponse)
async def admin_customer_detail_page(request: Request, customer_id: str):
    auth_redirect = _require_auth(request)
    if auth_redirect:
        return auth_redirect
    customer = get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return templates.TemplateResponse(
        request,
        "admin_customer_detail.html",
        {
            "customer": customer,
            "page": "admin_customers",
        },
    )


# ════════════════════════════════════════════════════════════════════════════
# ADMIN — CUSTOMER MANAGEMENT (JSON API)
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/admin/customers")
async def api_list_customers():
    return list_customers()


@app.post("/api/admin/customers", status_code=201)
async def api_create_customer(request: Request):
    payload = await request.json()
    if not payload.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    customer = create_customer(payload)
    return customer


@app.get("/api/admin/customers/{customer_id}")
async def api_get_customer(customer_id: str):
    customer = get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@app.patch("/api/admin/customers/{customer_id}")
async def api_update_customer(customer_id: str, request: Request):
    payload = await request.json()
    updated = update_customer(customer_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Customer not found")
    return updated


@app.delete("/api/admin/customers/{customer_id}")
async def api_delete_customer(customer_id: str):
    ok = delete_customer(customer_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"ok": True, "deleted": customer_id}


# ── Environments ─────────────────────────────────────────────────────────────

@app.post("/api/admin/customers/{customer_id}/environments", status_code=201)
async def api_add_environment(customer_id: str, request: Request):
    if not get_customer(customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    payload = await request.json()
    env = add_environment(customer_id, payload)
    if not env:
        raise HTTPException(status_code=500, detail="Failed to add environment")
    return env


@app.delete("/api/admin/customers/{customer_id}/environments/{env_id}")
async def api_remove_environment(customer_id: str, env_id: str):
    ok = remove_environment(customer_id, env_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Environment not found")
    return {"ok": True, "deleted": env_id}


# ── Team members ──────────────────────────────────────────────────────────────

@app.post("/api/admin/customers/{customer_id}/members", status_code=201)
async def api_add_member(customer_id: str, request: Request):
    if not get_customer(customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    payload = await request.json()
    if not payload.get("email"):
        raise HTTPException(status_code=400, detail="email is required")
    member = add_team_member(customer_id, payload)
    if not member:
        raise HTTPException(status_code=500, detail="Failed to add team member")
    return member


@app.patch("/api/admin/customers/{customer_id}/members/{member_id}")
async def api_update_member(customer_id: str, member_id: str, request: Request):
    payload = await request.json()
    updated = update_team_member(customer_id, member_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Member not found")
    return updated


@app.delete("/api/admin/customers/{customer_id}/members/{member_id}")
async def api_remove_member(customer_id: str, member_id: str):
    ok = remove_team_member(customer_id, member_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"ok": True, "deleted": member_id}


# ── Integrations ──────────────────────────────────────────────────────────────

@app.patch("/api/admin/customers/{customer_id}/integrations/{integration_name}")
async def api_update_integration(customer_id: str, integration_name: str, request: Request):
    if not get_customer(customer_id):
        raise HTTPException(status_code=404, detail="Customer not found")
    payload = await request.json()
    result = update_integration(customer_id, integration_name, payload)
    if result is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return result


# ════════════════════════════════════════════════════════════════════════════
# SIDEBAR CONTEXT API
# ════════════════════════════════════════════════════════════════════════════

@app.get("/api/sidebar/projects")
async def api_sidebar_projects():
    """Return all projects for the sidebar project selector."""
    if not _AUTH_AVAILABLE:
        return []
    try:
        projects = list_projects()
        return [{"id": p["id"], "name": p["name"]} for p in projects]
    except Exception:
        return []


@app.get("/api/sidebar/apps")
async def api_sidebar_apps(repo: IncidentRepository = Depends(get_repository)):
    """Return distinct application names from incidents for the app selector."""
    try:
        incidents = repo.get_all(limit=1000)
        apps = sorted({str(i.app_name) for i in incidents if getattr(i, "app_name", None) is not None and str(i.app_name).strip()})
        return apps
    except Exception:
        return []


@app.post("/api/switch-project")
async def api_switch_project(request: Request):
    """Store the active project selection in a session cookie."""
    user = _get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "message": "Not authenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

    project_id = (body.get("project_id") or "").strip()
    if not project_id:
        return JSONResponse({"success": False, "message": "project_id is required"}, status_code=400)

    # Validate access: admins can switch to any project, others only their own
    if not _AUTH_AVAILABLE:
        return JSONResponse({"success": False, "message": "Auth not available"}, status_code=503)
    project = get_project(project_id)  # type: ignore[possibly-unbound]
    if not project:
        return JSONResponse({"success": False, "message": "Project not found"}, status_code=404)

    role = (user or {}).get("role", "user")
    if role != "admin":
        accessible = [p["id"] for p in _get_accessible_projects_with_fallback(user)]  # type: ignore[possibly-unbound]
        if project_id not in accessible:
            return JSONResponse({"success": False, "message": "Access denied"}, status_code=403)

    resp = JSONResponse({"success": True, "project_id": project_id, "project_name": project.get("name", project_id)})
    resp.set_cookie("active_project", project_id, httponly=True, max_age=86400 * 7)
    return resp


@app.post("/api/switch-app")
async def api_switch_app(request: Request):
    """Store the active application selection in a session cookie."""
    user = _get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "message": "Not authenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid JSON"}, status_code=400)

    app_id = (body.get("app_id") or "").strip()
    if not app_id:
        return JSONResponse({"success": False, "message": "app_id is required"}, status_code=400)

    resp = JSONResponse({"success": True, "app_id": app_id, "app_name": app_id})
    resp.set_cookie("active_app", app_id, httponly=True, max_age=86400 * 7)
    return resp


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "ui.server:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info",
    )
