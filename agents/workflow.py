"""LangGraph workflow orchestration for incident processing."""
import logging
from typing import Literal, Optional
from datetime import datetime
from langgraph.graph import StateGraph, END
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
    update_jira_with_branch,
    update_jira_with_commit,
    update_jira_with_pr,
)
from agents.nodes.pr_creator import create_pr_node
from integrations.github_client import GitHubClient
from config.settings import get_settings
from storage.models import Severity

logger = logging.getLogger(__name__)


def send_notifications_node(state: AgentState) -> AgentState:
    """Send notifications via Slack/Teams for incident."""
    settings = get_settings()
    
    try:
        from integrations.notification import send_slack_alert, send_teams_alert
        
        notification_status = {
            'slack_sent': False,
            'teams_sent': False,
            'notification_errors': []
        }
        
        # Prepare notification message
        severity = state.get('severity', 'UNKNOWN')
        incident_id = state.get('incident_id', 'N/A')
        error_title = state.get('error_title', 'Unknown Error')
        app_name = state.get('app_name', 'Unknown App')
        environment = state.get('environment', 'Unknown')
        rca_summary = state.get('rca_text', 'RCA in progress...')[:200] + '...' if state.get('rca_text') else 'RCA pending'
        
        # Send Slack notification
        if settings.slack_webhook_url:
            try:
                slack_success = send_slack_alert(
                    incident_id=incident_id,
                    title=f"[{severity}] {error_title}",
                    message=f"**App:** {app_name}\n**Env:** {environment}\n\n{rca_summary}",
                    severity=severity,
                    jira_url=state.get('jira_ticket_url'),
                    app_name=app_name,
                    environment=environment
                )
                notification_status['slack_sent'] = slack_success
                if slack_success:
                    state['messages'].append(f"✓ Slack notification sent")
                    logger.info(f"Slack notification sent for incident {incident_id}")
                else:
                    notification_status['notification_errors'].append("Slack notification failed")
            except Exception as e:
                logger.warning(f"Failed to send Slack notification: {e}")
                notification_status['notification_errors'].append(f"Slack: {str(e)}")
        
        # Send Teams notification
        if settings.teams_webhook_url:
            try:
                teams_success = send_teams_alert(
                    incident_id=incident_id,
                    title=f"[{severity}] {error_title}",
                    message=f"**App:** {app_name}\n**Env:** {environment}\n\n{rca_summary}",
                    severity=severity,
                    jira_url=state.get('jira_ticket_url'),
                    app_name=app_name,
                    environment=environment
                )
                notification_status['teams_sent'] = teams_success
                if teams_success:
                    state['messages'].append(f"✓ Teams notification sent")
                    logger.info(f"Teams notification sent for incident {incident_id}")
            except Exception as e:
                logger.warning(f"Failed to send Teams notification: {e}")
                notification_status['notification_errors'].append(f"Teams: {str(e)}")
        
        # Update state with notification status
        state['notification_status'] = notification_status
        
        if not notification_status['slack_sent'] and not notification_status['teams_sent']:
            if not settings.slack_webhook_url and not settings.teams_webhook_url:
                state['messages'].append("ℹ️ No notification channels configured")
            else:
                state['messages'].append("⚠️ Notification delivery failed")
        
        # Update workflow tracking
        if 'workflow_completed_steps' not in state:
            state['workflow_completed_steps'] = []
        
        # Add step only if not already completed (prevent duplicates)
        if 'send_notifications' not in state['workflow_completed_steps']:
            state['workflow_completed_steps'].append('send_notifications')
        
        # Calculate progress based on 11 total workflow steps
        state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
        
        # Update database with workflow progress
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state['incident_id'],
                    current_workflow_node='send_notifications',
                    workflow_completed_steps=state['workflow_completed_steps'],
                    workflow_progress_pct=state['workflow_progress_pct']
                )
        except Exception as db_error:
            logger.warning(f"Failed to update workflow progress in DB: {db_error}")
        
    except Exception as e:
        logger.error(f"Notification node failed: {e}")
        state['messages'].append(f"⚠️ Notification error: {str(e)}")
    
    return state


def create_jira_and_pr_node(state: AgentState) -> AgentState:
    """Create Jira ticket, attach patch, create PR, and update Jira."""
    settings = get_settings()
    
    # Step 1: Create Jira ticket
    state = create_jira_ticket(state)
    
    # Step 2: Generate patch file
    state = generate_patch_file_node(state)
    
    # Step 3: Attach patch to Jira
    if state.get('jira_ticket_key') and state.get('patch_path'):
        state = attach_patch_to_jira(state)
    
    # Step 4: Create GitHub PR if approved
    if state.get('approval_status') == 'approved':
        state = create_pr_node(state)
        
        # Step 5: Update Jira with branch info
        if state.get('branch_name'):
            state = update_jira_with_branch(state)

        # Step 6: Update Jira with commit details
        if state.get('commit_sha'):
            state = update_jira_with_commit(state)
        
        # Step 7: Update Jira with PR details
        if state.get('pr_url'):
            state = update_jira_with_pr(state)
    
    return state


