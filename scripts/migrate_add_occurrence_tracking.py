"""
Migration script to add occurrence tracking columns to incidents table.

This adds:
- occurrence_count: Track how many times this error has occurred
- last_occurrence_at: Track when the error last occurred
"""
import sys
import os
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from storage.database import get_engine, Base, Incident
from sqlalchemy import Column, Integer, DateTime, inspect, text
from sqlalchemy.orm import sessionmaker
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_add_occurrence_tracking():
    """Add occurrence tracking columns to incidents table."""
    
    logger.info("="*80)
    logger.info("MIGRATION: Adding Occurrence Tracking Columns")
    logger.info("="*80)
    
    try:
        engine = get_engine()
        inspector = inspect(engine)
        
        # Check if columns already exist
        columns = [col['name'] for col in inspector.get_columns('incidents')]
        
        needs_occurrence_count = 'occurrence_count' not in columns
        needs_last_occurrence = 'last_occurrence_at' not in columns
        
        if not needs_occurrence_count and not needs_last_occurrence:
            logger.info("✓ Columns already exist, no migration needed")
            return True
        
        # Add columns using raw SQL (SQLite-specific)
        with engine.connect() as conn:
            if needs_occurrence_count:
                logger.info("Adding occurrence_count column...")
                conn.execute(text("ALTER TABLE incidents ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 1"))
                logger.info("✓ Added occurrence_count column")
            
            if needs_last_occurrence:
                logger.info("Adding last_occurrence_at column...")
                conn.execute(text(f"ALTER TABLE incidents ADD COLUMN last_occurrence_at TIMESTAMP DEFAULT '{datetime.utcnow().isoformat()}'"))
                logger.info("✓ Added last_occurrence_at column")
            
            conn.commit()
        
        # Verify columns were added
        inspector = inspect(engine)
        columns_after = [col['name'] for col in inspector.get_columns('incidents')]
        
        if 'occurrence_count' in columns_after and 'last_occurrence_at' in columns_after:
            logger.info("\n" + "="*80)
            logger.info("✓ MIGRATION SUCCESSFUL")
            logger.info("="*80)
            logger.info("\nNew columns added:")
            logger.info("  - occurrence_count (INTEGER, default=1)")
            logger.info("  - last_occurrence_at (TIMESTAMP)")
            logger.info("\nYou can now restart your services.")
            return True
        else:
            logger.error("✗ Migration verification failed")
            return False
            
    except Exception as e:
        logger.error(f"✗ Migration failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = migrate_add_occurrence_tracking()
    sys.exit(0 if success else 1)
