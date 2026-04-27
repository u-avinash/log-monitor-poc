"""Check workflow status for latest incident."""
from storage.database import get_session
from storage.incident_repository import IncidentRepository

with get_session() as session:
    repo = IncidentRepository(session)
    incident = repo.get_by_id(4)
    
    print(f"\nIncident #{incident.incident_id}")
    print(f"Status: {incident.status}")
    print(f"Severity: {incident.severity}")
    print(f"RCA Generated: {'Yes' if incident.rca_text else 'No'}")
    print(f"Fix Generated: {'Yes' if incident.proposed_fix else 'No'}")
    print(f"Workflow Progress: {incident.workflow_progress_pct:.0%}")
    print(f"Current Node: {incident.current_workflow_node}")
    print(f"Completed Steps: {len(incident.workflow_completed_steps) if incident.workflow_completed_steps else 0}")
    
    if incident.rca_text: 
        
        print(f"\nRCA Preview: {incident.rca_text[:200]}...")
    
    if incident.proposed_fix:
        print(f"\nFix Preview: {incident.proposed_fix[:200]}...")
