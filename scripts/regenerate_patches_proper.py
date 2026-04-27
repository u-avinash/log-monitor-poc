"""Regenerate patches with proper fix parsing logic."""
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import get_session
from storage.incident_repository import IncidentRepository
from agents.state import AgentState
from agents.nodes.patch_generator import generate_patch_file_node

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def regenerate_patches():
    """Regenerate patches for all incidents with fixes."""
    with get_session() as session:
        repo = IncidentRepository(session)
        
        # Get all incidents that have fixes but need proper patches
        incidents = repo.get_all()
        
        regenerated = 0
        skipped = 0
        errors = 0
        
        for incident in incidents:
            # Skip if no proposed fix
            if not incident.proposed_fix:
                logger.info(f"Skipping {incident.incident_id} - no proposed fix")
                skipped += 1
                continue
            
            # Skip if no original code (needed for proper diff)
            if not incident.fetched_code:
                logger.warning(f"Skipping {incident.incident_id} - no original code available")
                skipped += 1
                continue
            
            try:
                logger.info(f"\n{'='*60}")
                logger.info(f"Regenerating patch for incident {incident.incident_id}")
                logger.info(f"{'='*60}")
                
                # Create state from incident
                state = AgentState(
                    incident_id=incident.incident_id,
                    app_name=incident.app_name,
                    environment=incident.environment,
                    severity=incident.severity,
                    error_title=incident.error_title,
                    error_description=incident.error_description,
                    stack_trace=incident.stack_trace,
                    proposed_fix=incident.proposed_fix,
                    original_code=incident.fetched_code,
                    fix_explanation=incident.fix_explanation or "Auto-generated fix",
                    error_file_path=incident.error_file_path,
                    error_line_number=incident.error_line_number,
                    error_file_type=incident.error_file_type,
                    repo_full_name=incident.repo_full_name,
                    rca_text=incident.rca_text,
                    workflow_completed_steps=incident.workflow_completed_steps or [],
                    messages=[]
                )
                
                # Generate patch with corrected logic
                logger.info(f"Calling patch generator...")
                updated_state = generate_patch_file_node(state)
                
                if updated_state.get('patch_path'):
                    logger.info(f"✓ Successfully regenerated patch: {updated_state['patch_path']}")
                    regenerated += 1
                else:
                    logger.warning(f"⚠️ No patch path returned for {incident.incident_id}")
                    if updated_state.get('messages'):
                        for msg in updated_state['messages']:
                            logger.info(f"  Message: {msg}")
                    errors += 1
                    
            except Exception as e:
                logger.error(f"❌ Failed to regenerate patch for {incident.incident_id}: {str(e)}")
                import traceback
                traceback.print_exc()
                errors += 1
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Patch Regeneration Summary")
        logger.info(f"{'='*60}")
        logger.info(f"Total incidents: {len(incidents)}")
        logger.info(f"✓ Regenerated: {regenerated}")
        logger.info(f"⊘ Skipped: {skipped}")
        logger.info(f"❌ Errors: {errors}")
        logger.info(f"{'='*60}")


if __name__ == "__main__":
    regenerate_patches()
