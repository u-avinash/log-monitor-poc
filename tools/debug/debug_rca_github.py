"""Debug why RCA isn't using GitHub code."""
import sqlite3
from pathlib import Path
import json

db_path = Path("data/incidents.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get latest incident
cursor.execute("""
    SELECT 
        incident_id,
        app_name,
        error_title,
        raw_log,
        stack_trace,
        rca_text,
        repo_full_name,
        error_file_path,
        error_line_number,
        error_file_type
    FROM incidents 
    ORDER BY created_at DESC 
    LIMIT 1
""")

row = cursor.fetchone()
conn.close()

if row:
    inc_id, app, title, raw_log, stack, rca, repo, file_path, line_num, file_type = row
    
    print("=" * 80)
    print(f"INCIDENT #{inc_id}: {app}")
    print("=" * 80)
    print(f"Error: {title}")
    print()
    
    print("STORED METADATA:")
    print(f"  repo_full_name: {repo}")
    print(f"  error_file_path: {file_path}")
    print(f"  error_line_number: {line_num}")
    print(f"  error_file_type: {file_type}")
    print()
    
    print("RCA ANALYSIS:")
    print("-" * 80)
    
    # Check for GitHub indicators in RCA
    has_repo_section = "**Repository:**" in rca
    has_code_snippet = "```" in rca
    has_file_section = "**File:**" in rca
    has_not_available = "Code not available" in rca or "GitHub not configured" in rca
    
    print(f"  Contains '**Repository:**': {has_repo_section}")
    print(f"  Contains '**File:**': {has_file_section}")
    print(f"  Contains code blocks (```): {has_code_snippet}")
    print(f"  Contains 'Code not available': {has_not_available}")
    print()
    
    if has_not_available:
        print("❌ RCA shows 'Code not available' - GitHub fetch failed!")
    elif has_repo_section and has_file_section:
        print("✓ RCA contains GitHub code section!")
    elif has_code_snippet:
        print("⚠ RCA has code snippets but no explicit GitHub markers")
    else:
        print("❌ RCA has no code at all")
    
    print()
    print("RCA TEXT SAMPLE (first 1000 chars):")
    print("-" * 80)
    print(rca[:1000])
    
else:
    print("No incidents found")
