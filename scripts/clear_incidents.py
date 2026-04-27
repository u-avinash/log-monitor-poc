"""Clear all incidents from the database."""
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.database import get_db, Incident
from sqlalchemy.orm import Session

def clear_all_incidents():
    """Delete all incidents from the database."""
    db: Session = next(get_db())
    
    try:
        # Delete all incidents
        count = db.query(Incident).count()
        db.query(Incident).delete()
        db.commit()
        
        print(f"[OK] Successfully deleted {count} incident(s) from the database.")
        
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Error clearing incidents: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    print("Clearing all incidents from database...")
    clear_all_incidents()
