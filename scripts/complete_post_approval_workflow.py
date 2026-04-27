"""Complete post-approval workflow for approved incidents."""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.database import get_db
from storage.incident_repository import IncidentRepository
from agents.nodes.patch_generator import generate_patch_file_node
from agents.nodes.pr_creator import create_pr_node
from agents.state import AgentState
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def complete_workflow_for_approved():
    """Complete workflow for all approved incidents."""
    db = next(get_db())
    repo = IncidentRepository(db)
    
    # Get all approved incidents
    incidents = repo.get_all(limit=1000)
    approved_incidents = [inc for inc in incidents if inc.approval_status == 'approved']
    
    if not approved_incidents:
        logger.info("No approved incidents found")
        return
    
    logger.info(f"Found {len(approved_incidents)} approved incident(s)\n")
    
    for incident in approved_incidents:
        logger.info(f"{'='*70}")
        logger.info(f"Processing Incident #{incident.incident_id}")
        logger.info(f"App: {incident.app_name}")
        logger.info(f"Status: {incident.status}")
        logger.info(f"{'='*70}\n")
        
        # Check if patch already exists
        if incident.patch_path:
            logger.info(f"✓ Patch already exists: {incident.patch_path}")
        else:
            logger.info("[STEP 1] Generating patch...")
            try:
                # Create state dict
                state = {
                    'incident_id': incident.incident_id,
                    'app_name': incident.app_name,
                    'environment': incident.environment,
                    'error_title': incident.error_title,
                    'error_description': incident.error_description,
                    'stack_trace': incident.stack_trace,
                    'raw_log': incident.raw_log,
                    'severity': incident.severity,
                    'rca_text': incident.rca_text,
                    'proposed_fix': incident.proposed_fix,
                    'fix_explanation': incident.fix_explanation,
                    'repo_full_name': incident.repo_full_name,
                    'error_file_path': incident.error_file_path,
                    'error_line_number': incident.error_line_number,
                    'original_code': incident.fetched_code,
                    'approval_status': incident.approval_status
                }
                
                # Generate patch
                result_state = generate_patch_file_node(state)
                
                # Update incident
                incident.patch_path = result_state.get("patch_path")
                incident.status = "patch_generated"
                db.commit()
                
                logger.info(f"✓ Patch generated: {incident.patch_path}\n")
                
            except Exception as e:
                logger.error(f"✗ Patch generation failed: {str(e)}\n")
                continue
        
        # Check if PR already exists
        if incident.pr_url:
            logger.info(f"✓ PR already exists: {incident.pr_url}")
        else:
            logger.info("[STEP 2] Creating Pull Request...")
            try:
                # Create state dict
                state = {
                    'incident_id': incident.incident_id,
                    'app_name': incident.app_name,
                    'environment': incident.environment,
                    'error_title': incident.error_title,
                    'error_description': incident.error_description,
                    'stack_trace': incident.stack_trace,
                    'raw_log': incident.raw_log,
                    'severity': incident.severity,
                    'rca_text': incident.rca_text,
                    'proposed_fix': incident.proposed_fix,
                    'fix_explanation': incident.fix_explanation,
                    'repo_full_name': incident.repo_full_name,
                    'error_file_path': incident.error_file_path,
                    'error_line_number': incident.error_line_number,
                    'error_file_type': incident.error_file_type,
                    'patch_path': incident.patch_path,
                    'approval_status': incident.approval_status,
                    'approved_by': incident.approved_by,
                    'jira_ticket_url': incident.jira_ticket_url
                }
                
                # Create PR
                result_state = create_pr_node(state)
                
                # Update incident
                incident.pr_url = result_state.get("pr_url")
                incident.pr_number = result_state.get("pr_number")
                incident.fix_branch = result_state.get("fix_branch")
                incident.status = "pr_created"
                incident.workflow_progress_pct = 100.0
                db.commit()
                
                logger.info(f"✓ PR created: {incident.pr_url}\n")
                
            except Exception as e:
                logger.error(f"✗ PR creation failed: {str(e)}\n")
                continue
        
        logger.info(f"✓ Workflow completed for incident #{incident.incident_id}\n")
    
    logger.info(f"{'='*70}")
    logger.info("All approved incidents processed")
    logger.info(f"{'='*70}\n")


if __name__ == "__main__":
    complete_workflow_for_approved()
