"""LangGraph workflow orchestration for incident processing."""
import logging
import time
from typing import Literal, Optional, cast
from datetime import datetime
from langgraph.graph import StateGraph, END

from agents.nodes.patch_generator import _apply_targeted_fix
from agents.state import AgentState, create_initial_state
from agents.nodes import (
    assess_severity_node,
    generate_rca_node,
    generate_fix_node,
    generate_patch_file_node,
    reflect_on_fix_node,
    await_approval_node,
    finalize_node
)
from agents.nodes.pdf_report import generate_pdf_report
from agents.nodes.jira_creator import (
    attach_patch_to_jira,
    create_jira_ticket,
    sync_jira_development_info,
    update_jira_with_branch,
    update_jira_with_commit,
    update_jira_with_pr,
)
from agents.nodes.pr_creator import create_pr_node
from storage.models import Severity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry wrapper for workflow nodes
# ---------------------------------------------------------------------------

def _make_retryable_node(node_fn, node_name: str, max_retries: int = 2, retry_delay_seconds: float = 5.0):
    """
    Wrap a workflow node function with retry-on-error logic.

    If the node sets ``error_message`` in the returned state the call is
    retried up to ``max_retries`` additional times with ``retry_delay_seconds``
    sleep between attempts.  The DB is updated to show a "retrying" banner so
    the UI reflects that the step is being re-attempted.

    After all retries are exhausted the state is returned with
    ``error_message`` still set so that the downstream conditional edge can
    route the workflow to ``finalize`` instead of the next step.
    """
    def wrapped(state: AgentState) -> AgentState:
        total_attempts = max_retries + 1

        for attempt in range(1, total_attempts + 1):
            # On a retry, clear the previous error so the node starts fresh.
            if attempt > 1:
                state["error_message"] = None
                logger.info(
                    "[Workflow Retry] %s — attempt %d/%d for incident %s",
                    node_name, attempt, total_attempts, state.get("incident_id"),
                )
                # Surface the retry in the UI via a DB update.
                try:
                    from storage.database import get_session
                    from storage.incident_repository import IncidentRepository

                    with get_session() as session:
                        repo = IncidentRepository(session)
                        repo.update(
                            incident_id=state["incident_id"],
                            current_workflow_node=f"{node_name}_retry_{attempt}",
                        )
                except Exception:
                    pass

            result_state = node_fn(state)

            if not result_state.get("error_message"):
                # Step succeeded.
                return result_state

            # Step failed.
            if attempt < total_attempts:
                logger.warning(
                    "[Workflow Retry] %s failed (attempt %d/%d) for incident %s: %s — "
                    "retrying in %.0fs ...",
                    node_name, attempt, total_attempts,
                    state.get("incident_id"),
                    result_state.get("error_message"),
                    retry_delay_seconds,
                )
                # Persist a "retrying" marker so the UI can display it.
                try:
                    from storage.database import get_session
                    from storage.incident_repository import IncidentRepository

                    with get_session() as session:
                        repo = IncidentRepository(session)
                        repo.update(
                            incident_id=result_state["incident_id"],
                            current_workflow_node=f"{node_name}_retrying",
                        )
                except Exception:
                    pass

                time.sleep(retry_delay_seconds)
                state = result_state  # carry forward the latest state on the next attempt
            else:
                logger.error(
                    "[Workflow Retry] %s exhausted all %d attempt(s) for incident %s: %s — "
                    "workflow will stop at this step.",
                    node_name, total_attempts,
                    state.get("incident_id"),
                    result_state.get("error_message"),
                )
                return result_state  # error_message still set → conditional edge routes to finalize

        return result_state  # unreachable, but satisfies type-checkers

    wrapped.__name__ = node_fn.__name__
    wrapped.__qualname__ = getattr(node_fn, "__qualname__", node_fn.__name__)
    return wrapped


