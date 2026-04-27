"""
Test script specifically for order-processing-service OTLP integration.
This verifies that errors from order-processing-service reach the UI properly.
"""
import requests
import json
import time
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def load_sample_message():
    """Load the sample OTLP message for order-processing-service."""
    sample_path = Path(__file__).parent.parent.parent / "sample-otel-message.json"
    with open(sample_path, 'r') as f:
        return json.load(f)


def send_otlp_message(api_url="http://localhost:8000"):
    """Send the order-processing-service OTLP message to the API."""
    print("=" * 80)
    print("Testing order-processing-service OTLP Integration")
    print("=" * 80)
    
    # Load sample message
    print("\n1. Loading sample OTLP message...")
    payload = load_sample_message()
    
    app_name = payload['resourceLogs'][0]['resource']['attributes'][0]['value']['stringValue']
    print(f"   ✓ Application: {app_name}")
    
    environment = payload['resourceLogs'][0]['resource']['attributes'][2]['value']['stringValue']
    print(f"   ✓ Environment: {environment}")
    
    severity = payload['resourceLogs'][0]['scopeLogs'][0]['logRecords'][0]['severityText']
    print(f"   ✓ Severity: {severity}")
    
    # Send to API
    print(f"\n2. Sending to {api_url}/v1/logs...")
    
    try:
        response = requests.post(
            f"{api_url}/v1/logs",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        print(f"   ✓ Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n3. API Response:")
            print(f"   - Status: {result['status']}")
            print(f"   - Message: {result['message']}")
            
            if 'stats' in result:
                print(f"\n   Statistics:")
                for key, value in result['stats'].items():
                    print(f"   - {key}: {value}")
            
            if 'created_incidents' in result and result['created_incidents']:
                print(f"\n4. Created Incidents:")
                for incident in result['created_incidents']:
                    print(f"   - ID: {incident['incident_id']}")
                    print(f"     Severity: {incident['severity']}")
                    print(f"     App: {incident['app_name']}")
                    print(f"     Environment: {incident['environment']}")
                
                print("\n   ✓ Incident created successfully!")
                return True, result['created_incidents'][0]['incident_id']
            else:
                print("\n   ⚠ Warning: No incidents were created")
                print("   This could mean:")
                print("   - The error severity was too low (only ERROR and above create incidents)")
                print("   - The error was deduplicated (already exists)")
                return False, None
        else:
            print(f"\n   ✗ Error: {response.text}")
            return False, None
            
    except requests.exceptions.ConnectionError:
        print(f"\n   ✗ Error: Could not connect to {api_url}")
        print(f"   Make sure the ingestion API is running:")
        print(f"   python ingestion/api.py")
        return False, None
    except Exception as e:
        print(f"\n   ✗ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False, None


def check_incident_status(incident_id, api_url="http://localhost:8000"):
    """Check the status of the created incident."""
    print("\n" + "=" * 80)
    print(f"Checking Incident Status: {incident_id}")
    print("=" * 80)
    
    try:
        response = requests.get(f"{api_url}/incidents/{incident_id}")
        
        if response.status_code == 200:
            incident = response.json()
            print(f"\n✓ Incident Found:")
            print(f"  - ID: {incident['incident_id']}")
            print(f"  - App: {incident['app_name']}")
            print(f"  - Environment: {incident['environment']}")
            print(f"  - Severity: {incident['severity']}")
            print(f"  - Status: {incident['status']}")
            print(f"  - Title: {incident['error_title'][:80]}...")
            
            if incident.get('github_repo'):
                print(f"\n  GitHub Integration:")
                print(f"  - Repo: {incident['github_repo']}")
                print(f"  - Branch: {incident.get('github_branch', 'N/A')}")
            
            if incident.get('jira_ticket_id'):
                print(f"\n  JIRA Integration:")
                print(f"  - Ticket: {incident['jira_ticket_id']}")
            
            return True
        else:
            print(f"\n✗ Could not retrieve incident: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"\n✗ Error checking incident: {str(e)}")
        return False


def check_app_repo_mapping():
    """Verify that order-processing-service is in the app repo mapping."""
    print("\n" + "=" * 80)
    print("Checking Application-to-Repository Mapping")
    print("=" * 80)
    
    mapping_path = Path(__file__).parent.parent.parent / "config" / "app_repo_mapping.yaml"
    
    try:
        with open(mapping_path, 'r') as f:
            content = f.read()
        
        if 'order-processing-service:' in content:
            print("\n✓ order-processing-service mapping found!")
            
            # Extract the mapping details
            lines = content.split('\n')
            in_order_section = False
            for i, line in enumerate(lines):
                if 'order-processing-service:' in line:
                    in_order_section = True
                    print(f"\n  Mapping configuration:")
                    print(f"  {line}")
                elif in_order_section:
                    if line.strip() and not line.strip().startswith('#'):
                        if line.startswith('  ') and not line.startswith('    '):
                            # Next top-level entry, stop
                            break
                        print(f"  {line}")
                    elif not line.strip():
                        break
            
            return True
        else:
            print("\n✗ order-processing-service NOT FOUND in mapping!")
            print("\n  To fix this, add the following to config/app_repo_mapping.yaml:")
            print("\n  order-processing-service:")
            print("    repo: avinash-ai-langchain/order-processing-service")
            print("    branch: main")
            print("    description: 'Order processing service'")
            return False
            
    except Exception as e:
        print(f"\n✗ Error reading mapping file: {str(e)}")
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test order-processing-service OTLP integration"
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Base URL of the ingestion API (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--check-mapping-only",
        action="store_true",
        help="Only check the app-to-repo mapping configuration"
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 80)
    print("Order Processing Service - OTLP Integration Test")
    print("=" * 80)
    print(f"API URL: {args.api_url}")
    print("=" * 80)
    
    # Always check mapping first
    mapping_ok = check_app_repo_mapping()
    
    if args.check_mapping_only:
        exit(0 if mapping_ok else 1)
    
    if not mapping_ok:
        print("\n⚠ Warning: Mapping not configured correctly!")
        print("The incident may be created but workflow steps may fail.")
        print("\nContinuing with test anyway...\n")
    
    # Send OTLP message
    success, incident_id = send_otlp_message(args.api_url)
    
    if success and incident_id:
        # Wait a moment for processing
        print("\n⏳ Waiting 2 seconds for incident processing...")
        time.sleep(2)
        
        # Check incident status
        check_incident_status(incident_id, args.api_url)
        
        print("\n" + "=" * 80)
        print("Test Summary")
        print("=" * 80)
        print("✓ OTLP message sent successfully")
        print("✓ Incident created in database")
        print(f"✓ Incident ID: {incident_id}")
        
        if mapping_ok:
            print("✓ Repository mapping configured")
            print("\nNext steps:")
            print(f"1. Check the UI at http://localhost:8501")
            print(f"2. Verify the incident appears with ID: {incident_id}")
            print(f"3. Check that workflow steps complete (RCA, fix generation, etc.)")
        else:
            print("⚠ Repository mapping NOT configured")
            print("\nTo complete the fix:")
            print("1. Add order-processing-service to config/app_repo_mapping.yaml")
            print("2. Ensure the GitHub repository exists")
            print("3. Re-run this test")
        
        print("=" * 80 + "\n")
        exit(0)
    else:
        print("\n" + "=" * 80)
        print("Test Failed")
        print("=" * 80)
        print("✗ Could not create incident")
        print("\nTroubleshooting:")
        print("1. Make sure the ingestion API is running:")
        print("   python ingestion/api.py")
        print("2. Check the API logs for errors")
        print("3. Verify the OTLP message format in sample-otel-message.json")
        print("=" * 80 + "\n")
        exit(1)
