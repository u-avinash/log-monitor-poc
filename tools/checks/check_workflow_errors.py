"""Check for workflow processing errors."""
import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Check workflow processing errors for an incident.")
    parser.add_argument("--incident", help="Incident ID to inspect (e.g., 3EKQ)")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Inspect the most recently created incident (ignores --incident).",
    )
    args = parser.parse_args()

    db_path = Path("data/incidents.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    if args.latest:
        cursor.execute(
            """
            SELECT 
                incident_id,
                app_name,
                error_title,
                status,
                current_workflow_node,
                workflow_completed_steps,
                processing_errors,
                rca_text,
                proposed_fix
            FROM incidents 
            ORDER BY created_at DESC 
            LIMIT 1
            """
        )
    else:
        if not args.incident:
            raise SystemExit("ERROR: Provide --incident <ID> or use --latest")
        cursor.execute(
            """
            SELECT 
                incident_id,
                app_name,
                error_title,
                status,
                current_workflow_node,
                workflow_completed_steps,
                processing_errors,
                rca_text,
                proposed_fix
            FROM incidents
            WHERE incident_id = ?
            LIMIT 1
            """,
            (args.incident,),
        )

    inc = cursor.fetchone()

    if not inc:
        print("[INFO] No matching incident found")
    else:
        inc_id, app_name, error_title, status, node, steps, errors, rca, fix = inc

        print("=" * 80)
        print(f"INCIDENT #{inc_id} WORKFLOW STATUS")
        print("=" * 80)
        print(f"\nApp: {app_name}")
        print(f"Error: {error_title}")
        print(f"Status: {status}")
        print(f"Current Node: {node}")
        print()

        print("-" * 80)
        print("Completed Steps:")
        print("-" * 80)
        print(steps if steps else "[NONE]")
        print()

        print("-" * 80)
        print("Processing Errors:")
        print("-" * 80)
        print(errors if errors else "[NO ERRORS]")
        print()

        print("-" * 80)
        print("Content Status:")
        print("-" * 80)
        print(f"RCA: {'GENERATED' if rca else 'NOT GENERATED'} ({len(rca) if rca else 0} chars)")
        print(f"Fix: {'GENERATED' if fix else 'NOT GENERATED'} ({len(fix) if fix else 0} chars)")

    conn.close()


if __name__ == "__main__":
    main()