def _normalize_project_token(value: Optional[str]) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _resolve_project_id_for_incident(
    project_id: Optional[str],
    app_name: Optional[str],
    environment: Optional[str],
) -> Optional[str]:
    if project_id:
        return project_id

    try:
        from storage.auth_store import get_project_config, list_projects

        normalized_app = _normalize_project_token(app_name)
        normalized_env = (environment or "").strip().lower()

        configured_fallback: Optional[str] = None
        matched_projects: list[tuple[dict, bool]] = []

        env_aliases = {
            "prod": {"prod", "production"},
            "production": {"prod", "production"},
            "stage": {"stage", "staging"},
            "staging": {"stage", "staging"},
            "dev": {"dev", "development"},
            "development": {"dev", "development"},
            "qa": {"qa", "test", "testing"},
            "test": {"qa", "test", "testing"},
            "testing": {"qa", "test", "testing"},
        }

        all_projects = list_projects()

        for project in all_projects:
            project_id_candidate = project.get("id")
            project_repo = project.get("repo_url") or ""
            project_app_names = project.get("app_names") or []
            project_env = (project.get("environment") or "").strip().lower()

            candidate_tokens: set[str] = set()

            normalized_project_repo = _normalize_project_token(project_repo)
            if normalized_project_repo:
                candidate_tokens.add(normalized_project_repo)

            normalized_repo_name = _normalize_project_token(
                project_repo.rstrip("/").split("/")[-1] if project_repo else ""
            )
            if normalized_repo_name:
                candidate_tokens.add(normalized_repo_name)

            for app_alias in project_app_names:
                normalized_alias = _normalize_project_token(app_alias)
                if normalized_alias:
                    candidate_tokens.add(normalized_alias)

            project_config = {}
            if project_id_candidate:
                try:
                    project_config = get_project_config(project_id_candidate) or {}
                    if configured_fallback is None:
                        llm_cfg = project_config.get("llm", {}) or {}
                        if llm_cfg.get("api_key") or llm_cfg.get("provider"):
                            configured_fallback = project_id_candidate
                except Exception:
                    project_config = {}

            repo_mappings = project_config.get("repo_mappings") or {}
            if isinstance(repo_mappings, dict):
                for mapped_app, mapping_val in repo_mappings.items():
                    mapped_app_token = _normalize_project_token(mapped_app)
                    if mapped_app_token:
                        candidate_tokens.add(mapped_app_token)

                    mapped_repo = (mapping_val.get("repo") or "") if isinstance(mapping_val, dict) else ""
                    mapped_repo_token = _normalize_project_token(mapped_repo)
                    if mapped_repo_token:
                        candidate_tokens.add(mapped_repo_token)

                    mapped_repo_leaf = _normalize_project_token(mapped_repo.split("/")[-1] if mapped_repo else "")
                    if mapped_repo_leaf:
                        candidate_tokens.add(mapped_repo_leaf)

            candidate_tokens = {token for token in candidate_tokens if token}

            name_matches = (
                not normalized_app
                or any(
                    normalized_app == token
                    or normalized_app in token
                    or token in normalized_app
                    for token in candidate_tokens
                )
            )

            if not name_matches:
                continue

            env_matches = (
                not normalized_env
                or not project_env
                or normalized_env in env_aliases.get(project_env, {project_env})
            )
            matched_projects.append((project, env_matches))

        if matched_projects:
            exact_env_project = next(
                (project for project, env_matches in matched_projects if env_matches),
                None,
            )
            if exact_env_project:
                return exact_env_project.get("id")

            # Environment values are dynamic telemetry attributes and should not
            # block routing once the application mapping is known.
            return matched_projects[0][0].get("id")

        if configured_fallback:
            logger.warning(
                "No project matched app_name='%s'. Using project_id=%s as fallback.",
                app_name, configured_fallback,
            )
            return configured_fallback

        if all_projects:
            fallback_id = all_projects[0].get("id")
            logger.warning(
                "No project matched app_name='%s'. Using first project_id=%s.",
                app_name, fallback_id,
            )
            return fallback_id

        logger.warning("No projects found. Cannot resolve project_id for app='%s'.", app_name)
        return None

    except Exception as exc:
        logger.warning("Failed to resolve project_id for app=%s env=%s: %s", app_name, environment, exc)
        return project_id


# ---------------------------------------------------------------------------
# Helper: fire-and-forget notification at a workflow event
# ---------------------------------------------------------------------------

def _send_event_notification(
    event: str,
    incident_id: str,
    severity: str,
    app_name: str,
    environment: str,
    details: str,
    jira_url: Optional[str] = None,
    project_id: Optional[str] = None,
) -> None:
    """Send a Slack/Teams notification for a specific workflow event (best-effort).

    Webhooks are loaded exclusively from the project's DB config.
    No global/settings fallback — if the project has no webhooks configured,
    notifications are silently skipped.

    Delivery results are persisted on the incident record for UI transparency.
    """
    try:
        from integrations.notification import NotificationClient

        notifier = NotificationClient(project_id=project_id)
        title = f"[{severity}] {event} — {incident_id}"
        message = (
            f"**App:** {app_name}  |  **Env:** {environment}\n"
            f"**Event:** {event}\n\n"
            f"{details}"
        )

        channels_sent = notifier.send_alert(
            title=title,
            message=message,
            severity=severity,
            incident_id=incident_id,
            jira_url=jira_url,
        )

        slack_sent = "slack" in channels_sent
        teams_sent = "teams" in channels_sent

        if slack_sent:
            logger.info("[Notification] Slack alert sent — event='%s' incident=%s", event, incident_id)
        if teams_sent:
            logger.info("[Notification] Teams alert sent — event='%s' incident=%s", event, incident_id)
        if not channels_sent:
            logger.debug("[Notification] No channels delivered for event '%s' (not configured)", event)

        if slack_sent or teams_sent:
            try:
                from storage.database import get_session
                from storage.incident_repository import IncidentRepository

                with get_session() as session:
                    repo = IncidentRepository(session)
                    updates: dict[str, object] = {}
                    if slack_sent:
                        updates["slack_notification_sent"] = True
                    if teams_sent:
                        updates["teams_notification_sent"] = True
                    if updates:
                        repo.update(incident_id=incident_id, **updates)
            except Exception as db_error:
                logger.warning(
                    "[Notification] Failed to persist event delivery status for %s: %s",
                    incident_id, db_error,
                )
    except Exception as exc:
        logger.warning("[Notification] Event '%s' notification failed: %s", event, exc)


