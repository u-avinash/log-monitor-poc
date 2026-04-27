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
        
        if approval_status == 'approved':
            final_status = 'fix_approved'
            status_msg = "✅ Fix approved - ready for implementation"
        elif approval_status == 'rejected':
            final_status = 'fix_rejected'
            status_msg = "❌ Fix rejected by human reviewer"
        else:
            # Workflow paused - awaiting approval decision
            final_status = 'pending_approval'
            status_msg = "⏸️ Awaiting human approval"
        
        # Update workflow tracking
        if 'workflow_completed_steps' not in state:
            state['workflow_completed_steps'] = []
        
        # Add step only if not already completed (prevent duplicates)
        if 'finalize' not in state['workflow_completed_steps']:
            state['workflow_completed_steps'].append('finalize')
        
        # Calculate progress based on 11 total workflow steps
        state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
        
        # Determine if workflow is truly complete or paused
        is_workflow_complete = approval_status in ['approved', 'rejected']
        if not is_workflow_complete and approval_status == 'pending':
            # Workflow is paused, don't set to 100%
            workflow_progress = state['workflow_progress_pct']
        else:
            # Workflow is complete
            workflow_progress = 1.0
        
        # Update database with all workflow outputs
        with get_session() as session:
            repo = IncidentRepository(session)
            
            update_data = {
                'status': final_status,
                'rca': state.get('rca_text'),
                'proposed_fix': state.get('proposed_fix'),
                'fix_explanation': state.get('fix_explanation'),
                'current_workflow_node': 'finalize',
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
            
            # Add approval info if available
            if state.get('approved_by'):
                update_data['approval_status'] = approval_status
                update_data['approval_notes'] = state.get('approval_notes')
                update_data['approved_by'] = state.get('approved_by')
                update_data['approved_at'] = datetime.fromisoformat(state['approved_at']) if state.get('approved_at') else None
            
            # Add PDF and patch paths if available
            if state.get('pdf_path'):
                update_data['pdf_path'] = state.get('pdf_path')
            if state.get('patch_path'):
                update_data['patch_path'] = state.get('patch_path')
            
            repo.update(
                incident_id=state['incident_id'],
                **update_data
            )
        
        # Update state
        state['current_node'] = 'finalize'
        state['completed_at'] = datetime.utcnow().isoformat()
        state['updated_at'] = state['completed_at']
        state['messages'] = state.get('messages', []) + [
            status_msg,
            f"✓ Workflow completed at {state['completed_at']}"
        ]
        
        logger.info(f"[Finalizer] Completed {state['incident_id']} with status: {final_status}")
        
        # Send Slack notification and track status
        notification_status = {
            'slack_sent': False,
            'teams_sent': False,
            'notification_errors': []
        }
        
        try:
            from integrations.notification import NotificationClient
            notifier = NotificationClient()
            
            # Build notification message
            severity = state.get('severity', 'MEDIUM')
            app_name = state.get('app_name', 'Unknown')
            environment = state.get('environment', 'Unknown')
            error_title = state.get('error_title', 'Unknown Error')
            jira_url = state.get('jira_ticket_url')
            
            message = f"""
*Application:* {app_name}
*Environment:* {environment}
*Status:* {final_status}

*Error:* {error_title}

*RCA Available:* {'Yes' if state.get('rca_text') else 'No'}
*Fix Generated:* {'Yes' if state.get('proposed_fix') else 'No'}
"""
            
            channels = notifier.send_alert(
                title=f"Incident #{state['incident_id']} - {app_name}",
                message=message,
                severity=severity,
                incident_id=state['incident_id'],
                jira_url=jira_url
            )
            
            # Update notification status based on successful channels
            if 'slack' in channels:
                notification_status['slack_sent'] = True
            if 'teams' in channels:
                notification_status['teams_sent'] = True
            
            if channels:
                logger.info(f"Notifications sent to: {', '.join(channels)}")
                state['messages'].append(f"✓ Notifications sent to: {', '.join(channels)}")
                
                # Store notification status in state for database update
                state['notification_status'] = notification_status
                
                # Update database with notification status
                with get_session() as session:
                    repo = IncidentRepository(session)
                    repo.update(
                        incident_id=state['incident_id'],
                        slack_notification_sent=notification_status['slack_sent'],
                        teams_notification_sent=notification_status['teams_sent'],
                        notification_errors=notification_status['notification_errors']
                    )
            else:
                notification_status['notification_errors'].append("No channels configured or all failed")
                state['notification_status'] = notification_status
                
        except Exception as notif_error:
            logger.warning(f"Failed to send notifications: {notif_error}")
            notification_status['notification_errors'].append(str(notif_error))
            state['notification_status'] = notification_status
            
            # Update database with error
            try:
                with get_session() as session:
                    repo = IncidentRepository(session)
                    repo.update(
                        incident_id=state['incident_id'],
                        slack_notification_sent=False,
                        teams_notification_sent=False,
                        notification_errors=notification_status['notification_errors']
                    )
            except Exception as db_error:
                logger.error(f"Failed to update notification status in DB: {db_error}")
        
    except Exception as e:
        logger.error(f"[Finalizer] Failed for {state['incident_id']}: {str(e)}")
        state['error_message'] = f"Finalization failed: {str(e)}"
        state['messages'] = state.get('messages', []) + [
            f"⚠️ Finalization failed: {str(e)}"
        ]
    
    return state
