"""View full RCA content."""
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
        rca_text,
        proposed_fix
    FROM incidents 
    ORDER BY created_at DESC 
    LIMIT 1
""")

inc = cursor.fetchone()
conn.close()

if inc:
    inc_id, app, title, sev, node, rca, fix = inc
    
    print("=" * 80)
    print(f"INCIDENT #{inc_id}: {app}")
    print("=" * 80)
    print(f"Error: {title}")
    print(f"Severity: {sev}")
    print(f"Current Node: {node}")
    print()
    print("=" * 80)
    print("FULL RCA CONTENT:")
    print("=" * 80)
    print(rca if rca else "[NO RCA]")
    print()
    print("=" * 80)
    print("PROPOSED FIX:")
    print("=" * 80)
    print(fix if fix else "[NO FIX YET]")
    
    # Check for GitHub code indicators
    if rca:
        indicators = [
            ("GitHub file fetched", "github" in rca.lower()),
            ("Code snippet present", "```" in rca),
            ("File path mentioned", "src/" in rca),
            ("Repository mentioned", "repository" in rca.lower()),
        ]
        
        print()
        print("=" * 80)
        print("GITHUB INTEGRATION CHECK:")
        print("=" * 80)
        for name, found in indicators:
            status = "[OK]" if found else "[NO]"
            print(f"{status} {name}")