# ---------------------------------------------------------------------------
# Workflow nodes
# ---------------------------------------------------------------------------

def send_notifications_node(state: AgentState) -> AgentState:
    """Send notifications via Slack/Teams after fix approval.

    Webhooks are loaded exclusively from the project's DB config.
    No global/settings fallback.
    """
    try:
        from integrations.notification import NotificationClient

        project_id = state.get("project_id")
        notifier = NotificationClient(project_id=project_id)

        severity = state.get("severity", "UNKNOWN")
        incident_id = state.get("incident_id") or ""
        error_title = state.get("error_title", "Unknown Error")
        app_name = state.get("app_name", "Unknown App")
        environment = state.get("environment", "Unknown")
        rca_summary = (
            (state.get("rca_text") or "")[:200] + "..."
            if state.get("rca_text")
            else "RCA pending"
        )
        patch_info = (
            f"Patch generated: {state.get('patch_path', '')}"
            if state.get("patch_path")
            else "Patch: generating..."
        )
        jira_info = (
            f"Jira: {state.get('jira_ticket_key', '')} — {state.get('jira_ticket_url', '')}"
            if state.get("jira_ticket_url")
            else "Jira: pending"
        )

        message = (
            f"**Fix Approved** — post-approval workflow running.\n"
            f"**App:** {app_name}  |  **Env:** {environment}\n\n"
            f"**Root Cause:**\n{rca_summary}\n\n"
            f"{patch_info}\n{jira_info}"
        ).strip()

        completed_steps = list(state.get("workflow_completed_steps") or [])
        state_messages = list(state.get("messages") or [])

        channels_sent = notifier.send_alert(
            title=f"[{severity}] Fix Approved: {error_title}",
            message=message,
            severity=severity,
            incident_id=incident_id,
            jira_url=state.get("jira_ticket_url"),
        )

        notification_status: dict = {
            "slack_sent": "slack" in channels_sent,
            "teams_sent": "teams" in channels_sent,
            "notification_errors": [],
        }

        if "slack" in channels_sent:
            state_messages.append("✓ Slack notification sent")
            logger.info("Slack notification sent for incident %s", incident_id)
        if "teams" in channels_sent:
            state_messages.append("✓ Teams notification sent")
            logger.info("Teams notification sent for incident %s", incident_id)
        if not channels_sent:
            state_messages.append("ℹ️ No notification channels configured for this project")

        state["notification_status"] = notification_status  # type: ignore[typeddict-unknown-key]
        state["messages"] = state_messages

        if "send_notifications" not in completed_steps:
            completed_steps.append("send_notifications")
        state["workflow_completed_steps"] = completed_steps
        state["workflow_progress_pct"] = len(completed_steps) / 11.0

        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository

            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state["incident_id"],
                    current_workflow_node="send_notifications",
                    workflow_completed_steps=state["workflow_completed_steps"],
                    workflow_progress_pct=state["workflow_progress_pct"],
                    slack_notification_sent=notification_status.get("slack_sent", False),
                    teams_notification_sent=notification_status.get("teams_sent", False),
                    notification_errors=notification_status.get("notification_errors", []),
                )
        except Exception as db_error:
            logger.warning("Failed to update workflow progress in DB: %s", db_error)

    except Exception as exc:
        logger.error("Notification node failed: %s", exc)
        msgs = list(state.get("messages") or [])
        msgs.append(f"⚠️ Notification error: {exc}")
        state["messages"] = msgs

    return state


