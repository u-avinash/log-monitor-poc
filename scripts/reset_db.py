"""
reset_db.py — Complete database reset script.
Clears all data from all tables including telemetry_logs and project_integration_configs.
Safe to run while the app is running (uses WAL mode + timeout).
"""
import sys
import sqlite3
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import get_settings

settings = get_settings()
db_path = settings.database_path

print(f"Database: {db_path}")
print("Connecting with 30s lock timeout...")

conn = sqlite3.connect(db_path, timeout=30, isolation_level="IMMEDIATE")
cursor = conn.cursor()

tables_to_clear = [
    "incident_comments",
    "incidents",
    "telemetry_logs",
    "project_integration_configs",
]

try:
    # Switch to WAL journal mode for better concurrency
    cursor.execute("PRAGMA journal_mode=WAL")

    total_deleted = 0
    for table in tables_to_clear:
        # Check if table exists
        exists = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists:
            cursor.execute(f"DELETE FROM {table}")
            deleted = cursor.rowcount
            total_deleted += deleted
            print(f"  [OK] {table}: {deleted} rows deleted")
        else:
            print(f"  [SKIP] {table}: table not found")

    conn.commit()
    print(f"\nAll tables cleared. Total rows deleted: {total_deleted}")

except sqlite3.OperationalError as e:
    conn.rollback()
    print(f"\n[ERROR] Database lock error: {e}")
    print("  The app may be holding an exclusive write lock.")
    print("  Solution: Stop the running app, run this script, then restart the app.")
    sys.exit(1)
finally:
    conn.close()

# Final verification
conn2 = sqlite3.connect(db_path)
cur2 = conn2.cursor()
print("\nVerification:")
for table in tables_to_clear:
    exists = cur2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if exists:
        count = cur2.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        status = "[EMPTY]" if count == 0 else f"[WARN] {count} rows remain"
        print(f"  {table}: {status}")
conn2.close()
print("\nDatabase reset complete. Restart the app to begin fresh.")
