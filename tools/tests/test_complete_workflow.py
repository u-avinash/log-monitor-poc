"""
Complete Workflow Test - End to End
Tests the full flow: Error -> RCA -> Fix -> Approval -> Patch -> PR
"""
import time
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from storage.database import get_db, Incident
from sqlalchemy.orm import Session


def check_incident_details(db: Session, incident_id: int) -> dict:
    """Check detailed status of an incident"""
    incident = db.query(Incident).filter(Incident.incident_id == incident_id).first()
    
    if not incident:
        return {"error": "Incident not found"}
    
    return {
        "id": incident.incident_id,
        "app_name": incident.app_name,
        "status": incident.status,
        "approval_status": incident.approval_status,
        "has_rca": bool(incident.rca_text),
        "has_proposed_fix": bool(incident.proposed_fix),
        "has_original_code": bool(incident.error_file_path),
        "file_path": incident.error_file_path,
        "repo_name": incident.repo_full_name,
        "has_patch_file": bool(incident.patch_path),
        "pr_url": incident.pr_url,
        "jira_ticket": incident.jira_ticket_key,
        "current_node": incident.current_workflow_node,
        "workflow_progress": incident.workflow_progress_pct
    }


def print_section(title: str):
    """Print formatted section header"""
    print(f"\n{'='*80}")
    print(f"{title:^80}")
    print(f"{'='*80}\n")


def print_incident_status(details: dict):
    """Print incident status in formatted way"""
    print(f"Incident #{details['id']}: {details['app_name']}")
    print(f"  Status: {details['status']}")
    print(f"  Approval: {details['approval_status'] or 'Not set'}")
    print(f"  Progress: {details['workflow_progress']}%")
    print(f"  Current Node: {details['current_node']}")
    print(f"\n  GitHub Integration:")
    print(f"    * RCA Generated: {details['has_rca']}")
    print(f"    * Fix Proposed: {details['has_proposed_fix']}")
    print(f"    * Original Code: {details['has_original_code']}")
    print(f"    * File Path: {details['file_path'] or 'N/A'}")
    print(f"    * Repository: {details['repo_name'] or 'N/A'}")
    print(f"\n  Post-Approval:")
    print(f"    * Patch File: {details['has_patch_file']}")
    print(f"    * GitHub PR: {details['pr_url'] or 'N/A'}")
    print(f"    * Jira Ticket: {details['jira_ticket'] or 'N/A'}")


def test_workflow():
    """Test complete workflow"""
    print_section("COMPLETE WORKFLOW TEST")
    
    db = next(get_db())
    
    # Get all incidents
    incidents = db.query(Incident).order_by(Incident.incident_id.desc()).limit(5).all()
    
    if not incidents:
        print("[ERROR] No incidents found!")
        print("\nPlease generate test incidents through the MuleSoft error generator services.")
        print("See individual service READMEs for details on triggering errors.")
        return False
    
    print(f"Found {len(incidents)} incident(s)\n")
    
    # Check each incident
    all_ready_for_approval = True
    for incident in incidents:
        details = check_incident_details(db, incident.incident_id)
        print_incident_status(details)
        print("\n" + "-"*80)
        
        # Check if ready for approval
        if not details['has_proposed_fix']:
            all_ready_for_approval = False
            print(f"  [WAIT] Incident #{incident.incident_id} not ready for approval yet")
        elif details['status'] == 'pending_approval':
            print(f"  [READY] Incident #{incident.incident_id} ready for approval!")
        elif details['approval_status'] == 'approved':
            print(f"  [APPROVED] Incident #{incident.incident_id} approved!")
            
            # Check post-approval steps
            if details['has_patch_file']:
                print(f"     [OK] Patch file generated")
            else:
                print(f"     [MISSING] Patch file not generated")
                
            if details['pr_url']:
                print(f"     [OK] GitHub PR created: {details['pr_url']}")
            else:
                print(f"     [MISSING] GitHub PR not created")
                
            if details['jira_ticket']:
                print(f"     [OK] Jira ticket created: {details['jira_ticket']}")
            else:
                print(f"     [MISSING] Jira ticket not created")
    
    # Summary
    print_section("WORKFLOW SUMMARY")
    
    pending_approval = [i for i in incidents if i.status == 'pending_approval']
    approved = [i for i in incidents if i.approval_status == 'approved']
    
    print(f"Total Incidents: {len(incidents)}")
    print(f"Pending Approval: {len(pending_approval)}")
    print(f"Approved: {len(approved)}")
    
    if pending_approval:
        print(f"\n[OK] {len(pending_approval)} incident(s) ready for approval in UI!")
        print(f"   Visit: http://localhost:8501")
    
    if approved:
        print(f"\n[OK] {len(approved)} incident(s) approved!")
        
        # Check if post-approval steps completed
        with_patches = sum(1 for i in approved if check_incident_details(db, i.incident_id)['has_patch_file'])
        with_prs = sum(1 for i in approved if check_incident_details(db, i.incident_id)['pr_url'])
        
        print(f"   Patches Generated: {with_patches}/{len(approved)}")
        print(f"   PRs Created: {with_prs}/{len(approved)}")
    
    # GitHub Integration Check
    print_section("GITHUB INTEGRATION STATUS")
    
    incidents_with_code = sum(1 for i in incidents if check_incident_details(db, i.incident_id)['has_original_code'])
    incidents_with_repo = sum(1 for i in incidents if check_incident_details(db, i.incident_id)['repo_name'])
    
    print(f"Incidents with GitHub code fetched: {incidents_with_code}/{len(incidents)}")
    print(f"Incidents with repository mapping: {incidents_with_repo}/{len(incidents)}")
    
    if incidents_with_code < len(incidents):
        print("\n[WARNING] Some incidents missing GitHub code!")
        print("   This will prevent patch/PR generation after approval.")
        print("\n   Possible causes:")
        print("   1. GitHub repos don't exist yet (run: python scripts/create_sample_repos.py)")
        print("   2. Files don't exist in repos")
        print("   3. App name doesn't match config/app_repo_mapping.yaml")
    
    # Next Steps
    print_section("NEXT STEPS")
    
    if not incidents:
        print("1. Generate test incidents through the MuleSoft error generator services")
        print("   See individual service READMEs for details")
    elif pending_approval:
        print("1. Approve incidents in UI:")
        print("   Visit http://localhost:8501")
        print("   Click 'Approve' on any incident")
        print("\n2. After approval, check for:")
        print("   - Patch file generation")
        print("   - GitHub PR creation")
        print("   - Jira ticket creation")
    elif not approved:
        print("1. Wait for workflow to complete")
        print("   python check_workflow_completion.py")
        print("\n2. Force completion if stuck:")
        print("   python fix_all_incidents_for_approval.py")
    else:
        print("[OK] All incidents processed!")
        print("\n3. Test complete workflow again:")
        print("   python scripts/clear_incidents.py")
        print("   Generate new test incidents through MuleSoft error generator services")
    
    return True


def main():
    """Main entry point"""
    try:
        test_workflow()
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
