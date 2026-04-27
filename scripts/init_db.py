"""Initialize the database with required tables."""
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import Base, get_engine
from config.settings import get_settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def initialize_database():
    """Create all database tables."""
    settings = get_settings()
    
    logger.info(f"Initializing database at: {settings.database_path}")
    
    # Ensure directories exist
    settings.ensure_directories()
    
    # Create all tables
    engine = get_engine()
    Base.metadata.create_all(engine)
    
    logger.info("✅ Database initialized successfully!")
    logger.info(f"   Database: {settings.database_path}")
    logger.info(f"   PDF dir: {settings.pdf_output_dir}")
    logger.info(f"   Patch dir: {settings.patch_output_dir}")


if __name__ == "__main__":
    initialize_database()
