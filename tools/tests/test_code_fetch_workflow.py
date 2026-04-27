"""Test the exact workflow used in RCA generation."""
from utils.code_fetcher import CodeFetcher
from integrations.github_client import GitHubClient

# Simulate what happens in RCA generator
app_name = "mulesoft-payment-service"
raw_log = """Application: mulesoft-payment-service
Environment: production
Cloud Platform: cloudhub-2.0
Region: us-east-1
Mule Version: 4.4.0

Error Type: NullPointerException
Flow: processPaymentFlow
File: src/main/mule/payment-processing.xml
Line: 19
Thread: worker-1

Message: java.lang.NullPointerException: Cannot read field 'name' because 'customer' is null
"""

stack_trace = """java.lang.NullPointerException: Cannot read field 'name' because 'customer' is null
    at org.mule.extension.db.internal.DbConnector.select(payment-processing.xml:19)
"""

error_title = "[FATAL] NullPointerException in processPaymentFlow"

print("=" * 80)
print("TESTING CODE FETCH WORKFLOW")
print("=" * 80)

# Step 1: Initialize clients
print("\n[Step 1] Initialize clients...")
code_fetcher = CodeFetcher()
github_client = GitHubClient()
print(f"  GitHub client initialized: {github_client.client is not None}")

# Step 2: Extract file info
print("\n[Step 2] Extract file info from logs...")
file_info = code_fetcher.extract_error_file_info(raw_log, stack_trace, error_title)
print(f"  File info: {file_info}")

# Step 3: Get repo name
print("\n[Step 3] Extract repo from log...")
repo_full_name = None
if file_info and github_client.client:
    repo_full_name = github_client.extract_repo_from_log(raw_log, app_name)
    print(f"  Repo: {repo_full_name}")
else:
    print(f"  Skipped (file_info={file_info}, github_client={github_client.client is not None})")

# Step 4: Fetch code
print("\n[Step 4] Fetch code from GitHub...")
if repo_full_name and file_info:
    print(f"  Calling fetch_code_for_analysis({repo_full_name}, {file_info['file_path']}, {file_info.get('line_number')})")
    code_context = code_fetcher.fetch_code_for_analysis(
        repo_full_name=repo_full_name,
        file_path=file_info['file_path'],
        line_number=file_info.get('line_number'),
        context_lines=20
    )
    
    if code_context:
        print(f"  [OK] Success!")
        print(f"    - File: {code_context['file_path']}")
        print(f"    - Repo: {code_context['repo']}")
        print(f"    - Lines: {code_context['line_count']}")
        print(f"    - Error line: {code_context.get('error_line_number')}")
        print(f"\n  Context snippet preview:")
        print("  " + "-" * 76)
        for line in code_context['context_snippet'].split('\n')[:10]:
            print(f"  {line}")
    else:
        print(f"  [FAIL] Failed to fetch code")
else:
    print(f"  Skipped (repo={repo_full_name}, file_info={file_info})")

print("\n" + "=" * 80)