def create_agent_workflow() -> StateGraph:
    """
    Create the LangGraph workflow for incident processing.
    
    Workflow:
    1. assess_severity → Determine if auto-fix needed
    2. generate_rca → Create Root Cause Analysis
    3. generate_fix → Generate code fix
    4. reflect → Quality assessment
    5. await_approval → Human review (conditional)
    6. create_jira_pr → Create Jira ticket and GitHub PR
    7. finalize → Update database and complete
    
    Returns:
        Compiled LangGraph workflow
    """
    # Create state graph
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("assess_severity", assess_severity_node)
    workflow.add_node("generate_rca", generate_rca_node)
    workflow.add_node("generate_fix", generate_fix_node)
    workflow.add_node("generate_pdf", generate_pdf_report)
    workflow.add_node("reflect", reflect_on_fix_node)
    workflow.add_node("await_approval", await_approval_node)
    workflow.add_node("send_notifications", send_notifications_node)
    workflow.add_node("create_jira_pr", create_jira_and_pr_node)
    workflow.add_node("finalize", finalize_node)
    
    # Set entry point
    workflow.set_entry_point("assess_severity")
    
    # Define conditional routing after severity assessment
    def should_generate_rca(state: AgentState) -> Literal["generate_rca", "finalize"]:
        """
        Decide if RCA generation should proceed based on severity.
        
        HIGH and CRITICAL errors → generate RCA
        MEDIUM and LOW → skip to finalize (just log)
        """
        severity = state.get('severity', 'LOW')
        is_duplicate = state.get('is_duplicate', False)
        
        # Skip RCA for duplicates (already have RCA from original)
        if is_duplicate:
            logger.info(f"Skipping RCA for duplicate incident {state['incident_id']}")
            return "finalize"
        
        # Only generate RCA for HIGH and CRITICAL
        if severity in ['HIGH', 'CRITICAL']:
            logger.info(f"Proceeding to RCA for {severity} incident {state['incident_id']}")
            return "generate_rca"
        else:
            logger.info(f"Skipping RCA for {severity} incident {state['incident_id']}")
            return "finalize"
    
    workflow.add_conditional_edges(
        "assess_severity",
        should_generate_rca,
        {
            "generate_rca": "generate_rca",
            "finalize": "finalize"
        }
    )
    
    # RCA → Fix generation (always proceed if RCA was generated)
    workflow.add_edge("generate_rca", "generate_fix")
    
    # Fix → PDF generation (always generate PDF after fix)
    workflow.add_edge("generate_fix", "generate_pdf")
    
    # PDF → Reflection (always proceed)
    workflow.add_edge("generate_pdf", "reflect")
    
    # Reflect → Always go to await_approval (the approval node handles auto-approval internally)
    workflow.add_edge("reflect", "await_approval")
    
    # Define conditional routing after approval
    def should_continue_after_approval(state: AgentState) -> Literal["send_notifications", "finalize"]:
        """
        Decide if workflow should continue after approval node.
        
        If approval is still pending → finalize (pause workflow)
        If approved/rejected → continue to notifications
        """
        approval_status = state.get('approval_status', 'pending')
        
        if approval_status == 'pending':
            logger.info(f"Workflow paused at approval for incident {state['incident_id']}")
            return "finalize"
        else:
            logger.info(f"Approval decision made: {approval_status} - continuing workflow")
            return "send_notifications"
    
    workflow.add_conditional_edges(
        "await_approval",
        should_continue_after_approval,
        {
            "send_notifications": "send_notifications",
            "finalize": "finalize"
        }
    )
    
    # Send Notifications → Create Jira/PR (only if approved)
    workflow.add_edge("send_notifications", "create_jira_pr")
    
    # Create Jira/PR → Finalize
    workflow.add_edge("create_jira_pr", "finalize")
    
    # Finalize → END
    workflow.add_edge("finalize", END)
    
    # Compile workflow
    app = workflow.compile()
    
    logger.info("Agent workflow compiled successfully")
    return app


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
    metadata: Optional[dict] = None
) -> AgentState:
    """
    Run the complete incident processing workflow.
    
    Args:
        incident_id: Unique incident identifier
        app_name: Application name
        environment: Environment (production, staging, etc.)
        error_title: Error title/message
        error_description: Detailed error description
        stack_trace: Stack trace
        raw_log: Raw log content
        fingerprint: Error fingerprint for deduplication
        severity: Severity level (CRITICAL, HIGH, MEDIUM, LOW)
        is_duplicate: Whether this is a duplicate error
        metadata: OTLP metadata including custom attributes
        
    Returns:
        Final AgentState after workflow completion
    """
    logger.info(f"Starting workflow for incident {incident_id} (severity: {severity})")
    
    # Create initial state
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
        metadata=metadata
    )
    
    # Create and run workflow
    workflow_app = create_agent_workflow()
    
    try:
        # Execute workflow
        final_state = await workflow_app.ainvoke(initial_state)
        
        logger.info(f"Workflow completed for incident {incident_id}")
        logger.info(f"Final status: {final_state.get('approval_status', 'unknown')}")
        logger.info(f"Messages: {final_state.get('messages', [])}")
        
        return final_state
        
    except Exception as e:
        logger.error(f"Workflow failed for incident {incident_id}: {str(e)}")
        initial_state['error_message'] = f"Workflow execution failed: {str(e)}"
        initial_state['messages'] = [f"❌ Workflow failed: {str(e)}"]
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
    metadata: Optional[dict] = None
) -> AgentState:
    """
    Synchronous version of run_incident_workflow.
    
    This is a convenience wrapper for sync contexts.
    For async contexts, use run_incident_workflow() directly.
    
    Args:
        Same as run_incident_workflow
        
    Returns:
        Final AgentState after workflow completion
    """
    import asyncio
    
    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is already running, create task
            raise RuntimeError("Cannot run sync version in async context - use run_incident_workflow() instead")
        else:
            # Run in existing loop
            return loop.run_until_complete(
                run_incident_workflow(
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
                    metadata=metadata
                )
            )
    except RuntimeError:
        # No event loop, create new one
        return asyncio.run(
            run_incident_workflow(
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
                metadata=metadata
            )
        )