def create_jira_and_pr_node(state: AgentState) -> AgentState:
    """Create Jira ticket, attach patch, create PR, and update Jira."""
    # Step 1: Create Jira ticket
    state = cast(AgentState, create_jira_ticket(state))

    # Step 2: Attach patch to Jira
    if state.get("jira_ticket_key") and state.get("patch_path"):
        state = cast(AgentState, attach_patch_to_jira(state))

    # Step 3: Create GitHub PR if approved
    if state.get("approval_status") == "approved":
        state = create_pr_node(state)

        if state.get("branch_name"):
            state = cast(AgentState, update_jira_with_branch(state))
        if state.get("commit_sha"):
            state = cast(AgentState, update_jira_with_commit(state))
        if state.get("pr_url"):
            state = cast(AgentState, update_jira_with_pr(state))

        # Step 4: Sync branch / commit / PR into the Jira Development panel.
        # This pushes data via the Jira Software DevInfo API (devinfo/0.10)
        # so that the branch, commit, and PR are visible as clickable links
        # inside the Jira ticket's *Development* section.  Falls back to
        # remote web links when the DevInfo API is unavailable.
        if state.get("jira_ticket_key") and state.get("repo_full_name"):
            state = cast(AgentState, sync_jira_development_info(state))

        # Notify about PR creation
        if state.get("pr_url"):
            _send_event_notification(
                event="Pull Request Created",
                incident_id=state.get("incident_id", ""),
                severity=state.get("severity", "HIGH"),
                app_name=state.get("app_name", ""),
                environment=state.get("environment", ""),
                details=(
                    f"PR #{state.get('pr_number', '?')} created on branch "
                    f"`{state.get('fix_branch', '')}`.\nPR URL: {state.get('pr_url', '')}"
                ),
                jira_url=state.get("jira_ticket_url"),
                project_id=state.get("project_id"),
            )

    # Notify about Jira ticket creation
    if state.get("jira_ticket_url"):
        _send_event_notification(
            event="Jira Ticket Created",
            incident_id=state.get("incident_id", ""),
            severity=state.get("severity", "HIGH"),
            app_name=state.get("app_name", ""),
            environment=state.get("environment", ""),
            details=(
                f"Jira ticket {state.get('jira_ticket_key', '')} created.\n"
                f"URL: {state.get('jira_ticket_url', '')}"
            ),
            jira_url=state.get("jira_ticket_url"),
            project_id=state.get("project_id"),
        )

    return state


# ---------------------------------------------------------------------------
# Helper: update incident status in the DB between post-approval steps
# ---------------------------------------------------------------------------

def _update_incident_status(incident_id: str, status: str, current_node: str) -> None:
    """Persist a mid-workflow status change so the UI reflects the active step."""
    try:
        from storage.database import get_session
        from storage.incident_repository import IncidentRepository

        with get_session() as session:
            repo = IncidentRepository(session)
            repo.update(
                incident_id=incident_id,
                status=status,
                current_workflow_node=current_node,
            )
        logger.debug(
            "[Status] %s → status=%s node=%s", incident_id, status, current_node
        )
    except Exception as exc:
        logger.warning(
            "[Status] Failed to update status for %s to %s: %s",
            incident_id, status, exc,
        )


# ---------------------------------------------------------------------------
# Post-approval workflow continuation (called from API after human approval)
# ---------------------------------------------------------------------------

