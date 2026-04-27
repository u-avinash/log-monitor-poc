"""Regenerate XNRY patch without embedded comments."""
import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from storage.database import get_session
from storage.incident_repository import IncidentRepository
from agents.state import AgentState
from agents.nodes.patch_generator import generate_patch_file_node
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """Regenerate XNRY patch with cleaner code (no embedded comments)."""
    
    with get_session() as session:
        repo = IncidentRepository(session)
        incident = repo.get_by_id('XNRY')
        
        if not incident:
            logger.error("Incident XNRY not found")
            return
        
        # Get the proposed fix and remove the multi-line comments
        proposed_fix = incident.proposed_fix
        
        if not proposed_fix:
            logger.error("No proposed fix found for XNRY")
            return
        
        # Remove multi-line comment blocks that are inline with code changes
        # Keep the fix logic but remove the comment explanations
        clean_fix = proposed_fix.replace(
            '/* \n * Fix: guard against vars.errorResults being null/uninitialized in the error path.\n * This prevents the error handler from failing while appending the current error result.\n */',
            ''
        ).replace(
            '/* Fix: safely initialize the aggregation variable before concatenation. */',
            ''
        )
        
        # Clean up extra blank lines
        lines = clean_fix.split('\n')
        cleaned_lines = []
        prev_blank = False
        for line in lines:
            is_blank = line.strip() == ''
            if is_blank and prev_blank:
                continue  # Skip consecutive blank lines
            cleaned_lines.append(line)
            prev_blank = is_blank
        
        clean_fix = '\n'.join(cleaned_lines)
        
        # Create state for patch generation
        state = AgentState(
            incident_id='XNRY',
            app_name=incident.app_name,
            environment=incident.environment,
            severity=incident.severity,
            repo_full_name=incident.repo_full_name,
            original_code=incident.fetched_code,
            proposed_fix=clean_fix,
            fix_explanation=incident.fix_explanation,
            error_file_path=incident.error_file_path,
            rca_text=incident.rca_text,
            workflow_completed_steps=incident.workflow_completed_steps or [],
            messages=[]
        )
        
        # Generate clean patch
        logger.info("Generating clean patch without embedded comments...")
        result_state = generate_patch_file_node(state)
        
        if result_state.get('patch_path'):
            logger.info(f"✓ Clean patch created: {result_state['patch_path']}")
            print(f"\nClean patch file created at: {result_state['patch_path']}")
            print("\nThis patch should work in Anypoint Studio without the 'diffLabel' error.")
        else:
            logger.error("Failed to create clean patch")
            print(f"Error: {result_state.get('error_message', 'Unknown error')}")

if __name__ == '__main__':
    main()
