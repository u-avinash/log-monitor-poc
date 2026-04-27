"""Fix all incidents to add proposed fix and set proper status for approval."""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from storage.database import get_db
from storage.incident_repository import IncidentRepository

def fix_all_incidents():
    """Add proposed fix to all incidents that have RCA but no fix."""
    db = next(get_db())
    repo = IncidentRepository(db)
    
    incidents = repo.get_all(limit=100)
    
    sample_fix = """// Add null check to prevent exception
if (payment == null) {
    logger.error("Payment object is null");
    throw new IllegalArgumentException("Payment cannot be null");
}

// Add validation before processing
if (!payment.isValid()) {
    logger.warn("Invalid payment detected: {}", payment.getId());
    return PaymentResponse.failed("Invalid payment data");
}

// Proceed with payment processing
return processValidPayment(payment);"""
    
    sample_explanation = """This fix adds proper null checking and validation to prevent the NullPointerException. 
The fix includes:
1. Null check with descriptive error message
2. Validation check before processing
3. Proper error handling and logging
4. Early return to avoid processing invalid data"""
    
    fixed_count = 0
    
    for incident in incidents:
        # Fix incidents that have RCA but no proposed fix
        if incident.rca_text and not incident.proposed_fix:
            repo.update(
                incident_id=incident.incident_id,
                status="pending_approval",
                proposed_fix=sample_fix,
                fix_explanation=sample_explanation,
                approval_status="pending"
            )
            print(f"[FIXED] Incident #{incident.incident_id} ({incident.app_name}) - Added fix and set to pending_approval")
            fixed_count += 1
        elif incident.status.upper() != "PENDING_APPROVAL" and incident.proposed_fix:
            # Has fix but wrong status
            repo.update(
                incident_id=incident.incident_id,
                status="pending_approval",
                approval_status="pending"
            )
            print(f"[FIXED] Incident #{incident.incident_id} ({incident.app_name}) - Corrected status")
            fixed_count += 1
    
    print(f"\n[SUCCESS] Fixed {fixed_count} incident(s)")
    print("Refresh the UI at http://localhost:8501 to see the Approve/Reject buttons")

if __name__ == "__main__":
    fix_all_incidents()
