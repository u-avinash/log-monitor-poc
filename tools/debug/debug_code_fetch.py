"""Debug why code fetching is failing in the workflow."""
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
        raw_log,
        stack_trace
    FROM incidents 
    ORDER BY created_at DESC 
    LIMIT 5
""")

incidents = cursor.fetchall()
conn.close()

for inc in incidents:
    inc_id, app, title, raw_log, stack = inc
    print("=" * 80)
    print(f"Incident #{inc_id}: {app}")
    print("=" * 80)
    print(f"Error: {title[:80]}...")
    print()
    print("Raw Log (first 500 chars):")
    print("-" * 80)
    print((raw_log or "")[:500])
    print()
    print("Stack Trace (first 300 chars):")
    print("-" * 80)
    print((stack or "")[:300])
    print()
    
    # Try to extract file path
    from utils.code_fetcher import CodeFetcher
    fetcher = CodeFetcher()
    file_info = fetcher.extract_error_file_info(raw_log or "", stack or "", title)
    
    print("Extracted File Info:")
    print(f"  {file_info}")
    print()
