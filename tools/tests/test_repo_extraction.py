"""Test repository extraction logic"""
from integrations.github_client import GitHubClient
from config.settings import get_settings

settings = get_settings()
client = GitHubClient()

# Test the app names from our simulator with their actual file paths
test_cases = [
    ("payment-processing-app", "src/main/mule/payment-processing.xml"),
    ("order-management-app", "src/main/mule/order-flows.xml"),
    ("inventory-app", "src/main/mule/inventory.xml")
]

print("="*80)
print("TESTING REPO EXTRACTION AND FILE ACCESS")
print("="*80)
print(f"\nConfigured org: {settings.github_org}\n")

for app_name, file_path in test_cases:
    print(f"\nApp Name: {app_name}")
    
    # Simulate what RCA generator does
    raw_log = f"Error in {app_name}"  # Minimal log
    
    repo_full_name = client.extract_repo_from_log(raw_log, app_name)
    print(f"  Repo: {repo_full_name}")
    
    # Check if this repo exists and can fetch files
    if repo_full_name:
        print(f"  Testing: {file_path}")
        content = client.get_file_content(repo_full_name, file_path)
        if content:
            print(f"  [OK] SUCCESS - File fetched ({len(content)} bytes)")
        else:
            print(f"  [FAIL] Could not fetch file")
