"""Test script to verify GitHub, Jira, and Slack integrations."""
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent))

from config.settings import get_settings


def test_env_variables():
    """Check which environment variables are configured."""
    print("\n" + "="*60)
    print("Environment Configuration Check")
    print("="*60)
    
    settings = get_settings()
    
    # LLM Provider
    print("\n[LLM Provider]")
    print(f"Provider: {settings.llm_provider}")
    print(f"Model: {settings.llm_model}")
    
    if settings.llm_provider == "openai":
        if settings.openai_api_key:
            print(f"OpenAI API Key: Configured ({settings.openai_api_key[:10]}...)")
        else:
            print("OpenAI API Key: NOT CONFIGURED")
    
    # GitHub Integration
    print("\n[GitHub Integration]")
    if settings.github_token:
        print(f"GitHub Token: Configured ({settings.github_token[:10]}...)")
        print(f"GitHub Org: {settings.github_org}")
        print(f"GitHub Repo: {settings.github_default_repo}")
    else:
        print("GitHub Token: NOT CONFIGURED")
    
    # Jira Integration
    print("\n[Jira Integration]")
    print(f"Jira URL: {settings.jira_url}")
    if settings.jira_email:
        print(f"Jira Email: {settings.jira_email}")
    else:
        print("Jira Email: NOT CONFIGURED")
    
    if settings.jira_api_token:
        print(f"Jira API Token: Configured ({settings.jira_api_token[:10]}...)")
    else:
        print("Jira API Token: NOT CONFIGURED")
    
    print(f"Jira Project Key: {settings.jira_project_key}")
    
    # Slack Integration
    print("\n[Slack Integration]")
    if settings.slack_webhook_url:
        print(f"Slack Webhook: Configured")
    else:
        print("Slack Webhook: NOT CONFIGURED")
    
    # Auto-Fix Configuration
    print("\n[Auto-Fix Configuration]")
    print(f"Auto-Fix Enabled: {settings.auto_fix_enabled}")
    print(f"Requires Approval: {settings.auto_fix_requires_approval}")
    print(f"Auto PR Create: {settings.auto_pr_create}")
    print(f"Severity Threshold: {settings.auto_fix_severity_threshold}")
    
    return settings


def test_github_connection(settings):
    """Test GitHub connection."""
    print("\n" + "="*60)
    print("Testing GitHub Connection")
    print("="*60)
    
    if not settings.github_token:
        print("SKIPPED: GitHub token not configured")
        return False
    
    try:
        from integrations.github_client import GitHubClient
        client = GitHubClient()
        
        if not client.client:
            print("FAILED: GitHub client not initialized (invalid token?)")
            return False
        
        # Test authentication
        user = client.client.get_user()
        print(f"SUCCESS: Authenticated as {user.login}")
        
        # Test repository access
        repo_full = f"{settings.github_org}/{settings.github_default_repo}"
        try:
            repo = client.client.get_repo(repo_full)
            print(f"SUCCESS: Repository {repo.full_name} accessible")
            print(f"  Push access: {repo.permissions.push}")
            return True
        except Exception as e:
            print(f"WARNING: Repository {repo_full} not accessible: {e}")
            print("  You may need to update GITHUB_ORG and GITHUB_DEFAULT_REPO in .env")
            return False
        
    except Exception as e:
        print(f"FAILED: {str(e)}")
        return False


def test_jira_connection(settings):
    """Test Jira connection."""
    print("\n" + "="*60)
    print("Testing Jira Connection")
    print("="*60)
    
    if not settings.jira_email or not settings.jira_api_token:
        print("SKIPPED: Jira credentials not configured")
        return False
    
    try:
        from integrations.jira_client import JiraClient
        client = JiraClient()
        
        if not client.client:
            print("FAILED: Jira client not initialized (invalid credentials?)")
            return False
        
        # Test authentication
        myself = client.client.myself()
        print(f"SUCCESS: Authenticated as {myself['displayName']}")
        
        # Test project access
        try:
            project = client.client.project(settings.jira_project_key)
            print(f"SUCCESS: Project {project['name']} accessible")
            return True
        except Exception as e:
            print(f"WARNING: Project {settings.jira_project_key} not accessible: {e}")
            print("  You may need to update JIRA_PROJECT_KEY in .env")
            return False
        
    except Exception as e:
        print(f"FAILED: {str(e)}")
        return False


def test_slack_connection(settings):
    """Test Slack webhook."""
    print("\n" + "="*60)
    print("Testing Slack Connection")
    print("="*60)
    
    if not settings.slack_webhook_url:
        print("SKIPPED: Slack webhook not configured")
        return False
    
    try:
        import requests
        
        # Send test message
        response = requests.post(
            settings.slack_webhook_url,
            json={"text": "Test message from Log Monitor POC"},
            timeout=10
        )
        
        if response.status_code == 200:
            print("SUCCESS: Slack webhook is working")
            return True
        else:
            print(f"FAILED: Status code {response.status_code}")
            return False
        
    except Exception as e:
        print(f"FAILED: {str(e)}")
        return False


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Log Monitor POC - Integration Tests")
    print("="*60)
    
    # Check environment variables
    settings = test_env_variables()
    
    # Test connections
    github_ok = test_github_connection(settings)
    jira_ok = test_jira_connection(settings)
    slack_ok = test_slack_connection(settings)
    
    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)
    print(f"GitHub:  {'PASS' if github_ok else 'FAIL/SKIP'}")
    print(f"Jira:    {'PASS' if jira_ok else 'FAIL/SKIP'}")
    print(f"Slack:   {'PASS' if slack_ok else 'FAIL/SKIP'}")
    
    if not settings.openai_api_key and settings.llm_provider == "openai":
        print("\nWARNING: OpenAI API key not configured!")
        print("The workflow will not be able to generate RCA and fixes.")
    
    print("\n" + "="*60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
