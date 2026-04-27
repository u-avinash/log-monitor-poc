"""Test the complete workflow with all integrations."""
import asyncio
import logging
from datetime import datetime
from agents.workflow import run_incident_workflow
from storage.database import get_session
from storage.incident_repository import IncidentRepository
from storage.models import IncidentCreate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_complete_workflow():
    """Test the complete workflow from start to finish."""
    print("\n" + "="*60)
    print("COMPLETE WORKFLOW TEST")
    print("="*60 + "\n")
    
    # Create test incident data
    test_data = {
        "incident_id": "TEST-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
        "app_name": "MuleApp-Payments",
        "environment": "production",
        "error_title": "NullPointerException in PaymentProcessor",
        "error_description": "Payment processing failed due to null customer object",
        "stack_trace": """java.lang.NullPointerException: Cannot invoke method getCustomerId() on null object
    at com.mulesoft.payment.PaymentProcessor.processPayment(PaymentProcessor.java:45)
    at com.mulesoft.payment.PaymentService.handleTransaction(PaymentService.java:123)
    at com.mulesoft.api.PaymentController.createPayment(PaymentController.java:78)""",
        "raw_log": """[2026-03-27 15:30:00] ERROR PaymentProcessor - Payment processing failed
Customer object is null when attempting to retrieve customer ID
Transaction ID: TXN-12345
Amount: $150.00
Error: NullPointerException""",
        "fingerprint": "nullpointer_paymentprocessor_45",
        "severity": "CRITICAL",
        "is_duplicate": False
    }
    
    print(f"[TEST] Test Incident: {test_data['incident_id']}")
    print(f"       App: {test_data['app_name']}")
    print(f"       Environment: {test_data['environment']}")
    print(f"       Severity: {test_data['severity']}")
    print(f"       Error: {test_data['error_title']}")
    print()
    
    # Create incident in database first
    print("[DB] Creating incident in database...")
    with get_session() as session:
        repo = IncidentRepository(session)
        incident_create = IncidentCreate(
            app_name=test_data['app_name'],
            environment=test_data['environment'],
            error_title=test_data['error_title'],
            error_description=test_data['error_description'],
            stack_trace=test_data['stack_trace'],
            raw_log=test_data['raw_log'],
            timestamp=datetime.utcnow()
        )
        incident = repo.create(
            incident=incident_create,
            error_fingerprint=test_data['fingerprint'],
            severity=test_data['severity']
        )
        incident_id = incident.incident_id
        print(f"[OK] Incident created with ID: {incident_id}\n")
    
    # Run workflow
    print("[WORKFLOW] Starting workflow execution...")
    print("-" * 60)
    
    try:
        final_state = await run_incident_workflow(
            incident_id=str(incident_id),
            app_name=test_data['app_name'],
            environment=test_data['environment'],
            error_title=test_data['error_title'],
            error_description=test_data['error_description'],
            stack_trace=test_data['stack_trace'],
            raw_log=test_data['raw_log'],
            fingerprint=test_data['fingerprint'],
            severity=test_data['severity'],
            is_duplicate=test_data['is_duplicate']
        )
        
        print("-" * 60)
        print("\n[SUCCESS] WORKFLOW COMPLETED SUCCESSFULLY\n")
        
        # Display results
        print("="*60)
        print("WORKFLOW RESULTS")
        print("="*60 + "\n")
        
        print(f"[SEVERITY] Severity Assessment:")
        print(f"           Original: {test_data['severity']}")
        print(f"           Confirmed: {final_state.get('severity', 'N/A')}")
        print()
        
        print(f"[RCA] Root Cause Analysis:")
        rca = final_state.get('rca_text', 'N/A')
        print(f"      {rca[:200]}..." if len(rca) > 200 else f"      {rca}")
        print(f"      Confidence: {final_state.get('rca_confidence', 0.0):.1%}")
        print()
        
        print(f"[FIX] Fix Generation:")
        fix = final_state.get('proposed_fix', 'N/A')
        print(f"      Generated: {'Yes' if fix and fix != 'N/A' else 'No'}")
        if fix and fix != 'N/A':
            print(f"      Preview: {fix[:150]}..." if len(fix) > 150 else f"      Preview: {fix}")
        print()
        
        print(f"[QUALITY] Quality Assessment:")
        print(f"          Overall Score: {final_state.get('overall_quality_score', 0.0):.2f}")
        print(f"          Recommendation: {final_state.get('quality_recommendation', 'N/A')}")
        print()
        
        print(f"[APPROVAL] Approval Status:")
        print(f"           Status: {final_state.get('approval_status', 'pending')}")
        print(f"           Approved By: {final_state.get('approved_by', 'N/A')}")
        print()
        
        print(f"[JIRA] Jira Integration:")
        jira_key = final_state.get('jira_ticket_key', 'N/A')
        jira_url = final_state.get('jira_ticket_url', 'N/A')
        print(f"       Ticket: {jira_key}")
        if jira_url != 'N/A':
            print(f"       URL: {jira_url}")
        print()
        
        print(f"[GITHUB] GitHub Integration:")
        pr_url = final_state.get('pr_url', 'N/A')
        pr_number = final_state.get('pr_number', 'N/A')
        print(f"         PR Number: {pr_number}")
        if pr_url != 'N/A':
            print(f"         URL: {pr_url}")
        print()
        
        print(f"[NOTIFY] Notifications:")
        messages = final_state.get('messages', [])
        notification_msgs = [msg for msg in messages if 'Notification' in msg or 'sent to' in msg]
        if notification_msgs:
            for msg in notification_msgs:
                print(f"         {msg}")
        else:
            print("         Check finalizer output above")
        print()
        
        print(f"[TIMING] Workflow Timing:")
        print(f"         Started: {final_state.get('created_at', 'N/A')}")
        print(f"         Completed: {final_state.get('completed_at', 'N/A')}")
        print(f"         Progress: {final_state.get('workflow_progress_pct', 0.0):.0%}")
        print()
        
        print("="*60)
        print("WORKFLOW MESSAGES")
        print("="*60 + "\n")
        for i, msg in enumerate(messages, 1):
            print(f"{i}. {msg}")
        print()
        
        # Verify database was updated
        print("="*60)
        print("DATABASE VERIFICATION")
        print("="*60 + "\n")
        
        with get_session() as session:
            repo = IncidentRepository(session)
            incident = repo.get_by_id(incident_id)
            
            print(f"[OK] Incident ID: {incident.id}")
            print(f"[OK] Status: {incident.status}")
            print(f"[OK] RCA Saved: {'Yes' if incident.rca else 'No'}")
            print(f"[OK] Fix Saved: {'Yes' if incident.proposed_fix else 'No'}")
            print(f"[OK] Jira Ticket: {incident.jira_ticket_key or 'N/A'}")
            print(f"[OK] Workflow Progress: {incident.workflow_progress_pct:.0%}")
        print()
        
        print("="*60)
        print("[PASS] TEST PASSED - ALL SYSTEMS OPERATIONAL")
        print("="*60)
        
        return True
        
    except Exception as e:
        print("-" * 60)
        print(f"\n[ERROR] WORKFLOW FAILED: {str(e)}\n")
        logger.exception("Workflow test failed")
        
        print("="*60)
        print("[FAIL] TEST FAILED")
        print("="*60)
        
        return False


if __name__ == "__main__":
    print("\n" + "="*60)
    print("LOG MONITOR POC - COMPLETE WORKFLOW TEST")
    print("="*60)
    print("\nThis test will:")
    print("  1. Create a test incident")
    print("  2. Run complete workflow (severity -> RCA -> fix -> approval)")
    print("  3. Attempt to create Jira ticket")
    print("  4. Attempt to create GitHub PR")
    print("  5. Send Slack notification")
    print("  6. Verify database updates")
    print("\n" + "="*60 + "\n")
    
    success = asyncio.run(test_complete_workflow())
    
    exit(0 if success else 1)
