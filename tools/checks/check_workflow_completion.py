"""Check workflow completion details for incidents."""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from storage.database import get_db
from storage.incident_repository import IncidentRepository

def check_workflow():
    """Check workflow completion for all incidents."""
    db = next(get_db())
    repo = IncidentRepository(db)
    
    incidents = repo.get_all(limit=100)
    
    print(f"Found {len(incidents)} incident(s)\n")
    
    for incident in incidents:
        print(f"ID: {incident.incident_id} - {incident.app_name}")
        print(f"Status: {incident.status}")
        print(f"Approval Status: {incident.approval_status}")
        print(f"Current Node: {incident.current_workflow_node}")
        print(f"Progress: {(incident.workflow_progress_pct or 0) * 100:.0f}%")
        
        completed_steps = getattr(incident, 'workflow_completed_steps', []) or []
        print(f"Completed Steps ({len(completed_steps)}): {completed_steps}")
        
        print(f"\nIntegrations:")
        print(f"  - PDF: {incident.pdf_path or 'None'}")
        print(f"  - Patch: {incident.patch_path or 'None'}")
        print(f"  - Jira: {incident.jira_ticket_key or 'None'}")
        print(f"  - PR: {incident.pr_url or 'None'}")
        print(f"  - Slack: {getattr(incident, 'slack_notification_sent', 'Unknown')}")
        
        # Check for errors
        if hasattr(incident, 'jira_error') and incident.jira_error:
            print(f"  - Jira Error: {incident.jira_error}")
        
        if hasattr(incident, 'notification_errors') and incident.notification_errors:
            print(f"  - Notification Errors: {incident.notification_errors}")
        
        print("=" * 70)

if __name__ == "__main__":
    check_workflow()
