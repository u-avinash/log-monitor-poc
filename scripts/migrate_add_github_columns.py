"""Add GitHub metadata columns to incidents table."""
import sqlite3
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db_path = Path("data/incidents.db")

def migrate():
    """Add GitHub metadata columns."""
    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if columns already exist
    cursor.execute("PRAGMA table_info(incidents)")
    columns = {row[1] for row in cursor.fetchall()}
    
    migrations = [
        ("repo_full_name", "ALTER TABLE incidents ADD COLUMN repo_full_name TEXT"),
        ("error_file_path", "ALTER TABLE incidents ADD COLUMN error_file_path TEXT"),
        ("error_line_number", "ALTER TABLE incidents ADD COLUMN error_line_number INTEGER"),
        ("error_file_type", "ALTER TABLE incidents ADD COLUMN error_file_type TEXT"),
    ]
    
    for column_name, sql in migrations:
        if column_name in columns:
            logger.info(f"✓ Column '{column_name}' already exists")
        else:
            try:
                cursor.execute(sql)
                logger.info(f"✓ Added column '{column_name}'")
            except Exception as e:
                logger.error(f"✗ Failed to add column '{column_name}': {e}")
                conn.close()
                return False
    
    conn.commit()
    conn.close()
    
    logger.info("✓ Migration completed successfully!")
    return True

if __name__ == "__main__":
    migrate()
