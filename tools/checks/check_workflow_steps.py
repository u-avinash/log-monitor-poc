"""Check workflow steps for a specific incident."""
from storage.database import get_db
from storage.incident_repository import IncidentRepository

db = next(get_db())
repo = IncidentRepository(db)

# Get incident 1 (latest from logs)
incident = repo.get_by_id(1)

if incident:
    print(f"Incident #{incident.incident_id}: {incident.error_title}")
    print(f"Status: {incident.status}")
    print(f"Current Node: {incident.current_workflow_node}")
    print(f"Progress: {incident.workflow_progress_pct * 100:.1f}%")
    print(f"\nCompleted Steps: {incident.workflow_completed_steps}")
    print(f"\nStep details:")
    if incident.workflow_completed_steps:
        for i, step in enumerate(incident.workflow_completed_steps, 1):
            print(f"  {i}. {step}")
    else:
        print("  (None recorded)")
else:
    print("Incident #3 not found")
