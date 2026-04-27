"""Add metadata column to incidents table for OTLP custom attributes."""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from config.settings import get_settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate():
    """Add metadata column to store OTLP custom attributes."""
    settings = get_settings()
    db_path = settings.database_path
    
    if not os.path.exists(db_path):
        logger.error(f"Database not found at {db_path}")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(incidents)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'incident_metadata' in columns:
            logger.info("incident_metadata column already exists")
            return True
        
        # Add incident_metadata column (not 'metadata' which is reserved in SQLAlchemy)
        logger.info("Adding incident_metadata column to incidents table...")
        cursor.execute("""
            ALTER TABLE incidents 
            ADD COLUMN incident_metadata TEXT
        """)
        
        conn.commit()
        logger.info("Successfully added metadata column")
        
        # Verify the column was added
        cursor.execute("PRAGMA table_info(incidents)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'incident_metadata' in columns:
            logger.info("Verified: incident_metadata column exists")
            return True
        else:
            logger.error("Failed to add incident_metadata column")
            return False
            
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
