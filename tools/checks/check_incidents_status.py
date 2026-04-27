"""Check status of specific incidents."""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from storage.database import get_session_factory, Incident

SessionLocal = get_session_factory()
db = SessionLocal()

incidents = db.query(Incident).filter(
    Incident.incident_id.in_(['4AXH', 'EVJ6', 'DDCY'])
).all()

print('Incident Status Check:')
print('='*70)

for i in incidents:
    print(f'\nID: {i.incident_id}')
    print(f'  Status: {i.status}')
    print(f'  File Path: {i.error_file_path}')
    print(f'  Has Code: {i.fetched_code is not None}')
    print(f'  Code Length: {len(i.fetched_code) if i.fetched_code else 0}')
    print(f'  Current Node: {i.current_workflow_node}')
    print(f'  Fix Available: {i.proposed_fix is not None}')
    print(f'  Patch Path: {i.patch_path}')

db.close()
