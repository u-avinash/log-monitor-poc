"""Fix incident status to show approval buttons."""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from storage.database import get_db
from storage.incident_repository import IncidentRepository

def fix_approval_status():
    """Update incident status to pending_approval."""
    db = next(get_db())
    repo = IncidentRepository(db)
    
    # Get all incidents with RCA_COMPLETE status
    incidents = repo.get_all(limit=100)
    
    updated_count = 0
    for incident in incidents:
        if incident.status == "RCA_COMPLETE" and incident.proposed_fix:
            # Update to pending_approval
            repo.update(
                incident_id=incident.incident_id,
                status="pending_approval"
            )
            print(f"✓ Updated incident {incident.incident_id} to PENDING_APPROVAL")
            updated_count += 1
    
    if updated_count == 0:
        print("No incidents needed updating")
    else:
        print(f"\n✅ Updated {updated_count} incident(s)")
        print("Refresh the UI to see the Approve/Reject buttons")

if __name__ == "__main__":
    fix_approval_status()
