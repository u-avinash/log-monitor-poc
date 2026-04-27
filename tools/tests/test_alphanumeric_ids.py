"""Test script for alphanumeric incident IDs."""
import sys
sys.path.append('.')

from utils.id_generator import generate_incident_id, validate_incident_id

print("=" * 80)
print("TESTING ALPHANUMERIC INCIDENT ID SYSTEM")
print("=" * 80)
print()

# Test 1: Generate IDs
print("Test 1: Generating unique IDs")
print("-" * 80)
existing_ids = set()
for i in range(10):
    incident_id = generate_incident_id(existing_ids)
    existing_ids.add(incident_id)
    print(f"Generated ID {i+1}: {incident_id}")
print()

# Test 2: Validate IDs
print("Test 2: Validating ID formats")
print("-" * 80)
test_cases = [
    ("A7CB", True, "Valid: 4 uppercase alphanumeric"),
    ("Z9XY", True, "Valid: 4 uppercase alphanumeric"),
    ("1K2M", True, "Valid: starts with number"),
    ("0000", True, "Valid: all numbers"),
    ("ZZZZ", True, "Valid: all letters"),
    ("abc", False, "Invalid: too short"),
    ("ABCDE", False, "Invalid: too long"),
    ("AB-C", False, "Invalid: contains hyphen"),
    ("abc1", False, "Invalid: lowercase"),
    ("", False, "Invalid: empty string"),
    (None, False, "Invalid: None"),
]

for test_id, expected, description in test_cases:
    result = validate_incident_id(test_id)
    status = "[PASS]" if result == expected else "[FAIL]"
    print(f"{status}: {description} - validate_incident_id('{test_id}') = {result}")
print()

# Test 3: Collision avoidance
print("Test 3: Testing collision avoidance")
print("-" * 80)
existing = {"A7CB", "K9M2", "Z3X7"}
print(f"Existing IDs: {existing}")
print("Generating 5 new IDs that must not collide:")
for i in range(5):
    new_id = generate_incident_id(existing)
    if new_id in existing:
        print(f"[FAIL]: Generated duplicate ID: {new_id}")
    else:
        print(f"[PASS]: Generated unique ID: {new_id}")
        existing.add(new_id)
print()

# Test 4: Statistics
print("Test 4: ID Space Statistics")
print("-" * 80)
print(f"Characters available: A-Z (26) + 0-9 (10) = 36")
print(f"ID length: 4 characters")
print(f"Total possible IDs: 36^4 = {36**4:,}")
print(f"More than sufficient for any practical use case!")
print()

print("=" * 80)
print("ALL TESTS COMPLETED")
print("=" * 80)
