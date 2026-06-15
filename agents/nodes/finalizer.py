"""Workflow finalization node."""
import logging
from datetime import datetime
from agents.state import AgentState
from storage.database import get_session
from storage.incident_repository import IncidentRepository
from storage.models import IncidentStatus

logger = logging.getLogger(__name__)


def finalize_node(state: AgentState) -> AgentState:
    """
    Finalize the workflow and update database.
    
    This node:
    1. Updates incident with all agent outputs
    2. Sets final status based on approval decision
    3. Records completion timestamp
    4. Triggers notifications if configured
    
    Args:
        state: Current agent state
        
    Returns:
        Final updated state
    """
    logger.info(f"[Finalizer] Finalizing incident {state['incident_id']}")
    
    try:
        # Determine final status
        approval_status = state.get('approval_status', 'pending')
        error_message = state.get('error_message')

        # If a workflow step failed (error_message set) and the incident was not
        # explicitly approved or rejected, mark it FAILED and stop here.
        if error_message and approval_status not in ('approved', 'rejected'):
            final_status = 'FAILED'
            status_msg = f"❌ Workflow stopped due to step failure: {error_message}"
            logger.error(
                "[Finalizer] Incident %s marked FAILED — %s",
                state['incident_id'], error_message,
            )

            completed_steps = list(state.get('workflow_completed_steps') or [])
            state['workflow_completed_steps'] = completed_steps
            state['workflow_progress_pct'] = min(len(completed_steps) / 11.0, 1.0)

            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state['incident_id'],
                    status=final_status,
                    current_workflow_node='failed',
                    workflow_completed_steps=completed_steps,
                    workflow_progress_pct=state['workflow_progress_pct'],
                    rca_text=state.get('rca_text'),
                    updated_at=datetime.utcnow(),
                )

            state['current_node'] = 'failed'
            state['completed_at'] = datetime.utcnow().isoformat()
            state['updated_at'] = state['completed_at']
            state['messages'] = state.get('messages', []) + [
                status_msg,
                f"✓ Incident status set to FAILED at {state['completed_at']}",
            ]
            logger.info("[Finalizer] Incident %s finalized with status: FAILED", state['incident_id'])
            return state

        if approval_status == 'approved':
            # Set terminal status based on what was actually accomplished
            if state.get('pr_url'):
                final_status = 'PR_CREATED'
                status_msg = "✅ Pull request created successfully"
            else:
                # PR was not created — determine the most accurate terminal status.
                #
                # Case 1: GitHub is simply not configured for this project.
                #   → COMPLETED is correct; the workflow finished everything it could.
                #
                # Case 2: GitHub IS configured but PR creation was skipped/failed
                #   (missing fixed_file_content, missing repo, API error, etc.).
                #   → Use JIRA_CREATED when a Jira ticket exists so the UI clearly
                #     shows the workflow stalled before PR creation.
                #   → Fall back to COMPLETED when there is no Jira ticket either.
                github_not_configured = bool(state.get('pr_github_not_configured'))
                if github_not_configured:
                    final_status = 'COMPLETED'
                    status_msg = "✅ Post-approval workflow completed (GitHub not configured)"
                elif state.get('jira_ticket_key'):
                    final_status = 'JIRA_CREATED'
                    status_msg = "⚠️ Jira ticket created but PR could not be created"
                    logger.warning(
                        "[Finalizer] Incident %s approved but PR was not created — "
                        "check fixed_file_content, repo_full_name, and error_file_path in state",
                        state['incident_id']
                    )
                else:
                    final_status = 'COMPLETED'
                    status_msg = "✅ Post-approval workflow completed"
        elif approval_status == 'rejected':
            final_status = 'REJECTED'
            status_msg = "❌ Fix rejected by human reviewer"
        else:
            # Workflow paused - awaiting approval decision
            final_status = 'PENDING_APPROVAL'
            status_msg = "⏸️ Awaiting human approval"
        
        # Update workflow tracking
        completed_steps = list(state.get('workflow_completed_steps') or [])

        # Only mark finalize as complete when the workflow truly finishes.
        # When pausing for human approval (PENDING_APPROVAL), do NOT add finalize
        # to completed_steps — the workflow has not actually completed yet and
        # showing it as complete while intermediate steps are pending is misleading.
        if final_status != 'PENDING_APPROVAL':
            if 'finalize' not in completed_steps:
                completed_steps.append('finalize')
        state['workflow_completed_steps'] = completed_steps

        # Calculate progress from actual completed steps only.
        total_workflow_steps = 11.0
        state['workflow_progress_pct'] = min(len(completed_steps) / total_workflow_steps, 1.0)

        # Finalization should not force 100% when intermediate steps are still pending.
        workflow_progress = state['workflow_progress_pct']
        
        # Map terminal status to the most informative current_workflow_node value.
        # For intermediate statuses (waiting for approval, stalled at PR creation),
        # "finalize" is misleading — show the actual step the workflow is blocked at.
        _node_for_status = {
            'PENDING_APPROVAL': 'await_approval',   # waiting for human decision
            'JIRA_CREATED':     'create_pr',         # stalled before PR creation
            'REJECTED':         'finalize',
            'COMPLETED':        'finalize',
            'PR_CREATED':       'finalize',
        }
        current_workflow_node_val = _node_for_status.get(final_status, 'finalize')

        # Update database with all workflow outputs
        with get_session() as session:
            repo = IncidentRepository(session)
            
            update_data = {
                'status': final_status,
                'rca_text': state.get('rca_text'),
                'proposed_fix': state.get('proposed_fix'),
                'fix_explanation': state.get('fix_explanation'),
                'current_workflow_node': current_workflow_node_val,
                'workflow_completed_steps': state['workflow_completed_steps'],
                'workflow_progress_pct': workflow_progress,
                'updated_at': datetime.utcnow()
            }
            
            # Add notification status if available
            notification_status = state.get('notification_status', {})
            if notification_status:
                update_data['slack_notification_sent'] = notification_status.get('slack_sent', False)
                update_data['teams_notification_sent'] = notification_status.get('teams_sent', False)
                update_data['notification_errors'] = notification_status.get('notification_errors', [])
            
            # Add Jira/PR info if available
            if state.get('jira_ticket_key'):
                update_data['jira_ticket_key'] = state.get('jira_ticket_key')
                update_data['jira_ticket_url'] = state.get('jira_ticket_url')
            if state.get('jira_error'):
                update_data['jira_error'] = state.get('jira_error')
            
            if state.get('pr_url'):
                update_data['pr_url'] = state.get('pr_url')
                update_data['pr_number'] = state.get('pr_number')
            
            # Always persist approval info so the DB reflects the current decision.
            # approved_by may be None when called from run_post_approval_workflow
            # (state is reconstructed without a username), but approval_status and
            # approved_at are always available and must be saved.
            update_data['approval_status'] = approval_status
            update_data['approval_notes'] = state.get('approval_notes')
            if state.get('approved_by'):
                update_data['approved_by'] = state.get('approved_by')
            approved_at = state.get('approved_at')
            if approved_at:
                try:
                    update_data['approved_at'] = datetime.fromisoformat(approved_at)
                except (ValueError, TypeError):
                    pass
            
            # Add PDF and patch paths if available
            if state.get('pdf_path'):
                update_data['pdf_path'] = state.get('pdf_path')
            if state.get('patch_path'):
                update_data['patch_path'] = state.get('patch_path')
            
            repo.update(
                incident_id=state['incident_id'],
                **update_data
            )
        
        # Update state — use the same status-aware node value
        state['current_node'] = current_workflow_node_val
        state['completed_at'] = datetime.utcnow().isoformat()
        state['updated_at'] = state['completed_at']
        state['messages'] = state.get('messages', []) + [
            status_msg,
            f"✓ Workflow completed at {state['completed_at']}"
        ]
        
        logger.info(f"[Finalizer] Completed {state['incident_id']} with status: {final_status}")
        
        # Notification status is managed by send_notifications_node (post-approval flow).
        # Only send here if no prior notification was sent (non-approval / pending paths).
        prior_notification = state.get('notification_status') or {}  # type: ignore[typeddict-item]
        already_sent_slack = bool(prior_notification.get('slack_sent', False))
        already_sent_teams = bool(prior_notification.get('teams_sent', False))

        if not already_sent_slack and not already_sent_teams:
            notification_status = {
                'slack_sent': False,
                'teams_sent': False,
                'notification_errors': []
            }

            try:
                from integrations.notification import NotificationClient
                notifier = NotificationClient(project_id=state.get("project_id"))

                severity = state.get('severity', 'MEDIUM')
                app_name = state.get('app_name', 'Unknown')
                environment = state.get('environment', 'Unknown')
                error_title = state.get('error_title', 'Unknown Error')
                jira_url = state.get('jira_ticket_url')

                message = (
                    f"*Application:* {app_name}\n"
                    f"*Environment:* {environment}\n"
                    f"*Status:* {final_status}\n\n"
                    f"*Error:* {error_title}\n\n"
                    f"*RCA Available:* {'Yes' if state.get('rca_text') else 'No'}\n"
                    f"*Fix Generated:* {'Yes' if state.get('proposed_fix') else 'No'}"
                )

                channels = notifier.send_alert(
                    title=f"Incident #{state['incident_id']} - {app_name}",
                    message=message,
                    severity=severity,
                    incident_id=None,
                    jira_url=jira_url
                )

                if 'slack' in channels:
                    notification_status['slack_sent'] = True
                if 'teams' in channels:
                    notification_status['teams_sent'] = True

                if channels:
                    logger.info(f"Notifications sent to: {', '.join(channels)}")
                    state['messages'].append(f"✓ Notifications sent to: {', '.join(channels)}")

                state['notification_status'] = notification_status  # type: ignore[typeddict-unknown-key]

                # Persist notification outcome only if we attempted delivery here
                try:
                    with get_session() as session:
                        repo = IncidentRepository(session)
                        repo.update(
                            incident_id=state['incident_id'],
                            slack_notification_sent=notification_status['slack_sent'],
                            teams_notification_sent=notification_status['teams_sent'],
                            notification_errors=notification_status['notification_errors']
                        )
                except Exception as db_err:
                    logger.error(f"Failed to update notification status in DB: {db_err}")

            except Exception as notif_error:
                logger.warning(f"Failed to send notifications in finalizer: {notif_error}")
        else:
            logger.info(
                "[Finalizer] Skipping own notification — already sent by send_notifications_node "
                "(slack=%s, teams=%s)", already_sent_slack, already_sent_teams
            )
        
    except Exception as e:
        logger.error(f"[Finalizer] Failed for {state['incident_id']}: {str(e)}")
        state['error_message'] = f"Finalization failed: {str(e)}"
        state['messages'] = state.get('messages', []) + [
            f"⚠️ Finalization failed: {str(e)}"
        ]
    
    return state
