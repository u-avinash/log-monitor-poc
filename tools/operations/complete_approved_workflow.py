"""Complete the workflow for approved incidents by generating patch and PR."""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from storage.database import get_db
from storage.incident_repository import IncidentRepository
from agents.nodes.patch_generator import generate_patch_file_node
from agents.nodes.pr_creator import create_pr_node
from agents.state import create_initial_state
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def complete_workflow(incident_id: int):
    """Complete workflow for an approved incident."""
    db = next(get_db())
    repo = IncidentRepository(db)
    
    incident = repo.get_by_id(incident_id)
    if not incident:
        print(f"Incident {incident_id} not found")
        return
    
    print(f"\n{'='*70}")
    print(f"Completing workflow for Incident #{incident_id}")
    print(f"App: {incident.app_name}")
    print(f"Status: {incident.status}")
    print(f"Approval: {incident.approval_status}")
    print(f"{'='*70}\n")
    
    # Recreate state from database
    state = create_initial_state(
        incident_id=str(incident_id),
        app_name=incident.app_name,
        environment=incident.environment,
        error_title=incident.error_title,
        error_description=incident.error_description or "",
        stack_trace=incident.stack_trace or "",
        raw_log=incident.raw_log or "",
        fingerprint=incident.error_fingerprint or "",
        severity=incident.severity or "MEDIUM",
        is_duplicate=False,
        created_at=incident.created_at.isoformat() if incident.created_at else ""
    )
    
    # Add existing data from database
    state['rca_text'] = incident.rca_text
    state['rca_confidence'] = incident.rca_confidence
    state['proposed_fix'] = incident.proposed_fix
    state['fix_explanation'] = incident.fix_explanation
    state['overall_quality_score'] = incident.fix_quality_score or 0.8
    state['pdf_path'] = incident.pdf_path
    state['jira_ticket_key'] = incident.jira_ticket_key
    state['jira_ticket_url'] = incident.jira_ticket_url
    state['approval_status'] = incident.approval_status or 'approved'
    state['repo_full_name'] = incident.repo_full_name
    state['repo_branch'] = getattr(incident, 'repo_branch', None) or 'main'
    state['workflow_completed_steps'] = getattr(incident, 'workflow_completed_steps', []) or []
    
    # Step 1: Generate patch file if missing
    if not incident.patch_path:
        print("[1/2] Generating patch file...")
        try:
            state = generate_patch_file_node(state)
            if state.get('patch_path'):
                print(f"  [OK] Patch generated: {state['patch_path']}")
                repo.update(incident_id=incident_id, patch_path=state['patch_path'])
            else:
                print(f"  [FAIL] Patch generation failed")
        except Exception as e:
            print(f"  [ERROR] Error generating patch: {e}")
            logger.error(f"Patch generation failed: {e}", exc_info=True)
    else:
        print(f"[1/2] Patch already exists: {incident.patch_path}")
        state['patch_path'] = incident.patch_path
    
    # Step 2: Create PR if approved and missing
    if incident.approval_status == 'approved' and not incident.pr_url:
        print("[2/2] Creating GitHub PR...")
        try:
            state = create_pr_node(state)
            if state.get('pr_url'):
                print(f"  [OK] PR created: {state['pr_url']}")
                repo.update(
                    incident_id=incident_id,
                    pr_url=state['pr_url'],
                    pr_number=state.get('pr_number'),
                    fix_branch=state.get('branch_name')
                )
            else:
                print(f"  [FAIL] PR creation failed")
                if state.get('messages'):
                    for msg in state['messages']:
                        if 'failed' in msg.lower() or 'error' in msg.lower():
                            print(f"     {msg}")
        except Exception as e:
            print(f"  [ERROR] Error creating PR: {e}")
            logger.error(f"PR creation failed: {e}", exc_info=True)
    elif incident.pr_url:
        print(f"[2/2] PR already exists: {incident.pr_url}")
    else:
        print(f"[2/2] Skipping PR (not approved or rejected)")
    
    # Update workflow completion
    completed_steps = state.get('workflow_completed_steps', [])
    if 'generate_patch' not in completed_steps and state.get('patch_path'):
        completed_steps.append('generate_patch')
    if 'create_pr' not in completed_steps and state.get('pr_url'):
        completed_steps.append('create_pr')
    
    progress = len(completed_steps) / 11.0
    repo.update(
        incident_id=incident_id,
        workflow_completed_steps=completed_steps,
        workflow_progress_pct=progress,
        current_workflow_node='finalize'
    )
    
    print(f"\n[OK] Workflow completion: {int(progress * 100)}%")
    print(f"[OK] Completed steps: {len(completed_steps)}/11")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    db = next(get_db())
    repo = IncidentRepository(db)
    
    # Find all approved incidents
    incidents = repo.get_all(limit=100)
    approved = [i for i in incidents if i.approval_status == 'approved']
    
    if not approved:
        print("No approved incidents found")
    else:
        print(f"Found {len(approved)} approved incident(s)\n")
        for incident in approved:
            complete_workflow(incident.incident_id)
