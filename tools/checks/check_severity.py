"""Check incident severity assignment."""
import sqlite3
from pathlib import Path

db_path = Path("data/incidents.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("""
    SELECT 
        incident_id,
        app_name,
        error_title,
        severity,
        current_workflow_node,
        workflow_completed_steps
    FROM incidents 
    ORDER BY created_at DESC 
    LIMIT 3
""")

incidents = cursor.fetchall()

print("="*80)
print("INCIDENT SEVERITY CHECK")
print("="*80)

for inc in incidents:
    inc_id, app_name, error_title, severity, node, steps = inc
    print(f"\nIncident #{inc_id}: {app_name}")
    print(f"  Severity: {severity}")
    print(f"  Error: {error_title[:60]}...")
    print(f"  Current Node: {node}")
    print(f"  Completed Steps: {steps}")
    print()
    
    if severity not in ['HIGH', 'CRITICAL']:
        print(f"  [INFO] RCA skipped - only HIGH/CRITICAL errors get RCA")
        print(f"  [ACTION] Severity needs to be HIGH or CRITICAL for full workflow")

conn.close()
