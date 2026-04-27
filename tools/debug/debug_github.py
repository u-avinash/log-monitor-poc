from integrations.github_client import GitHubClient
from config.settings import get_settings
import requests

settings = get_settings()
client = GitHubClient()

print("="*80)
print("DEBUGGING GITHUB FILE ACCESS")
print("="*80)

repo_name = "mulesoft-order-service"
repo_full_name = f"{settings.github_org}/{repo_name}"
file_path = "src/main/mule/order-flows.xml"

print(f"\nOrg: {settings.github_org}")
print(f"Repo: {repo_name}")
print(f"Full Repo Name: {repo_full_name}")
print(f"File: {file_path}")

# Try to list contents of the repo
url = f"https://api.github.com/repos/{repo_full_name}/contents/src/main/mule"
headers = {
    "Authorization": f"token {client.token}",
    "Accept": "application/vnd.github.v3+json"
}

print(f"\nChecking directory: src/main/mule")
response = requests.get(url, headers=headers)
print(f"Status: {response.status_code}")

if response.status_code == 200:
    files = response.json()
    print(f"\nFiles in src/main/mule:")
    for file in files:
        print(f"  - {file['name']}")
else:
    print(f"Error: {response.text}")

# Check what get_file_content returns
print(f"\nTrying get_file_content...")
content = client.get_file_content(repo_full_name, file_path)
print(f"Result: {content[:100] if content else 'None'}")
