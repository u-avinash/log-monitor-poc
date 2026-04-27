"""Script to fetch GitHub code for incidents that have file metadata."""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.database import get_db
from storage.incident_repository import IncidentRepository
from integrations.github_client import GitHubClient
from utils.code_fetcher import CodeFetcher
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def fetch_code_for_incidents():
    """Fetch GitHub code for all incidents that have repo/file metadata."""
    db = next(get_db())
    repo = IncidentRepository(db)
    github_client = GitHubClient()
    code_fetcher = CodeFetcher()
    
    # Get all incidents
    incidents = repo.get_all(limit=1000)
    
    fetched_count = 0
    skipped_count = 0
    error_count = 0
    
    for incident in incidents:
        # Skip if already has code
        if incident.fetched_code:
            logger.info(f"[SKIP] Incident #{incident.incident_id} - already has code")
            skipped_count += 1
            continue
        
        # Skip if missing metadata
        if not incident.repo_full_name or not incident.error_file_path:
            logger.info(f"[SKIP] Incident #{incident.incident_id} - missing repo/file metadata")
            skipped_count += 1
            continue
        
        try:
            logger.info(f"\n[FETCH] Incident #{incident.incident_id}")
            logger.info(f"  Repo: {incident.repo_full_name}")
            logger.info(f"  File: {incident.error_file_path}")
            logger.info(f"  Line: {incident.error_line_number}")
            
            # Normalize the file path if it's just a filename
            file_path = incident.error_file_path
            if '/' not in file_path:
                # Just a filename - add standard Mule path based on extension
                if file_path.endswith('.xml'):
                    file_path = f'src/main/mule/{file_path}'
                elif file_path.endswith('.dwl'):
                    file_path = f'src/main/resources/dataweave/{file_path}'
                elif file_path.endswith(('.yaml', '.yml')):
                    file_path = f'src/main/resources/properties/{file_path}'
                elif file_path.endswith('.java'):
                    file_path = f'src/main/java/{file_path}'
                logger.info(f"  Normalized to: {file_path}")
            
            # Fetch code from GitHub using CodeFetcher
            result = code_fetcher.fetch_code_for_analysis(
                repo_full_name=incident.repo_full_name,
                file_path=file_path,
                line_number=incident.error_line_number
            )
            
            if result and result.get('full_content'):
                # Update incident with fetched code
                incident.fetched_code = result['full_content']
                # Update the path if it was normalized
                if file_path != incident.error_file_path:
                    incident.error_file_path = file_path
                db.commit()
                
                logger.info(f"  ✓ Fetched {len(result['full_content'])} characters")
                fetched_count += 1
            else:
                logger.warning(f"  ✗ Failed to fetch code")
                error_count += 1
                
        except Exception as e:
            logger.error(f"  ✗ Error: {str(e)}")
            error_count += 1
    
    logger.info(f"\n{'='*70}")
    logger.info(f"SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"Fetched: {fetched_count}")
    logger.info(f"Skipped: {skipped_count}")
    logger.info(f"Errors:  {error_count}")
    logger.info(f"{'='*70}\n")


if __name__ == "__main__":
    fetch_code_for_incidents()
