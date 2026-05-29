"""Test script to verify GitHub and Jira credentials."""
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from config.settings import get_settings
from integrations.github_client import GitHubClient
from integrations.jira_client import JiraClient


def test_github():
    """Test GitHub credentials."""
    print("\n" + "="*60)
    print("🔍 Testing GitHub Integration")
    print("="*60)
    
    settings = get_settings()
    
    if not settings.github_token:
        print("❌ GITHUB_TOKEN not configured in .env")
        return False
    
    if not settings.github_repo_owner:
        print("❌ GITHUB_REPO_OWNER not configured in .env")
        return False
    
    if not settings.github_repo_name:
        print("❌ GITHUB_REPO_NAME not configured in .env")
        return False
    
    print(f"✓ Token found: {settings.github_token[:10]}...")
    print(f"✓ Repository: {settings.github_repo_owner}/{settings.github_repo_name}")
    
    try:
        client = GitHubClient()
        
        # Test authentication
        print("\n📡 Testing GitHub API connection...")
        user = client.github.get_user()
        print(f"✅ Authenticated as: {user.login}")
        print(f"   Name: {user.name or 'N/A'}")
        print(f"   Email: {user.email or 'N/A'}")
        
        # Test repository access
        print(f"\n📦 Testing repository access...")
        repo = client.github.get_repo(f"{settings.github_repo_owner}/{settings.github_repo_name}")
        print(f"✅ Repository found: {repo.full_name}")
        print(f"   Description: {repo.description or 'N/A'}")
        print(f"   Private: {repo.private}")
        print(f"   Default Branch: {repo.default_branch}")
        
        # Check permissions
        permissions = repo.permissions
        print(f"\n🔐 Repository Permissions:")
        print(f"   Push: {'✅' if permissions.push else '❌'}")
        print(f"   Pull: {'✅' if permissions.pull else '❌'}")
        print(f"   Admin: {'✅' if permissions.admin else '❌'}")
        
        if not permissions.push:
            print("\n⚠️  WARNING: You don't have push access to this repository!")
            print("   PRs may not be created successfully.")
            return False
        
        print("\n✅ GitHub integration is properly configured!")
        return True
        
    except Exception as e:
        print(f"\n❌ GitHub test failed: {str(e)}")
        return False


def test_jira():
    """Test Jira credentials."""
    print("\n" + "="*60)
    print("🔍 Testing Jira Integration")
    print("="*60)
    
    settings = get_settings()
    
    if not settings.jira_url:
        print("❌ JIRA_URL not configured in .env")
        return False
    
    if not settings.jira_user_email:
        print("❌ JIRA_USER_EMAIL not configured in .env")
        return False
    
    if not settings.jira_api_token:
        print("❌ JIRA_API_TOKEN not configured in .env")
        return False
    
    if not settings.jira_project_key:
        print("❌ JIRA_PROJECT_KEY not configured in .env")
        return False
    
    print(f"✓ URL: {settings.jira_url}")
    print(f"✓ User: {settings.jira_user_email}")
    print(f"✓ Project Key: {settings.jira_project_key}")
    print(f"✓ Token: {settings.jira_api_token[:10]}...")
    
    try:
        client = JiraClient()
        
        # Test authentication
        print("\n📡 Testing Jira API connection...")
        myself = client.jira.myself()
        print(f"✅ Authenticated as: {myself['displayName']}")
        print(f"   Email: {myself['emailAddress']}")
        print(f"   Account ID: {myself['accountId']}")
        
        # Test project access
        print(f"\n📦 Testing project access...")
        project = client.jira.project(settings.jira_project_key)
        print(f"✅ Project found: {project['name']}")
        print(f"   Key: {project['key']}")
        print(f"   Type: {project.get('projectTypeKey', 'N/A')}")
        print(f"   Lead: {project.get('lead', {}).get('displayName', 'N/A')}")
        
        # Test issue types
        print(f"\n📋 Available Issue Types:")
        issue_types = client.jira.project_issue_types(settings.jira_project_key)
        for issue_type in issue_types[:5]:  # Show first 5
            print(f"   - {issue_type['name']} (ID: {issue_type['id']})")
        
        print("\n✅ Jira integration is properly configured!")
        return True
        
    except Exception as e:
        print(f"\n❌ Jira test failed: {str(e)}")
        print("\nCommon issues:")
        print("  - Invalid API token")
        print("  - Incorrect project key")
        print("  - Insufficient permissions")
        print("  - Invalid Jira URL")
        return False


def main():
    """Run all credential tests."""
    print("\n" + "="*60)
    print("🔐 Prism AI - Credential Verification")
    print("="*60)
    
    github_ok = test_github()
    jira_ok = test_jira()
    
    print("\n" + "="*60)
    print("📊 Test Summary")
    print("="*60)
    print(f"GitHub: {'✅ PASS' if github_ok else '❌ FAIL'}")
    print(f"Jira:   {'✅ PASS' if jira_ok else '❌ FAIL'}")
    
    if github_ok and jira_ok:
        print("\n✅ All integrations are properly configured!")
        print("\nYou can now:")
        print("  1. Restart the API: python ingestion/api.py")
        print("  2. Restart the UI: python -m uvicorn ui.server:app --host 0.0.0.0 --port 8001 --reload")
        print("  3. Create a test incident to trigger integrations")
        return 0
    else:
        print("\n❌ Some integrations failed. Please fix the issues above.")
        print("\nTroubleshooting:")
        print("  - Double-check credentials in .env file")
        print("  - Ensure tokens have proper permissions")
        print("  - Verify URLs and project keys are correct")
        return 1


if __name__ == "__main__":
    sys.exit(main())
