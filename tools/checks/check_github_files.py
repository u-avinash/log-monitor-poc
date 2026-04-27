from integrations.github_client import GitHubClient

client = GitHubClient()

print("="*80)
print("CHECKING FILES IN GITHUB REPOS")
print("="*80)

# Check incident #2 file
print("\nIncident #2: OutOfMemoryError in mulesoft-order-service")
print("  Looking for: src/main/mule/order-flows.xml")
content1 = client.get_file_content('mulesoft-order-service', 'src/main/mule/order-flows.xml')
print(f"  File exists: {bool(content1)}")
if not content1:
    print("  ❌ FILE NOT FOUND - This is why code fetching fails!")

# Check incident #5 file
print("\nIncident #5: DataWeave::NullPointerException in mulesoft-payment-service")
print("  Looking for: src/main/resources/dataweave/transform-payment-request.dwl")
content2 = client.get_file_content('mulesoft-payment-service', 'src/main/resources/dataweave/transform-payment-request.dwl')
print(f"  File exists: {bool(content2)}")
if not content2:
    print("  ❌ FILE NOT FOUND - This is why code fetching fails!")

print("\n" + "="*80)
print("SUMMARY")
print("="*80) 

if content1 and content2:
    print("✅ All files exist - code fetching should work")
else:
    print("❌ Some files missing - need to recreate repositories")
