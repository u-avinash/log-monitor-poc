"""Script to regenerate patch file for an incident."""
import sys
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import get_session, Incident
from agents.nodes.patch_generator import generate_patch_file_node
from agents.state import AgentState
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def regenerate_patch(incident_id: str):
    """Regenerate patch file for a specific incident."""
    logger.info(f"Regenerating patch for incident {incident_id}")
    
    with get_session() as session:
        # Get incident
        incident = session.query(Incident).filter_by(incident_id=incident_id).first()
        
        if not incident:
            logger.error(f"Incident {incident_id} not found")
            return
        
        logger.info(f"Found incident: {incident.incident_id}")
        logger.info(f"Current patch path: {incident.patch_path}")
        
        # Build state from incident
        state: AgentState = {
            'incident_id': incident.incident_id,
            'app_name': incident.app_name,
            'environment': incident.environment,
            'severity': incident.severity,
            'proposed_fix': incident.proposed_fix,
            'original_code': incident.fetched_code,
            'fix_explanation': incident.fix_explanation or "Auto-generated fix",
            'error_file_path': incident.error_file_path,
            'error_file_type': incident.error_file_type,
            'repo_full_name': incident.repo_full_name,
            'rca_text': incident.rca_text,
            'error_title': incident.error_title,
            'error_description': incident.error_description,
            'stack_trace': incident.stack_trace,
            'workflow_completed_steps': incident.workflow_completed_steps or [],
            'messages': []
        }
        
        logger.info(f"Has proposed_fix: {bool(state.get('proposed_fix'))}")
        logger.info(f"Has original_code: {bool(state.get('original_code'))}")
        logger.info(f"Error file path: {state.get('error_file_path')}")
        
        # Generate new patch
        updated_state = generate_patch_file_node(state)
        
        if 'patch_path' in updated_state:
            logger.info(f"✓ New patch generated: {updated_state['patch_path']}")
            logger.info(f"Messages: {updated_state.get('messages', [])}")
        else:
            logger.error(f"Failed to generate patch")
            logger.error(f"Messages: {updated_state.get('messages', [])}")
            logger.error(f"Error: {updated_state.get('error_message')}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python regenerate_patch.py <incident_id>")
        sys.exit(1)
    
    incident_id = sys.argv[1]
    regenerate_patch(incident_id)
