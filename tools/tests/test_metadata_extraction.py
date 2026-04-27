"""Test OTLP metadata extraction for file info and severity."""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from storage.database import get_session
from storage.incident_repository import IncidentRepository
from utils.code_fetcher import CodeFetcher

def test_metadata_extraction():
    """Test that file metadata is extracted from OTLP custom attributes."""
    
    print("=" * 80)
    print("TESTING OTLP METADATA EXTRACTION")
    print("=" * 80)
    
    with get_session() as session:
        repo = IncidentRepository(session)
        fetcher = CodeFetcher()
        
        # Get recent incidents
        incidents = repo.get_all(limit=10)
        
        if not incidents:
            print("\n[ERROR] No incidents found in database")
            print("[TIP] Generate test incidents using the MuleSoft error generator")
            return
        
        print(f"\n[OK] Found {len(incidents)} incidents\n")
        
        for incident in incidents:
            print(f"\n{'=' * 80}")
            print(f"Incident: {incident.incident_id}")
            print(f"Title: {incident.error_title}")
            print(f"Severity: {incident.severity}")
            print(f"Status: {incident.status}")
            print(f"-" * 80)
            
            # Check metadata
            metadata_dict = incident.metadata if isinstance(incident.metadata, dict) else {}
            if metadata_dict:
                print(f"[OK] Metadata exists: {len(metadata_dict)} keys")
                
                if 'custom_attributes' in metadata_dict:
                    attrs = metadata_dict['custom_attributes']
                    print(f"[OK] Custom attributes found: {len(attrs)} attributes")
                    
                    # Check for file-related attributes
                    file_attrs = {
                        k: v for k, v in attrs.items() 
                        if any(term in k.lower() for term in ['file', 'line', 'source'])
                    }
                    
                    if file_attrs:
                        print("\n[FILE] File-related attributes:")
                        for key, value in file_attrs.items():
                            print(f"   {key}: {value}")
                    else:
                        print("\n[WARN] No file-related attributes found in custom_attributes")
                    
                    # Check severity info
                    severity_attrs = {
                        k: v for k, v in attrs.items()
                        if any(term in k.lower() for term in ['severity', 'level', 'priority'])
                    }
                    
                    if severity_attrs:
                        print("\n[SEVERITY] Severity-related attributes:")
                        for key, value in severity_attrs.items():
                            print(f"   {key}: {value}")
                else:
                    print("[WARN] No custom_attributes in metadata")
                    print(f"   Metadata keys: {list(metadata_dict.keys())}")
            else:
                print("[ERROR] No metadata available")
            
            # Test file extraction
            print(f"\n[TEST] Testing file info extraction...")
            file_info = fetcher.extract_error_file_info(
                raw_log=incident.raw_log or '',
                stack_trace=incident.stack_trace or '',
                error_title=incident.error_title or '',
                metadata=metadata_dict
            )
            
            if file_info:
                print(f"[SUCCESS] File info extracted:")
                print(f"   File: {file_info.get('file_path')}")
                print(f"   Line: {file_info.get('line_number')}")
                print(f"   Type: {file_info.get('file_type')}")
                print(f"   Source: {file_info.get('source')}")
            else:
                print("[ERROR] Could not extract file info")
                print(f"   Try checking raw_log and stack_trace for file references")
            
            print(f"\n{'=' * 80}")

if __name__ == "__main__":
    test_metadata_extraction()