def run_post_approval_workflow(incident_id: str, project_id: Optional[str] = None) -> AgentState:
    """
    Resume the workflow for an approved incident running only post-approval steps:
      1. Patch generation (skipped if patch was already generated pre-approval)
      2. Notifications (fix-approved Slack/Teams alert)
      3. Jira creation + PR creation
      4. Finalize

    Patch files are now generated before approval so the reviewer can test the
    changes.  If ``patch_path`` is already set on the incident record the patch
    step is skipped here to avoid overwriting an existing patch.

    The full LangGraph graph is NOT re-run from the beginning.
    This function is called from the API endpoint after the user clicks 'Approve'.

    Args:
        incident_id: The incident to process.
        project_id: Optional project context override.

    Returns:
        Final AgentState after post-approval processing.
    """
    from storage.database import get_session
    from storage.incident_repository import IncidentRepository

    logger.info("[Post-Approval] Starting post-approval workflow for incident %s", incident_id)

    with get_session() as session:
        repo = IncidentRepository(session)
        incident = repo.get_by_id(incident_id)
        if not incident:
            raise ValueError(f"Incident not found: {incident_id}")

        # Load ALL attributes inside the session to avoid DetachedInstanceError
        _approval_status = getattr(incident, "approval_status", None)
        _app_name = str(incident.app_name or "")
        _environment = str(incident.environment or "")
        _error_title = str(incident.error_title or "")
        _error_description = str(incident.error_description or "")
        _stack_trace = str(incident.stack_trace or "")
        _raw_log = str(incident.raw_log or "")
        _fingerprint = str(getattr(incident, "error_fingerprint", "") or "")
        _severity = str(incident.severity or "HIGH")
        _is_duplicate = bool(getattr(incident, "is_duplicate", False))
        _metadata = getattr(incident, "incident_metadata", None)
        _rca_text = getattr(incident, "rca_text", None)
        _repo_full_name = getattr(incident, "repo_full_name", None)
        _error_file_path = getattr(incident, "error_file_path", None)
        _error_line_number = getattr(incident, "error_line_number", None)
        _fetched_code = getattr(incident, "fetched_code", None)
        _proposed_fix = getattr(incident, "proposed_fix", None)
        _fix_explanation = getattr(incident, "fix_explanation", None)
        _approval_notes = getattr(incident, "approval_notes", None)
        _approved_at = str(getattr(incident, "approved_at", "") or "")
        _fix_branch = getattr(incident, "fix_branch", None)
        _pr_url = getattr(incident, "pr_url", None)
        _pr_number = getattr(incident, "pr_number", None)
        _commit_sha = getattr(incident, "commit_sha", None)
        _commit_url = getattr(incident, "commit_url", None)
        _jira_ticket_key = getattr(incident, "jira_ticket_key", None)
        _jira_ticket_url = getattr(incident, "jira_ticket_url", None)
        _pdf_path = getattr(incident, "pdf_path", None)
        _patch_path = getattr(incident, "patch_path", None)
        _slack_notified = bool(getattr(incident, "slack_notification_sent", False))
        _workflow_completed_steps = list(getattr(incident, "workflow_completed_steps", None) or [])
        _workflow_progress_pct = float(getattr(incident, "workflow_progress_pct", 0.0) or 0.0)
        _created_at = str(getattr(incident, "created_at", "") or datetime.utcnow().isoformat())

    if _approval_status != "approved":
        raise ValueError(
            f"Incident {incident_id} is not approved "
            f"(approval_status={_approval_status})"
        )

    resolved_project_id = _resolve_project_id_for_incident(
        project_id,
        _app_name,
        _environment,
    )

    fixed_file_content = None
    if _fetched_code and _proposed_fix:
        try:
            fixed_file_content = _apply_targeted_fix(_fetched_code, _proposed_fix, cast(AgentState, {}))
            if fixed_file_content is None:
                # Exact / normalised matching failed — try the same fuzzy fallback
                # that generate_patch_file_node uses so PR creation is not skipped
                # when the LLM produced slightly mismatched context lines.
                import re as _re
                from agents.nodes.patch_generator import _apply_block_to_file_fuzzy

                _code_blocks = _re.findall(
                    r'```(?:\w+)?\n(.*?)\n```', _proposed_fix, _re.DOTALL
                )
                if len(_code_blocks) >= 2:
                    fixed_file_content = _apply_block_to_file_fuzzy(
                        _fetched_code,
                        _code_blocks[0].strip(),
                        _code_blocks[1].strip(),
                    )
                    if fixed_file_content:
                        logger.info(
                            "[Post-Approval] Fixed file content reconstructed via "
                            "fuzzy fallback for incident %s",
                            incident_id,
                        )
                    else:
                        logger.warning(
                            "[Post-Approval] Fuzzy fallback also failed for incident %s; "
                            "PR creation will be skipped",
                            incident_id,
                        )
        except Exception as exc:
            logger.warning(
                "[Post-Approval] Failed to reconstruct fixed file content for incident %s: %s",
                incident_id,
                exc,
            )

    # Reconstruct AgentState from the database record so all nodes have context.
    state: AgentState = {  # type: ignore[assignment]
        "incident_id": incident_id,
        "project_id": resolved_project_id,
        "app_name": _app_name,
        "environment": _environment,
        "error_title": _error_title,
        "error_description": _error_description,
        "stack_trace": _stack_trace,
        "raw_log": _raw_log,
        "fingerprint": _fingerprint,
        "severity": _severity,
        "is_duplicate": _is_duplicate,
        "metadata": _metadata,
        # Analysis results from earlier phases
        "rca_text": _rca_text,
        "rca_confidence": None,
        "repo_full_name": _repo_full_name,
        "error_file_path": _error_file_path,
        "error_line_number": _error_line_number,
        "error_file_type": None,
        "original_code": _fetched_code,
        "fix_type": None,
        "proposed_fix": _proposed_fix,
        "fix_explanation": _fix_explanation,
        "affected_files": None,
        # Quality scores (not needed for post-approval)
        "correctness_score": None,
        "safety_score": None,
        "code_quality_score": None,
        "completeness_score": None,
        "overall_quality_score": None,
        "quality_concerns": None,
        "quality_recommendation": None,
        "reflection_failed": None,
        # Approval — force to 'approved' so downstream nodes proceed
        "requires_approval": True,
        "approval_status": "approved",
        "approval_notes": _approval_notes,
        "approved_by": None,
        "approved_at": _approved_at,
        # Preserve any already-created integration artefacts
        "github_branch": _fix_branch,
        "github_pr_url": _pr_url,
        "github_pr_number": _pr_number,
        "jira_ticket_key": _jira_ticket_key,
        "jira_ticket_url": _jira_ticket_url,
        "pdf_path": _pdf_path,
        "patch_path": _patch_path,
        "fixed_file_content": fixed_file_content,
        "fix_branch": _fix_branch,
        "branch_name": _fix_branch,
        "pr_url": _pr_url,
        "pr_number": _pr_number,
        "commit_sha": _commit_sha,
        "commit_url": _commit_url,
        # Notifications
        "slack_notified": _slack_notified,
        "email_notified": False,
        # Workflow control
        "current_node": "post_approval",
        "workflow_completed_steps": _workflow_completed_steps,
        "workflow_progress_pct": _workflow_progress_pct,
        "error_message": None,
        "messages": [f"▶️ Resuming post-approval workflow for incident {incident_id}"],
        "created_at": _created_at,
        "updated_at": datetime.utcnow().isoformat(),
        "completed_at": None,
    }

    # Run post-approval steps sequentially (all node functions are synchronous).

    # Step 1: Patch generation — skip if the patch was already generated pre-approval.
    # The LangGraph workflow now runs generate_patch_file_node before await_approval so
    # that reviewers can download and test the patch before making their decision.
    if state.get("patch_path"):
        logger.info(
            "[Post-Approval] Step 1/3 — Patch already generated pre-approval (%s), skipping",
            state.get("patch_path"),
        )
    else:
        logger.info("[Post-Approval] Step 1/3 — Patch generation (fallback: not generated pre-approval)")
        _update_incident_status(incident_id, "PATCH_GENERATING", "generate_patch_file")
        state = generate_patch_file_node(state)

        if state.get("patch_path"):
            _send_event_notification(
                event="Patch Generated",
                incident_id=incident_id,
                severity=str(state.get("severity", "HIGH")),
                app_name=str(state.get("app_name", "")),
                environment=str(state.get("environment", "")),
                details=f"Patch file created: {state.get('patch_path', '')}",
                project_id=resolved_project_id,
            )

    logger.info("[Post-Approval] Step 2/3 — Notifications")
    state = send_notifications_node(state)

    logger.info("[Post-Approval] Step 3/3 — Jira & PR creation")
    _update_incident_status(incident_id, "CREATING_JIRA_PR", "create_jira_pr")
    state = create_jira_and_pr_node(state)

    logger.info("[Post-Approval] Step 4/4 — Finalize")
    state = finalize_node(state)

    logger.info("[Post-Approval] Workflow complete for incident %s", incident_id)
    return state


