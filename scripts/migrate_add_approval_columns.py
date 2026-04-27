"""Migration script to add approval tracking columns to the database."""
import sqlite3
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import get_settings

settings = get_settings()


def migrate_database():
    """Add approval columns to incidents table."""
    db_path = settings.database_path
    
    print(f"Migrating database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if columns already exist
    cursor.execute("PRAGMA table_info(incidents)")
    columns = [col[1] for col in cursor.fetchall()]
    
    columns_to_add = [
        ("approval_status", "VARCHAR(20)"),
        ("approved_by", "VARCHAR(255)"),
        ("approved_at", "DATETIME"),
        ("approval_notes", "TEXT")
    ]
    
    added_count = 0
    for col_name, col_type in columns_to_add:
        if col_name not in columns:
            print(f"  Adding column: {col_name} ({col_type})")
            cursor.execute(f"ALTER TABLE incidents ADD COLUMN {col_name} {col_type}")
            added_count += 1
        else:
            print(f"  Column already exists: {col_name}")
    
    conn.commit()
    conn.close()
    
    if added_count > 0:
        print(f"\n✅ Migration complete! Added {added_count} new columns.")
    else:
        print("\n✅ All columns already exist. No migration needed.")


if __name__ == "__main__":
    try:
        migrate_database()
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)
