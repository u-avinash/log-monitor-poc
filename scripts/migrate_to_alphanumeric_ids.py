"""
Migration script to convert incident IDs from integers to 4-character alphanumeric IDs.

This script:
1. Backs up the existing database
2. Creates a mapping of old integer IDs to new alphanumeric IDs
3. Creates a new database with the updated schema
4. Migrates all data with new IDs
5. Updates any references to old IDs
"""
import sys
import os
import shutil
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.database import get_engine, Base, Incident
from sqlalchemy.orm import Session, sessionmaker
from utils.id_generator import generate_incident_id
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def backup_database(db_path: str) -> str:
    """Create a backup of the existing database."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"
    
    if os.path.exists(db_path):
        shutil.copy2(db_path, backup_path)
        logger.info(f"Database backed up to: {backup_path}")
        return backup_path
    else:
        logger.warning(f"Database file not found: {db_path}")
        return ""


def migrate_incidents():
    """Migrate incidents from integer IDs to alphanumeric IDs."""
    from config.settings import get_settings
    settings = get_settings()
    
    db_path = settings.database_path
    
    # Step 1: Backup existing database
    logger.info("Step 1: Backing up database...")
    backup_path = backup_database(db_path)
    
    if not backup_path:
        logger.info("No existing database found. Creating new database with alphanumeric IDs.")
        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        logger.info("New database created successfully!")
        return
    
    # Step 2: Read existing incidents
    logger.info("Step 2: Reading existing incidents...")
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    try:
        # Try to query existing incidents
        # Note: This will fail if the schema has already been changed
        existing_incidents = session.query(Incident).all()
        
        if not existing_incidents:
            logger.info("No incidents found in database. Migration complete.")
            session.close()
            return
        
        logger.info(f"Found {len(existing_incidents)} incidents to migrate")
        
        # Step 3: Create ID mapping
        logger.info("Step 3: Generating new alphanumeric IDs...")
        id_mapping = {}  # old_id -> new_id
        existing_new_ids = set()
        
        for incident in existing_incidents:
            old_id = incident.incident_id
            
            # Check if ID is already alphanumeric (already migrated)
            if isinstance(old_id, str) and len(old_id) == 4 and old_id.isalnum() and old_id.isupper():
                logger.info(f"Incident {old_id} already has alphanumeric ID, skipping")
                continue
            
            # Generate new alphanumeric ID
            new_id = generate_incident_id(existing_new_ids)
            existing_new_ids.add(new_id)
            id_mapping[old_id] = new_id
            logger.info(f"Mapping: {old_id} -> {new_id}")
        
        if not id_mapping:
            logger.info("All incidents already have alphanumeric IDs. Migration complete.")
            session.close()
            return
        
        # Step 4: Delete existing database and recreate with new schema
        logger.info("Step 4: Recreating database with new schema...")
        session.close()
        
        # Remove old database
        if os.path.exists(db_path):
            os.remove(db_path)
            logger.info(f"Removed old database: {db_path}")
        
        # Create new database with updated schema
        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        logger.info("New database created with updated schema")
        
        # Step 5: Restore data with new IDs from backup
        logger.info("Step 5: Restoring incidents with new IDs...")
        
        # Connect to backup database
        backup_engine = create_engine(f"sqlite:///{backup_path}")
        BackupSessionLocal = sessionmaker(bind=backup_engine)
        backup_session = BackupSessionLocal()
        
        # Connect to new database
        new_session = SessionLocal()
        
        # Copy each incident with new ID
        backup_incidents = backup_session.query(Incident).all()
        
        for old_incident in backup_incidents:
            old_id = old_incident.incident_id
            new_id = id_mapping.get(old_id, old_id)  # Use old ID if not in mapping
            
            # Create new incident with alphanumeric ID
            new_incident = Incident(
                incident_id=new_id,
                app_name=old_incident.app_name,
                environment=old_incident.environment,
                error_title=old_incident.error_title,
                error_description=old_incident.error_description,
                stack_trace=old_incident.stack_trace,
                raw_log=old_incident.raw_log,
                error_fingerprint=old_incident.error_fingerprint,
                is_duplicate=old_incident.is_duplicate,
                existing_incident_id=id_mapping.get(old_incident.existing_incident_id, old_incident.existing_incident_id) if old_incident.existing_incident_id else None,
                status=old_incident.status,
                severity=old_incident.severity,
                current_workflow_node=old_incident.current_workflow_node,
                workflow_completed_steps=old_incident.workflow_completed_steps,
                workflow_progress_pct=old_incident.workflow_progress_pct,
                rca_text=old_incident.rca_text,
                rca_confidence=old_incident.rca_confidence,
                pdf_path=old_incident.pdf_path,
                alert_sent=old_incident.alert_sent,
                alert_channels=old_incident.alert_channels,
                slack_notification_sent=old_incident.slack_notification_sent,
                teams_notification_sent=old_incident.teams_notification_sent,
                notification_errors=old_incident.notification_errors,
                jira_ticket_key=old_incident.jira_ticket_key,
                jira_ticket_url=old_incident.jira_ticket_url,
                jira_error=old_incident.jira_error,
                should_auto_fix=old_incident.should_auto_fix,
                proposed_fix=old_incident.proposed_fix,
                fix_explanation=old_incident.fix_explanation,
                patch_path=old_incident.patch_path,
                fix_quality_score=old_incident.fix_quality_score,
                fix_approved=old_incident.fix_approved,
                fix_approval_comment=old_incident.fix_approval_comment,
                approval_status=old_incident.approval_status if hasattr(old_incident, 'approval_status') else None,
                approved_by=old_incident.approved_by if hasattr(old_incident, 'approved_by') else None,
                approved_at=old_incident.approved_at if hasattr(old_incident, 'approved_at') else None,
                approval_notes=old_incident.approval_notes if hasattr(old_incident, 'approval_notes') else None,
                repo_full_name=old_incident.repo_full_name if hasattr(old_incident, 'repo_full_name') else None,
                error_file_path=old_incident.error_file_path if hasattr(old_incident, 'error_file_path') else None,
                error_line_number=old_incident.error_line_number if hasattr(old_incident, 'error_line_number') else None,
                error_file_type=old_incident.error_file_type if hasattr(old_incident, 'error_file_type') else None,
                fetched_code=old_incident.fetched_code if hasattr(old_incident, 'fetched_code') else None,
                repo_path=old_incident.repo_path,
                fix_branch=old_incident.fix_branch,
                fix_committed=old_incident.fix_committed,
                pr_number=old_incident.pr_number,
                pr_url=old_incident.pr_url,
                processing_errors=old_incident.processing_errors,
                processing_duration_seconds=old_incident.processing_duration_seconds,
                retries=old_incident.retries,
                created_at=old_incident.created_at,
                updated_at=old_incident.updated_at
            )
            
            new_session.add(new_incident)
            logger.info(f"Migrated incident {old_id} -> {new_id}")
        
        new_session.commit()
        logger.info(f"Successfully migrated {len(backup_incidents)} incidents")
        
        # Clean up
        backup_session.close()
        new_session.close()
        
        logger.info("Migration completed successfully!")
        logger.info(f"Backup database saved at: {backup_path}")
        logger.info("You can delete the backup file once you've verified the migration.")
        
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        logger.error("Restoring backup...")
        
        # Restore backup
        if backup_path and os.path.exists(backup_path):
            if os.path.exists(db_path):
                os.remove(db_path)
            shutil.copy2(backup_path, db_path)
            logger.info("Backup restored successfully")
        
        raise


if __name__ == "__main__":
    from sqlalchemy import create_engine
    
    logger.info("=" * 80)
    logger.info("INCIDENT ID MIGRATION: Integer -> Alphanumeric")
    logger.info("=" * 80)
    logger.info("")
    logger.info("This script will migrate incident IDs from integers to 4-character")
    logger.info("alphanumeric IDs (e.g., 1 -> A7CB, 2 -> K3M9)")
    logger.info("")
    logger.info("The database will be backed up before migration.")
    logger.info("")
    
    try:
        migrate_incidents()
        logger.info("")
        logger.info("=" * 80)
        logger.info("MIGRATION COMPLETED SUCCESSFULLY!")
        logger.info("=" * 80)
    except Exception as e:
        logger.error("")
        logger.error("=" * 80)
        logger.error("MIGRATION FAILED!")
        logger.error(f"Error: {str(e)}")
        logger.error("=" * 80)
        sys.exit(1)