# ---------------------------------------------------------------------------
# LangGraph workflow graph definition
# ---------------------------------------------------------------------------

def create_agent_workflow() -> StateGraph:
    """
    Create the LangGraph workflow for incident processing.

    Workflow:
    1. assess_severity     → Determine if auto-fix needed
    2. generate_rca        → Create Root Cause Analysis      [retryable, stops on failure]
    3. generate_fix        → Generate code fix               [retryable, stops on failure]
    4. generate_pdf        → Generate RCA PDF report         [retryable, stops on failure]
    5. reflect             → Quality assessment              (uses safe defaults on failure)
    6. generate_patch_file → Generate .patch file            [retryable, stops on failure]
    7. await_approval      → Human review (workflow pauses here; patch already available)
    8. send_notifications  → Slack/Teams alerts
    9. create_jira_pr      → Create Jira ticket and GitHub PR
    10. finalize           → Update database and complete

    After human approval, run_post_approval_workflow() continues from step 8.
    Patch generation is skipped in run_post_approval_workflow() when the patch
    was already created in step 6.

    Failure handling:
    - Steps 2-4 and 6 are wrapped with _make_retryable_node() which retries up to
      2 times (with a 5-second delay) before giving up.
    - If all retries are exhausted the node sets ``error_message`` in state and
      conditional routing diverts the workflow directly to ``finalize`` so that
      no further steps are executed on bad data.
    - The ``finalize`` node detects ``error_message`` and persists status=FAILED.

    Returns:
        Compiled LangGraph workflow
    """
    workflow = StateGraph(AgentState)

    # Register nodes — LLM-calling nodes are wrapped with retry-on-error logic.
    workflow.add_node("assess_severity", assess_severity_node)
    workflow.add_node(
        "generate_rca",
        _make_retryable_node(generate_rca_node, "generate_rca", max_retries=2, retry_delay_seconds=5.0),
    )
    workflow.add_node(
        "generate_fix",
        _make_retryable_node(generate_fix_node, "generate_fix", max_retries=2, retry_delay_seconds=5.0),
    )
    workflow.add_node(
        "generate_pdf",
        _make_retryable_node(generate_pdf_report, "generate_pdf", max_retries=1, retry_delay_seconds=5.0),
    )
    workflow.add_node("reflect", reflect_on_fix_node)  # uses safe defaults on failure — no retry needed
    workflow.add_node(
        "generate_patch_file",
        _make_retryable_node(generate_patch_file_node, "generate_patch_file", max_retries=1, retry_delay_seconds=3.0),
    )
    workflow.add_node("await_approval", await_approval_node)
    workflow.add_node("send_notifications", send_notifications_node)
    workflow.add_node("create_jira_pr", create_jira_and_pr_node)
    workflow.add_node("finalize", finalize_node)

    workflow.set_entry_point("assess_severity")

    # --- assess_severity → generate_rca / finalize ---
    def should_generate_rca(state: AgentState) -> Literal["generate_rca", "finalize"]:
        severity = state.get("severity", "LOW")
        is_duplicate = state.get("is_duplicate", False)
        if is_duplicate:
            logger.info("Skipping RCA for duplicate incident %s", state["incident_id"])
            return "finalize"
        if severity in ["HIGH", "CRITICAL"]:
            logger.info("Proceeding to RCA for %s incident %s", severity, state["incident_id"])
            return "generate_rca"
        logger.info("Skipping RCA for %s incident %s", severity, state["incident_id"])
        return "finalize"

    workflow.add_conditional_edges(
        "assess_severity",
        should_generate_rca,
        {"generate_rca": "generate_rca", "finalize": "finalize"},
    )

    # --- generate_rca → generate_fix  (stop on failure) ---
    def route_after_rca(state: AgentState) -> Literal["generate_fix", "finalize"]:
        if state.get("error_message"):
            logger.error(
                "[Workflow] RCA failed after retries for incident %s — stopping workflow: %s",
                state.get("incident_id"), state.get("error_message"),
            )
            return "finalize"
        return "generate_fix"

    workflow.add_conditional_edges(
        "generate_rca",
        route_after_rca,
        {"generate_fix": "generate_fix", "finalize": "finalize"},
    )

    # --- generate_fix → generate_pdf  (stop on failure) ---
    def route_after_fix(state: AgentState) -> Literal["generate_pdf", "finalize"]:
        if state.get("error_message"):
            logger.error(
                "[Workflow] Fix generation failed after retries for incident %s — stopping workflow: %s",
                state.get("incident_id"), state.get("error_message"),
            )
            return "finalize"
        return "generate_pdf"

    workflow.add_conditional_edges(
        "generate_fix",
        route_after_fix,
        {"generate_pdf": "generate_pdf", "finalize": "finalize"},
    )

    # --- generate_pdf → reflect  (stop on failure) ---
    def route_after_pdf(state: AgentState) -> Literal["reflect", "finalize"]:
        if state.get("error_message"):
            logger.error(
                "[Workflow] PDF generation failed after retries for incident %s — stopping workflow: %s",
                state.get("incident_id"), state.get("error_message"),
            )
            return "finalize"
        return "reflect"

    workflow.add_conditional_edges(
        "generate_pdf",
        route_after_pdf,
        {"reflect": "reflect", "finalize": "finalize"},
    )

    # --- reflect → generate_patch_file  (reflect never truly fails — uses defaults) ---
    workflow.add_edge("reflect", "generate_patch_file")

    # --- generate_patch_file → await_approval  (stop on failure) ---
    def route_after_patch(state: AgentState) -> Literal["await_approval", "finalize"]:
        if state.get("error_message"):
            logger.error(
                "[Workflow] Patch generation failed after retries for incident %s — stopping workflow: %s",
                state.get("incident_id"), state.get("error_message"),
            )
            return "finalize"
        return "await_approval"

    workflow.add_conditional_edges(
        "generate_patch_file",
        route_after_patch,
        {"await_approval": "await_approval", "finalize": "finalize"},
    )

    # --- await_approval → send_notifications / finalize ---
    def should_continue_after_approval(
        state: AgentState,
    ) -> Literal["send_notifications", "finalize"]:
        approval_status = state.get("approval_status", "pending")
        if approval_status == "pending":
            logger.info("Workflow paused at approval for incident %s", state["incident_id"])
            return "finalize"
        logger.info(
            "Approval decision made: %s — continuing workflow for %s",
            approval_status,
            state["incident_id"],
        )
        return "send_notifications"

    workflow.add_conditional_edges(
        "await_approval",
        should_continue_after_approval,
        {"send_notifications": "send_notifications", "finalize": "finalize"},
    )

    workflow.add_edge("send_notifications", "create_jira_pr")
    workflow.add_edge("create_jira_pr", "finalize")
    workflow.add_edge("finalize", END)

    app = workflow.compile()
    logger.info("Agent workflow compiled successfully")
    return app


