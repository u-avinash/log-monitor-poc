"""Jira ticket creation node for agent workflow."""
import logging
from typing import Any, Dict
from pathlib import Path
from agents.state import AgentState
from integrations.jira_client import JiraClient
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def create_jira_ticket(state: AgentState) -> Dict[str, Any]:
    """
    Create Jira ticket for incident.
    
    This node:
    1. Initializes Jira client
    2. Creates ticket with incident details
    3. Includes RCA and proposed fix if available
    4. Links to PR if created
    5. Updates state with ticket info
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state with Jira ticket info
    """
    logger.info(f"Creating Jira ticket for incident {state.get('incident_id')}")
    
    try:
        # Initialize Jira client
        jira_client = JiraClient()
        
        if not jira_client.client:
            logger.warning("Jira client not configured, skipping ticket creation")
            state["messages"].append({
                "role": "system",
                "content": "Jira ticket creation skipped (not configured)"
            })
            return state
        
        # Extract incident details
        incident_id = state.get("incident_id", 0)
        app_name = state.get("app_name", "Unknown")
        environment = state.get("environment", "Unknown")
        error_title = state.get("error_title", "Unknown Error")
        error_description = state.get("error_description", "")
        severity = state.get("severity", "MEDIUM")
        stack_trace = state.get("stack_trace")
        rca_text = state.get("rca_text")
        proposed_fix = state.get("proposed_fix")
        pr_url = state.get("pr_url")
        
        # Create ticket
        ticket_info = jira_client.create_incident_ticket(
            incident_id=incident_id,
            app_name=app_name,
            environment=environment,
            error_title=error_title,
            error_description=error_description,
            severity=severity,
            stack_trace=stack_trace,
            rca_text=rca_text,
            proposed_fix=proposed_fix,
            pr_url=pr_url
        )
        
        if ticket_info:
            # Update state with ticket info
            state["jira_ticket_key"] = ticket_info["ticket_key"]
            state["jira_ticket_url"] = ticket_info["ticket_url"]
            state["jira_issue_type"] = "Bug"
            state["jira_error"] = None  # Clear any previous errors
            
            # Update workflow tracking
            if 'workflow_completed_steps' not in state:
                state['workflow_completed_steps'] = []

            # Add step only if not already completed (prevent duplicates)
            # Use 'create_jira' instead of legacy 'create_jira_pr'
            if 'create_jira' not in state['workflow_completed_steps']:
                state['workflow_completed_steps'].append('create_jira')
            
            # Calculate progress based on 11 total workflow steps
            state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
            
            logger.info(f"Created Jira ticket {ticket_info['ticket_key']} for incident {incident_id}")
            
            # Update database with workflow progress
            try:
                from storage.database import get_session
                from storage.incident_repository import IncidentRepository
                
                with get_session() as session:
                    repo = IncidentRepository(session)
                    repo.update(
                        incident_id=incident_id,
                        current_workflow_node='create_jira',
                        workflow_completed_steps=state['workflow_completed_steps'],
                        workflow_progress_pct=state['workflow_progress_pct'],
                        jira_ticket_key=ticket_info["ticket_key"],
                        jira_ticket_url=ticket_info["ticket_url"]
                    )
            except Exception as db_error:
                logger.warning(f"Failed to update workflow progress in DB: {db_error}")
            
            state["messages"].append({
                "role": "system",
                "content": f"✓ Jira ticket created: {ticket_info['ticket_key']} - {ticket_info['ticket_url']}"
            })
        else:
            error_msg = "Failed to create Jira ticket - check credentials and project permissions"
            logger.error(f"{error_msg} for incident {incident_id}")
            state["jira_error"] = error_msg
            
            # Update workflow tracking even on failure
            try:
                from storage.database import get_session
                from storage.incident_repository import IncidentRepository
                
                with get_session() as session:
                    repo = IncidentRepository(session)
                    repo.update(
                        incident_id=incident_id,
                        current_workflow_node='create_jira_failed',
                        workflow_progress_pct=state.get('workflow_progress_pct', 0.0)
                    )
            except Exception as db_error:
                logger.warning(f"Failed to update failure status in DB: {db_error}")
            
            state["messages"].append({
                "role": "system",
                "content": f"❌ {error_msg}"
            })
        
        return state
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error creating Jira ticket: {error_msg}")
        state["jira_error"] = error_msg
        state["messages"].append({
            "role": "system",
            "content": f"❌ Jira error: {error_msg}"
        })
        return state


