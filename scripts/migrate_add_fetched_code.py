"""Migration to add fetched_code column to incidents table."""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.database import get_engine
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate():
    """Add fetched_code column to incidents table."""
    engine = get_engine()
    
    try:
        with engine.connect() as conn:
            # Check if column exists
            result = conn.execute(text("PRAGMA table_info(incidents)"))
            columns = [row[1] for row in result]
            
            if 'fetched_code' in columns:
                logger.info("✓ Column 'fetched_code' already exists")
                return
            
            # Add column
            logger.info("Adding 'fetched_code' column...")
            conn.execute(text("ALTER TABLE incidents ADD COLUMN fetched_code TEXT"))
            conn.commit()
            
            logger.info("✓ Migration completed successfully")
            
    except Exception as e:
        logger.error(f"✗ Migration failed: {str(e)}")
        raise


if __name__ == "__main__":
    migrate()
