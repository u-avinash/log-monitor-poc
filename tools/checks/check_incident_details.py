"""Check full incident details to verify GitHub integration."""
import sqlite3
from pathlib import Path

db_path = Path("data/incidents.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get the most recent incident
cursor.execute("""
    SELECT 
        incident_id,
        app_name,
        error_title,
        current_workflow_node,
        rca_text,
        proposed_fix,
        patch_path
    FROM incidents 
    ORDER BY created_at DESC 
    LIMIT 1
""")

inc = cursor.fetchone()

if not inc:
    print("[INFO] No incidents found")
else:
    inc_id, app_name, error_title, node, rca, fix, patch = inc
    
    print("="*80)
    print(f"INCIDENT #{inc_id} DETAILS")
    print("="*80)
    print(f"\nApp Name: {app_name}")
    print(f"Error: {error_title}")
    print(f"Current Node: {node}")
    print()
    
    print("-"*80)
    print("RCA (Root Cause Analysis):")
    print("-"*80)
    if rca:
        print(rca[:500] + "..." if len(rca) > 500 else rca)
    else:
        print("[NOT GENERATED YET]")
    print()
    
    print("-"*80)
    print("Fix:")
    print("-"*80)
    if fix:
        print(fix[:500] + "..." if len(fix) > 500 else fix)
    else:
        print("[NOT GENERATED YET]")
    print()
    
    print("-"*80)
    print("Patch File:")
    print("-"*80)
    if patch:
        print(f"Location: {patch}")
        patch_path = Path(patch)
        if patch_path.exists():
            print("\n[OK] Patch file exists!")
            with open(patch_path, 'r') as f:
                content = f.read()
                print(f"\nFirst 300 chars:\n{content[:300]}")
        else:
            print("[WARNING] Patch file path set but file not found")
    else:
        print("[NOT GENERATED YET]")

conn.close()