def update_jira_with_rca(state: AgentState) -> Dict[str, Any]:
    """
    Update existing Jira ticket with RCA findings.
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state
    """
    logger.info(f"Updating Jira ticket with RCA for incident {state.get('incident_id')}")
    
    try:
        ticket_key = state.get("jira_ticket_key")
        rca_text = state.get("rca_text")
        rca_confidence = state.get("rca_confidence")
        
        if not ticket_key or not rca_text:
            logger.warning("Missing ticket key or RCA text, skipping update")
            return state
        
        # Initialize Jira client
        jira_client = JiraClient()
        
        if not jira_client.client:
            logger.warning("Jira client not configured")
            return state
        
        # Add RCA comment
        success = jira_client.add_rca_update(
            ticket_key=ticket_key,
            rca_text=rca_text,
            confidence=rca_confidence
        )
        
        if success:
            logger.info(f"Updated Jira ticket {ticket_key} with RCA")
            state["messages"].append({
                "role": "system",
                "content": f"Added RCA to Jira ticket {ticket_key}"
            })
        
        return state
        
    except Exception as e:
        logger.error(f"Error updating Jira with RCA: {str(e)}")
        return state


def attach_patch_to_jira(state: AgentState) -> Dict[str, Any]:
    """
    Attach patch file to Jira ticket.
    
    Args:
        state: Current agent state with patch_path
        
    Returns:
        Updated state
    """
    logger.info(f"Attaching patch file to Jira ticket for incident {state.get('incident_id')}")
    
    try:
        ticket_key = state.get("jira_ticket_key")
        patch_path = state.get("patch_path")
        
        if not ticket_key:
            logger.warning("Missing ticket key, skipping patch attachment")
            return state
        
        if not patch_path or not Path(patch_path).exists():
            logger.warning(f"Patch file not found: {patch_path}")
            return state
        
        # Initialize Jira client
        jira_client = JiraClient()
        
        if not jira_client.client:
            logger.warning("Jira client not configured")
            return state
        
        # Attach patch file
        try:
            # The Jira API add_attachment expects: (issue, attachment)
            # Pass the file path directly, not a file object
            jira_client.client.add_attachment(ticket_key, patch_path)
            
            logger.info(f"Attached patch file to Jira ticket {ticket_key}")
            state["messages"].append({
                "role": "system",
                "content": f"✓ Patch file attached to Jira ticket {ticket_key}"
            })
        except Exception as attach_error:
            logger.error(f"Failed to attach patch: {attach_error}")
            state["messages"].append({
                "role": "system",
                "content": f"⚠️ Failed to attach patch: {str(attach_error)}"
            })
        
        return state
        
    except Exception as e:
        logger.error(f"Error attaching patch to Jira: {str(e)}")
        return state


def update_jira_with_branch(state: AgentState) -> Dict[str, Any]:
    """
    Update Jira ticket with branch information.
    
    Args:
        state: Current agent state with branch_name
        
    Returns:
        Updated state
    """
    logger.info(f"Updating Jira ticket with branch info for incident {state.get('incident_id')}")
    
    try:
        ticket_key = state.get("jira_ticket_key")
        branch_name = state.get("branch_name")
        
        if not ticket_key or not branch_name:
            logger.warning("Missing ticket key or branch name, skipping update")
            return state
        
        # Initialize Jira client
        jira_client = JiraClient()
        
        if not jira_client.client:
            logger.warning("Jira client not configured")
            return state
        
        # Add comment with branch information
        comment_text = f"""🌿 *Fix Branch Created*

Branch: `{branch_name}`

To checkout this branch:
{{code}}
git fetch origin
git checkout {branch_name}
{{code}}

The fix has been committed to this branch and is ready for review."""
        
        try:
            jira_client.add_comment(ticket_key, comment_text)
            logger.info(f"Updated Jira ticket {ticket_key} with branch info")
            state["messages"].append({
                "role": "system",
                "content": f"✓ Branch info added to Jira ticket {ticket_key}"
            })
        except Exception as comment_error:
            logger.error(f"Failed to add branch comment: {comment_error}")
        
        return state
        
    except Exception as e:
        logger.error(f"Error updating Jira with branch: {str(e)}")
        return state