# ---------------------------------------------------------------------------
# Async / sync entry points
# ---------------------------------------------------------------------------

async def run_incident_workflow(
    incident_id: str,
    app_name: str,
    environment: str,
    error_title: str,
    error_description: str,
    stack_trace: str,
    raw_log: str,
    fingerprint: str,
    severity: str,
    is_duplicate: bool,
    metadata: Optional[dict] = None,
    project_id: Optional[str] = None,
) -> AgentState:
    """Run the complete incident processing workflow (async)."""
    logger.info("Starting workflow for incident %s (severity: %s)", incident_id, severity)

    resolved_project_id = _resolve_project_id_for_incident(project_id, app_name, environment)

    initial_state = create_initial_state(
        incident_id=incident_id,
        app_name=app_name,
        environment=environment,
        error_title=error_title,
        error_description=error_description,
        stack_trace=stack_trace,
        raw_log=raw_log,
        fingerprint=fingerprint,
        severity=severity,
        is_duplicate=is_duplicate,
        created_at=datetime.utcnow().isoformat(),
        metadata=metadata,
        project_id=resolved_project_id,
    )

    workflow_app = create_agent_workflow()

    try:
        final_state = await workflow_app.ainvoke(initial_state)
        logger.info("Workflow completed for incident %s", incident_id)
        return final_state
    except Exception as exc:
        logger.error("Workflow failed for incident %s: %s", incident_id, exc)
        initial_state["error_message"] = f"Workflow execution failed: {exc}"
        initial_state["messages"] = [f"❌ Workflow failed: {exc}"]
        return initial_state


