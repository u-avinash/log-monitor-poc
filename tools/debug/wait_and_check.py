"""Wait for workflow to complete and check results."""
import sqlite3
import time
from pathlib import Path

db_path = Path("data/incidents.db")

print("Waiting for workflow to complete...")
print("=" * 80)

for i in range(30):  # Wait up to 30 seconds
    time.sleep(1)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            incident_id,
            app_name,
            error_title,
            severity,
            current_workflow_node,
            workflow_completed_steps,
            rca_text,
            proposed_fix,
            patch_path
        FROM incidents 
        ORDER BY created_at DESC 
        LIMIT 1
    """)
    
    inc = cursor.fetchone()
    conn.close()
    
    if inc:
        inc_id, app, title, sev, node, steps, rca, fix, patch = inc
        print(f"\r[{i+1}s] Node: {node:20s} | Steps: {len(eval(steps) if steps else [])} | RCA: {len(rca or '')} chars | Fix: {len(fix or '')} chars", end='', flush=True)
        
        # Check if workflow completed
        if rca and len(rca) > 100:
            print("\n\n" + "=" * 80)
            print("WORKFLOW COMPLETED!")
            print("=" * 80)
            print(f"\nIncident #{inc_id}: {app}")
            print(f"Severity: {sev}")
            print(f"Current Node: {node}")
            print(f"\n✓ RCA Generated: {len(rca)} characters")
            print(f"✓ Fix Generated: {len(fix or '')} characters") 
            print(f"✓ Patch: {'YES' if patch else 'NO'}")
            
            # Show first 300 chars of RCA
            print("\n" + "-" * 80)
            print("RCA Preview (first 300 chars):")
            print("-" * 80)
            print(rca[:300] + "...")
            
            # Check if GitHub code was mentioned
            if 'github' in rca.lower() or 'repository' in rca.lower() or 'src/main' in rca.lower():
                print("\n" + "=" * 80)
                print("✓ GitHub code references found in RCA!")
                print("=" * 80)
            
            break

print("\n\nDone!")
