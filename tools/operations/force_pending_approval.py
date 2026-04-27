"""Force incident #4 to pending_approval status for UI testing."""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from storage.database import get_db
from storage.incident_repository import IncidentRepository

def force_approval_status():
    """Manually set incident to pending_approval."""
    db = next(get_db())
    repo = IncidentRepository(db)
    
    incident_id = 4
    
    # Add a dummy fix if none exists
    repo.update(
        incident_id=incident_id,
        status="pending_approval",
        proposed_fix="// Sample fix code\nif (payment == null) {\n    throw new IllegalArgumentException(\"Payment cannot be null\");\n}",
        fix_explanation="Added null check to prevent NullPointerException",
        approval_status="pending"
    )
    
    print(f"[SUCCESS] Updated incident #{incident_id} to PENDING_APPROVAL")
    print("Refresh the UI at http://localhost:8501 to see the Approve/Reject buttons")

if __name__ == "__main__":
    force_approval_status()