def run_incident_workflow_sync(
    incident_id: str,
    app_name: str,
    environment: str,
    error_title: str,
    error_description: str,
    stack_trace: str,
    raw_log: str,
    fingerprint: str,
    severity: str,
    is_duplicate: bool,
    metadata: Optional[dict] = None,
    project_id: Optional[str] = None,
) -> AgentState:
    """Synchronous wrapper around run_incident_workflow."""
    import asyncio

    coro = run_incident_workflow(
        incident_id=incident_id,
        app_name=app_name,
        environment=environment,
        error_title=error_title,
        error_description=error_description,
        stack_trace=stack_trace,
        raw_log=raw_log,
        fingerprint=fingerprint,
        severity=severity,
        is_duplicate=is_duplicate,
        metadata=metadata,
        project_id=project_id,
    )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError("Loop already running — use run_incident_workflow() directly.")
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def run_workflow_for_incident(incident_id: str, project_id: Optional[str] = None) -> AgentState:
    """Load incident data from storage and run the full workflow synchronously.

    Guard: if the incident has already been approved, completed, or has a PR
    created, the full workflow is NOT re-run from scratch (doing so would
    overwrite the approval decision and reset the status to PENDING_APPROVAL).
    Use run_post_approval_workflow() instead to resume post-approval steps.
    """
    from storage.database import get_session
    from storage.incident_repository import IncidentRepository

    # Terminal / protected statuses that must not be re-run from scratch
    _PROTECTED_STATUSES = {
        "APPROVED", "PR_CREATED", "COMPLETED", "JIRA_CREATED", "REJECTED",
    }
    _PROTECTED_APPROVAL_STATUSES = {"approved", "rejected"}

    with get_session() as session:
        repo = IncidentRepository(session)
        incident = repo.get_by_id(incident_id)
        if not incident:
            raise ValueError(f"Incident not found: {incident_id}")

        current_status = str(getattr(incident, "status", "") or "").upper()
        current_approval = str(getattr(incident, "approval_status", "") or "").lower()

        if current_status in _PROTECTED_STATUSES or current_approval in _PROTECTED_APPROVAL_STATUSES:
            logger.warning(
                "[Workflow] Re-trigger blocked for incident %s — "
                "status=%s approval_status=%s. "
                "Use run_post_approval_workflow() to continue post-approval steps.",
                incident_id, current_status, current_approval,
            )
            raise ValueError(
                f"Incident {incident_id} is in a protected state "
                f"(status={current_status}, approval_status={current_approval}). "
                "Re-triggering the full workflow would erase the approval decision. "
                "Use the post-approval workflow endpoint instead."
            )

        resolved_project_id = _resolve_project_id_for_incident(
            project_id,
            str(incident.app_name or ""),
            str(incident.environment or ""),
        )

        if resolved_project_id and getattr(incident, "incident_metadata", None) is not None:
            metadata = dict(getattr(incident, "incident_metadata", None) or {})
            metadata.setdefault("project_id", resolved_project_id)
        else:
            metadata = getattr(incident, "incident_metadata", None)

        return run_incident_workflow_sync(
            incident_id=str(incident.incident_id),
            app_name=str(incident.app_name or ""),
            environment=str(incident.environment or ""),
            error_title=str(incident.error_title or ""),
            error_description=str(incident.error_description or ""),
            stack_trace=str(incident.stack_trace or ""),
            raw_log=str(incident.raw_log or ""),
            fingerprint=str(getattr(incident, "error_fingerprint", "") or ""),
            severity=str(incident.severity or Severity.HIGH.value),
            is_duplicate=bool(getattr(incident, "is_duplicate", False)),
            metadata=metadata,
            project_id=resolved_project_id,
        )
