"""
Cleanup script:
- Clears all incidents and telemetry logs
- Retains only HSS project (PRJ-E95DA993) integration config
- Keeps only slack, jira, github, llm for HSS project
- Removes non-HSS integration configs
"""
import sqlite3
import os
import glob

DB_PATH = "./data/incidents.db"
HSS_PROJECT_ID = "PRJ-E95DA993"

conn = sqlite3.connect(DB_PATH)
conn.isolation_level = None  # autocommit mode
cur = conn.cursor()

cur.execute("BEGIN")

# 1. Delete all incidents
cur.execute("DELETE FROM incidents")
incidents_deleted = cur.rowcount
print(f"[OK] Deleted {incidents_deleted} incident(s)")

# 2. Delete all telemetry logs
cur.execute("DELETE FROM telemetry_logs")
tele_deleted = cur.rowcount
print(f"[OK] Deleted {tele_deleted} telemetry log(s)")

# 3. For HSS project: null out anypoint, teams, runtime (keep llm, jira, github, slack)
cur.execute(
    "UPDATE project_integration_configs SET anypoint = NULL, teams = NULL, runtime = NULL WHERE project_id = ?",
    (HSS_PROJECT_ID,)
)
print(f"[OK] HSS project config updated: retained slack, jira, github, llm only")

# 4. Remove all other project integration configs (keep only HSS)
cur.execute(
    "DELETE FROM project_integration_configs WHERE project_id != ?",
    (HSS_PROJECT_ID,)
)
removed_configs = cur.rowcount
print(f"[OK] Removed {removed_configs} non-HSS integration config(s)")

cur.execute("COMMIT")
conn.close()

# 5. Clean up patch files
patch_files = glob.glob("./data/patches/*.md") + glob.glob("./data/patches/*.patch")
for f in patch_files:
    os.remove(f)
    print(f"[OK] Removed patch file: {os.path.basename(f)}")

# 6. Clean up PDF files
pdf_files = glob.glob("./data/pdfs/*.pdf")
for f in pdf_files:
    os.remove(f)
    print(f"[OK] Removed PDF file: {os.path.basename(f)}")

print("\n=== Cleanup Summary ===")
print(f"  Incidents deleted     : {incidents_deleted}")
print(f"  Telemetry logs deleted: {tele_deleted}")
print(f"  Patch files removed   : {len(patch_files)}")
print(f"  PDF files removed     : {len(pdf_files)}")
print(f"  Integration configs   : retained HSS ({HSS_PROJECT_ID}) with slack/jira/github/llm only")
print("Done.")