def update_jira_with_commit(state: AgentState) -> Dict[str, Any]:
    """
    Update Jira ticket with commit details.

    Args:
        state: Current agent state with commit information

    Returns:
        Updated state
    """
    logger.info(f"Updating Jira ticket with commit details for incident {state.get('incident_id')}")

    try:
        ticket_key = state.get("jira_ticket_key")
        commit_sha = state.get("commit_sha")
        commit_url = state.get("commit_url")
        branch_name = state.get("branch_name")
        repo_full_name = state.get("repo_full_name")

        if not ticket_key or not commit_sha:
            logger.warning("Missing ticket key or commit SHA, skipping update")
            return state

        # Initialize Jira client
        jira_client = JiraClient()

        if not jira_client.client:
            logger.warning("Jira client not configured")
            return state

        short_sha = str(commit_sha)[:7]
        commit_link = commit_url or f"(sha: {commit_sha})"

        comment_text = f"""✅ *Commit Created*

Repository: `{repo_full_name or 'N/A'}`
Branch: `{branch_name or 'N/A'}`
Commit: `{short_sha}`

{f'[View Commit|{commit_url}]' if commit_url else f'SHA: {commit_sha}'}

*Notes:*
- Commit message includes the Jira key (if available) to populate the Development panel.
- If Jira↔GitHub integration is enabled, this should appear under *Development → Commits*."""

        try:
            jira_client.add_comment(ticket_key, comment_text)
            logger.info(f"Updated Jira ticket {ticket_key} with commit details")
            state["messages"].append({
                "role": "system",
                "content": f"✓ Commit details added to Jira ticket {ticket_key}"
            })
        except Exception as comment_error:
            logger.error(f"Failed to add commit comment: {comment_error}")

        return state

    except Exception as e:
        logger.error(f"Error updating Jira with commit: {str(e)}")
        return state


def update_jira_with_pr(state: AgentState) -> Dict[str, Any]:
    """
    Update Jira ticket with PR details.
    
    Args:
        state: Current agent state with PR information
        
    Returns:
        Updated state
    """
    logger.info(f"Updating Jira ticket with PR details for incident {state.get('incident_id')}")
    
    try:
        ticket_key = state.get("jira_ticket_key")
        pr_url = state.get("pr_url")
        pr_number = state.get("pr_number")
        branch_name = state.get("branch_name")
        
        if not ticket_key or not pr_url:
            logger.warning("Missing ticket key or PR URL, skipping update")
            return state
        
        # Initialize Jira client
        jira_client = JiraClient()
        
        if not jira_client.client:
            logger.warning("Jira client not configured")
            return state
        
        # Add comment with PR details
        comment_text = f"""🔀 *Pull Request Created*

PR #{pr_number}: {pr_url}
Branch: `{branch_name}`

*Action Required for Developer:*
1. Review the automated fix in the PR
2. Run tests locally to verify the fix
3. Check for any security concerns
4. Approve and merge if satisfied
5. Update this ticket once deployed

The PR includes:
- Root cause analysis
- Proposed code fix
- Patch file (attached)
- Automated tests (if applicable)

Please review at your earliest convenience."""
        
        try:
            jira_client.add_comment(ticket_key, comment_text)
            
            # Also try to create a web link
            try:
                jira_client.client.create_issue_link(
                    type="relates to",
                    inwardIssue=ticket_key,
                    outwardIssue=pr_url,
                    comment={
                        "body": f"Automated fix PR: {pr_url}"
                    }
                )
            except:
                pass  # Link creation might fail if not supported
            
            logger.info(f"Updated Jira ticket {ticket_key} with PR details")
            state["messages"].append({
                "role": "system",
                "content": f"✓ PR details added to Jira ticket {ticket_key}"
            })
        except Exception as comment_error:
            logger.error(f"Failed to add PR comment: {comment_error}")
        
        return state
        
    except Exception as e:
        logger.error(f"Error updating Jira with PR: {str(e)}")
        return state


def link_jira_to_pr(state: AgentState) -> Dict[str, Any]:
    """
    Link Jira ticket to GitHub PR (legacy function - use update_jira_with_pr).
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state
    """
    return update_jira_with_pr(state)
