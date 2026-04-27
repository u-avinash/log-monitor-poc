"""Generate unique alphanumeric incident IDs."""
import random
import string
from typing import Set


def generate_incident_id(existing_ids: Set[str] = None) -> str:
    """
    Generate a unique 4-character alphanumeric incident ID in uppercase.
    
    Format: 4 characters using A-Z and 0-9 (e.g., A7CB, Z9XY, 1K2M)
    
    Args:
        existing_ids: Set of existing IDs to avoid collisions
        
    Returns:
        str: A unique 4-character uppercase alphanumeric ID
    """
    if existing_ids is None:
        existing_ids = set()
    
    # Characters to use: A-Z and 0-9 (36 possible characters)
    # Total combinations: 36^4 = 1,679,616 possible IDs
    chars = string.ascii_uppercase + string.digits
    
    # Try up to 100 times to generate a unique ID (should rarely need more than 1)
    max_attempts = 100
    for _ in range(max_attempts):
        incident_id = ''.join(random.choices(chars, k=4))
        
        if incident_id not in existing_ids:
            return incident_id
    
    # If we somehow exhaust attempts, raise an error
    raise ValueError("Failed to generate unique incident ID after maximum attempts")


def validate_incident_id(incident_id: str) -> bool:
    """
    Validate that an incident ID matches the expected format.
    
    Args:
        incident_id: The ID to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not incident_id or not isinstance(incident_id, str):
        return False
    
    # Must be exactly 4 characters
    if len(incident_id) != 4:
        return False
    
    # Must be all uppercase alphanumeric
    return incident_id.isalnum() and incident_id.isupper()
