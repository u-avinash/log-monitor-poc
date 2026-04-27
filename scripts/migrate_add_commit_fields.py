"""Migration script to add commit_sha and commit_url columns."""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from storage.database import get_engine
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate():
    """Add commit_sha and commit_url columns to incidents table."""
    engine = get_engine()
    
    with engine.connect() as conn:
        try:
            # Add commit_sha column
            conn.execute(text("ALTER TABLE incidents ADD COLUMN commit_sha VARCHAR(255)"))
            logger.info("✅ Added commit_sha column")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                logger.info("ℹ️  commit_sha column already exists")
            else:
                logger.error(f"❌ Error adding commit_sha: {e}")
                raise
        
        try:
            # Add commit_url column
            conn.execute(text("ALTER TABLE incidents ADD COLUMN commit_url VARCHAR(500)"))
            logger.info("✅ Added commit_url column")
        except Exception as e:
            if "duplicate column name" in str(e).lower():
                logger.info("ℹ️  commit_url column already exists")
            else:
                logger.error(f"❌ Error adding commit_url: {e}")
                raise
        
        conn.commit()
    
    logger.info("✅ Migration completed successfully!")

if __name__ == "__main__":
    migrate()
