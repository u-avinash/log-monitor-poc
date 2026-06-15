"""
wipe_auth.py — Wipe all auth data except the default admin user.
Clears: users (non-admin), projects, teams, memberships, project_assignments, sessions.
Keeps: the seeded admin user account only.
"""
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
AUTH_FILE = os.path.join(DATA_DIR, "auth_data.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")

# ── Step 1: auth_data.json ────────────────────────────────────────────────
if not os.path.exists(AUTH_FILE):
    print("auth_data.json not found — will be seeded fresh on next app start.")
    print("Only default admin will exist.")
else:
    with open(AUTH_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    print("Current state:")
    print(f"  Users:    {[u.get('username') for u in data.get('users', [])]}")
    print(f"  Projects: {[p.get('name') for p in data.get('projects', [])]}")
    print(f"  Teams:    {len(data.get('teams', []))} teams")
    print(f"  Assignments: {len(data.get('project_assignments', []))} assignments")

    # Keep only the admin user
    admin_users = [u for u in data.get("users", []) if u.get("role") == "admin"]
    if not admin_users:
        print("\n[WARN] No admin user found in auth data. Deleting auth_data.json to force re-seed.")
        os.remove(AUTH_FILE)
    else:
        # Build a minimal fresh state with only the admin user
        fresh_data = {
            "users": admin_users,
            "projects": [],
            "teams": [],
            "memberships": [],
            "project_assignments": [],
            "project_configs": [],
        }
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(fresh_data, f, indent=2, ensure_ascii=False)

        print(f"\nKept {len(admin_users)} admin user(s), removed all projects, teams, and assignments.")
        print(f"  Kept: {[u.get('username') for u in admin_users]}")

# ── Step 2: sessions.json ─────────────────────────────────────────────────
if os.path.exists(SESSIONS_FILE):
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)
    print("sessions.json cleared (all active sessions invalidated).")
else:
    print("sessions.json not found (no active sessions to clear).")

# ── Step 3: Re-run DB reset ────────────────────────────────────────────────
print("\nClearing SQLite tables...")
import sqlite3
from config.settings import get_settings

s = get_settings()
db_path = s.database_path

conn = sqlite3.connect(db_path, timeout=30, isolation_level="IMMEDIATE")
cursor = conn.cursor()

tables = ["incident_comments", "incidents", "telemetry_logs", "project_integration_configs"]
total = 0
try:
    cursor.execute("PRAGMA journal_mode=WAL")
    for table in tables:
        exists = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists:
            cursor.execute(f"DELETE FROM {table}")
            deleted = cursor.rowcount
            total += deleted
            print(f"  [OK] {table}: {deleted} rows deleted")
        else:
            print(f"  [SKIP] {table}: not found")
    conn.commit()
except sqlite3.OperationalError as e:
    conn.rollback()
    print(f"  [ERROR] {e}")
finally:
    conn.close()

print(f"\nTotal DB rows cleared: {total}")

# ── Final summary ─────────────────────────────────────────────────────────
print("\n" + "="*50)
print("FRESH START COMPLETE")
print("="*50)
print("Restart the app to apply the clean state.")
print()
print("Login with:")
print("  Username : admin")
print("  Password : ChangeMe123!")
print("  URL      : http://localhost:8080")
