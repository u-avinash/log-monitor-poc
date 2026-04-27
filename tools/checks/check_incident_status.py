"""Check incident status."""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from storage.database import get_db
from storage.incident_repository import IncidentRepository

def check_incidents():
    """Check all incidents and their status."""
    db = next(get_db())
    repo = IncidentRepository(db)
    
    incidents = repo.get_all(limit=100)
    
    print(f"Found {len(incidents)} incident(s)\n")
    
    for incident in incidents:
        print(f"ID: {incident.incident_id}")
        print(f"App: {incident.app_name}")
        print(f"Status: {incident.status}")
        print(f"Approval Status: {incident.approval_status}")
        print(f"Has RCA: {bool(incident.rca_text)}")
        print(f"Has Fix: {bool(incident.proposed_fix)}")
        print(f"Has PDF: {bool(incident.pdf_path)}")
        print(f"Workflow Progress: {incident.workflow_progress_pct or 0:.0%}")
        print(f"Current Node: {incident.current_workflow_node}")
        print("-" * 50)

if __name__ == "__main__":
    check_incidents()
