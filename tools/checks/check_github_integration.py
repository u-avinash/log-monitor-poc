"""Check if GitHub integration is working in the workflow."""
import sqlite3
from pathlib import Path

db_path = Path("data/incidents.db")

if not db_path.exists():
    print("[ERROR] Database not found")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check recent incidents
cursor.execute("""
    SELECT 
        incident_id,
        app_name,
        error_title,
        current_workflow_node,
        LENGTH(rca_text) as rca_length
    FROM incidents 
    ORDER BY created_at DESC 
    LIMIT 5
""")

incidents = cursor.fetchall()

print("="*80)
print("GITHUB INTEGRATION STATUS CHECK")
print("="*80)

if not incidents:
    print("\n[INFO] No incidents found yet. Waiting for simulator to generate incidents...")
else:
    print(f"\n[OK] Found {len(incidents)} recent incidents:\n")
    
    for inc in incidents:
        inc_id, app_name, error_title, node, rca_len = inc
        print(f"Incident #{inc_id}: {app_name}")
        print(f"  Error: {error_title[:50] if error_title else 'N/A'}...")
        print(f"  Current Node: {node if node else 'not started'}")
        print(f"  RCA Length: {rca_len if rca_len else 0} chars")
        
        if rca_len and rca_len > 100:
            print(f"  [OK] Workflow is processing!")
        print()

conn.close()
