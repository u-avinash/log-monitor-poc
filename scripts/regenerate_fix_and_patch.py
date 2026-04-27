"""
Script to regenerate BOTH fix and patch for an incident with the new targeted approach.
This will re-run the fix_generator with the new targeted fix logic.
"""
import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import get_session
from storage.incident_repository import IncidentRepository
from agents.state import AgentState
from agents.nodes.fix_generator import generate_fix_node
from agents.nodes.patch_generator import generate_patch_file_node

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)


def regenerate_fix_and_patch(incident_id: str):
    """
    Regenerate both fix and patch for an incident.
    
    This will:
    1. Load the incident from database
    2. Re-run fix_generator (with new targeted fix approach)
    3. Re-run patch_generator (to create proper diff)
    """
    logger.info(f"Regenerating fix and patch for incident {incident_id}")
    
    with get_session() as session:
        repo = IncidentRepository(session)
        incident = repo.get_by_id(incident_id)
        
        if not incident:
            logger.error(f"Incident {incident_id} not found")
            return
        
        logger.info(f"Found incident: {incident_id}")
        logger.info(f"Current patch path: {incident.patch_path}")
        logger.info(f"Has RCA: {bool(incident.rca_text)}")
        logger.info(f"GitHub repo: {incident.repo_full_name}")
        logger.info(f"GitHub file: {incident.error_file_path}")
        
        # Build state from incident
        state: AgentState = {
            'incident_id': incident_id,
            'app_name': incident.app_name,
            'environment': incident.environment,
            'error_title': incident.error_title,
            'error_description': incident.error_description,
            'stack_trace': incident.stack_trace,
            'severity': incident.severity,
            'rca_text': incident.rca_text,
            'repo_full_name': incident.repo_full_name,
            'error_file_path': incident.error_file_path,
            'error_line_number': incident.error_line_number,
            'error_file_type': _detect_file_type(incident.error_file_path) if incident.error_file_path else 'unknown',
            'workflow_completed_steps': incident.workflow_completed_steps or [],
            'workflow_progress_pct': incident.workflow_progress_pct or 0.0,
            'messages': []
        }
        
        # STEP 1: Regenerate fix (this will fetch code and generate targeted fix)
        logger.info("=" * 60)
        logger.info("STEP 1: Regenerating fix with targeted approach")
        logger.info("=" * 60)
        state = generate_fix_node(state)
        
        if state.get('proposed_fix'):
            logger.info(f"✓ Fix regenerated ({len(state['proposed_fix'])} chars)")
            logger.info(f"  Has original_code: {bool(state.get('original_code'))}")
        else:
            logger.error("✗ Fix generation failed")
            return
        
        # STEP 2: Regenerate patch (this will apply targeted fix and create proper diff)
        logger.info("=" * 60)
        logger.info("STEP 2: Regenerating patch from targeted fix")
        logger.info("=" * 60)
        state = generate_patch_file_node(state)
        
        if state.get('patch_path'):
            logger.info(f"✓ Patch regenerated: {state['patch_path']}")
            
            # Analyze the new patch
            with open(state['patch_path'], 'r', encoding='utf-8') as f:
                patch_content = f.read()
            
            lines_deleted = len([l for l in patch_content.split('\n') if l.startswith('-') and not l.startswith('---')])
            lines_added = len([l for l in patch_content.split('\n') if l.startswith('+') and not l.startswith('+++')])
            
            logger.info("=" * 60)
            logger.info("PATCH ANALYSIS")
            logger.info("=" * 60)
            logger.info(f"Lines deleted: {lines_deleted}")
            logger.info(f"Lines added: {lines_added}")
            logger.info(f"Ratio (deleted/added): {lines_deleted/lines_added if lines_added > 0 else 'N/A'}")
            
            if lines_deleted > lines_added * 3:
                logger.warning(f"⚠️  Suspicious: Too many deletions ({lines_deleted}) vs additions ({lines_added})")
            else:
                logger.info(f"✓ Patch looks reasonable")
        else:
            logger.error("✗ Patch generation failed")
        
        logger.info(f"\nMessages: {state.get('messages', [])}")


def _detect_file_type(file_path: str) -> str:
    """Detect file type from path."""
    if not file_path:
        return 'unknown'
    
    if file_path.endswith('.xml'):
        return 'mule_xml'
    elif file_path.endswith('.dwl'):
        return 'dataweave'
    elif file_path.endswith('.java'):
        return 'java'
    elif file_path.endswith('.yaml') or file_path.endswith('.yml'):
        return 'yaml'
    else:
        return 'unknown'


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python regenerate_fix_and_patch.py <incident_id>")
        sys.exit(1)
    
    incident_id = sys.argv[1]
    regenerate_fix_and_patch(incident_id)
