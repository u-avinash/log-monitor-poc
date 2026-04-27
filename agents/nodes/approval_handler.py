"""Human approval workflow handler node."""
import logging
from datetime import datetime
import time
from agents.state import AgentState
from storage.database import get_session
from storage.incident_repository import IncidentRepository

logger = logging.getLogger(__name__)


def await_approval_node(state: AgentState) -> AgentState:
    """
    Handle human approval workflow.
    
    This node:
    1. Sets incident status to "pending_approval" in database
    2. For POC: Returns immediately (approval handled via API)
    3. In production: Would poll database or wait for webhook
    
    The actual approval/rejection is handled via:
    - Dashboard UI "Approve/Reject" buttons
    - API endpoint POST /incidents/{id}/approve
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state with approval status
    """
    logger.info(f"[Approval Handler] Awaiting approval for incident {state['incident_id']}")
    
    try:
        # Update database status to pending_approval
        with get_session() as session:
            repo = IncidentRepository(session)
            update_data = {
                'status': 'pending_approval',
                'rca': state.get('rca_text'),
                'proposed_fix': state.get('proposed_fix'),
                'fix_explanation': state.get('fix_explanation')
            }
            
            # Ensure PDF path is persisted if it was generated
            if state.get('pdf_path'):
                update_data['pdf_path'] = state.get('pdf_path')
                logger.info(f"[Approval Handler] Saving PDF path to database: {state.get('pdf_path')}")
            
            # Ensure patch path is persisted if it was generated
            if state.get('patch_path'):
                update_data['patch_path'] = state.get('patch_path')
            
            repo.update(incident_id=state['incident_id'], **update_data)
        
        # For POC: Mark as needing approval and pause workflow
        # In production with LangGraph checkpointing, this would create a breakpoint
        state['approval_status'] = 'pending'
        state['current_node'] = 'await_approval'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        # Update workflow tracking
        if 'workflow_completed_steps' not in state:
            state['workflow_completed_steps'] = []
        
        # Add step only if not already completed (prevent duplicates)
        if 'await_approval' not in state['workflow_completed_steps']:
            state['workflow_completed_steps'].append('await_approval')
        
        # Calculate progress based on 11 total workflow steps
        state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
        
        # Update database with workflow progress
        try:
            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state['incident_id'],
                    current_workflow_node='await_approval',
                    workflow_completed_steps=state['workflow_completed_steps'],
                    workflow_progress_pct=state['workflow_progress_pct']
                )
        except Exception as db_error:
            logger.warning(f"Failed to update workflow progress in DB: {db_error}")
        
        state['messages'] = state.get('messages', []) + [
            f"⏸️ Awaiting human approval (quality score: {state.get('overall_quality_score', 0):.2f})",
            f"Requires approval: {state['requires_approval']}"
        ]
        
        logger.info(f"[Approval Handler] Incident {state['incident_id']} awaiting approval")
        
        # Note: In POC, workflow pauses here
        # Continuation happens when user calls POST /incidents/{id}/approve
        # which will trigger the next phase of the workflow
        
    except Exception as e:
        logger.error(f"[Approval Handler] Failed for {state['incident_id']}: {str(e)}")
        state['error_message'] = f"Approval handler failed: {str(e)}"
        state['messages'] = state.get('messages', []) + [
            f"Approval handler failed: {str(e)}"
        ]
    
    return state


def check_approval_status(incident_id: str) -> tuple[bool, str, str]:
    """
    Check if incident has been approved/rejected.
    
    This is called by the workflow to check if approval was granted.
    
    Args:
        incident_id: Incident ID to check
        
    Returns:
        Tuple of (has_decision, decision, notes)
        - has_decision: True if approved or rejected
        - decision: "approved" or "rejected"
        - notes: User-provided notes
    """
    try:
        with get_session() as session:
            repo = IncidentRepository(session)
            incident = repo.get_incident(incident_id)
            
            if not incident:
                return False, "pending", "Incident not found"
            
            # Check approval_status field
            approval_status = incident.approval_status
            approval_notes = incident.approval_notes or ""
            
            if approval_status in ['approved', 'rejected']:
                return True, approval_status, approval_notes
            else:
                return False, 'pending', ""
                
    except Exception as e:
        logger.error(f"Error checking approval status: {str(e)}")
        return False, 'pending', f"Error: {str(e)}"
