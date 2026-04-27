"""Check severity in database for debugging."""
import sys
sys.path.insert(0, '.')

from storage.database import get_db, Incident
import json

def main():
    db = next(get_db())
    
    try:
        # Get all incidents
        incidents = db.query(Incident).order_by(Incident.created_at.desc()).limit(5).all()
        
        print(f"Total incidents in DB: {db.query(Incident).count()}")
        print("\n" + "="*80)
        
        for incident in incidents:
            print(f"\nIncident ID: {incident.incident_id}")
            print(f"App: {incident.app_name}")
            print(f"Error: {incident.error_title[:80]}")
            print(f"Severity in DB: {incident.severity}")
            print(f"Status: {incident.status}")
            print(f"Created: {incident.created_at}")
            print(f"\nRaw log (first 500 chars):")
            print(incident.raw_log[:500] if incident.raw_log else "None")
            print("-" * 80)
    
    finally:
        db.close()

if __name__ == "__main__":
    main()
