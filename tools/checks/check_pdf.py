"""Check if incidents have PDF files generated."""
from storage.database import get_db
from storage.incident_repository import IncidentRepository

db = next(get_db())
repo = IncidentRepository(db)
incidents = repo.get_all(limit=10)

print(f"Total incidents: {len(incidents)}")

for i in incidents:
    print(f"\nIncident #{i.incident_id}:")
    print(f"  Status: {i.status}")
    print(f"  PDF: {i.pdf_path}")
    print(f"  Steps: {getattr(i, 'workflow_completed_steps', [])}")
    print(f"  Current Node: {getattr(i, 'current_workflow_node', 'N/A')}")
    print(f"  Progress: {getattr(i, 'workflow_progress_pct', 0) * 100:.0f}%")
