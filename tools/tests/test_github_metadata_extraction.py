"""Test GitHub metadata extraction from OTLP messages."""
import requests
import json
import time
from storage.database import get_session
from storage.incident_repository import IncidentRepository

def test_github_metadata_extraction():
    """Test that GitHub metadata is properly extracted from OTLP messages."""
    
    # Load sample OTLP message with GitHub attributes
    with open('sample-otel-message.json', 'r') as f:
        otlp_message = json.load(f)
    
    print("[SENDING] OTLP message with GitHub metadata...")
    print("   - github.repo: avinash-ai-langchain/order-processing-service")
    print("   - github.branch: main")
    print("   - github.file_path: src/main/mule/order-validation.xml")
    
    # Send to ingestion API
    response = requests.post(
        'http://localhost:8000/v1/logs',
        json=otlp_message,
        headers={'Content-Type': 'application/json'}
    )
    
    print(f"[OK] Response: {response.status_code}")
    
    if response.status_code != 200:
        print(f"[FAILED] Failed to send OTLP message: {response.text}")
        return False
    
    # Wait for processing
    print("\n[WAITING] Waiting 5 seconds for workflow to process...")
    time.sleep(5)
    
    # Check if incident was created with GitHub metadata
    with get_session() as session:
        repo = IncidentRepository(session)
        incidents = repo.get_all()
        
        if not incidents:
            print("[ERROR] No incidents found")
            return False
        
        # Find the most recent incident
        latest_incident = max(incidents, key=lambda x: x.created_at)
        
        print(f"\n[INCIDENT] Latest Incident: {latest_incident.incident_id}")
        print(f"   App: {latest_incident.app_name}")
        print(f"   Error: {latest_incident.error_title}")
        
        # Check metadata
        print(f"\n[METADATA CHECK]")
        if latest_incident.metadata:
            metadata_dict = latest_incident.metadata
            if hasattr(metadata_dict, '__dict__'):
                metadata_dict = metadata_dict.__dict__
            
            print(f"   Metadata type: {type(metadata_dict)}")
            print(f"   Metadata: {metadata_dict}")
            
            # Try to access custom_attributes
            if 'custom_attributes' in metadata_dict:
                attrs = metadata_dict['custom_attributes']
                print(f"\n   Custom Attributes:")
                for key, value in attrs.items():
                    if key.startswith('github'):
                        print(f"      [OK] {key}: {value}")
            else:
                print(f"   [WARNING] No custom_attributes in metadata")
        else:
            print(f"   [WARNING] No metadata found")
        
        # Check if repo_full_name was extracted
        print(f"\n[EXTRACTION RESULTS]")
        print(f"   repo_full_name: {latest_incident.repo_full_name or '[NOT EXTRACTED]'}")
        print(f"   error_file_path: {latest_incident.error_file_path or '[NOT EXTRACTED]'}")
        print(f"   error_line_number: {latest_incident.error_line_number or '[NOT EXTRACTED]'}")
        
        # Success criteria
        success = all([
            latest_incident.repo_full_name == 'avinash-ai-langchain/order-processing-service',
            latest_incident.error_file_path == 'src/main/mule/order-validation.xml',
            latest_incident.error_line_number == 45
        ])
        
        if success:
            print(f"\n[SUCCESS] GitHub metadata extracted correctly!")
            return True
        else:
            print(f"\n[FAILED] GitHub metadata not extracted properly")
            return False

if __name__ == '__main__':
    try:
        success = test_github_metadata_extraction()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
