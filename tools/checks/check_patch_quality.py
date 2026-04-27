"""
Check quality of generated patch files.

This script analyzes .patch files to ensure they have reasonable
deletions/additions ratios and proper formatting.
"""
import sys
import os
from pathlib import Path
import re

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def analyze_patch_file(patch_path: Path) -> dict:
    """
    Analyze a patch file and return quality metrics.
    
    Returns:
        dict with:
        - deletions: number of deleted lines
        - additions: number of added lines
        - ratio: deletions/additions ratio
        - has_proper_headers: bool
        - issues: list of issues found
    """
    with open(patch_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    
    deletions = 0
    additions = 0
    has_proper_headers = False
    issues = []
    
    # Count deletions and additions
    in_diff = False
    for line in lines:
        if line.startswith('---') and not line.startswith('---#'):
            in_diff = True
            # Check for proper header format
            next_idx = lines.index(line) + 1
            if next_idx < len(lines) and lines[next_idx].startswith('+++'):
                has_proper_headers = True
            else:
                issues.append("Missing +++ header after --- header")
        
        if in_diff:
            if line.startswith('-') and not line.startswith('---'):
                deletions += 1
            elif line.startswith('+') and not line.startswith('+++'):
                additions += 1
    
    # Calculate ratio
    if additions > 0:
        ratio = deletions / additions
    elif deletions > 0:
        ratio = float('inf')
    else:
        ratio = 0
    
    # Quality checks
    if ratio > 50:
        issues.append(f"Excessive deletion ratio: {ratio:.1f}:1 (likely entire file replaced)")
    elif ratio > 10:
        issues.append(f"High deletion ratio: {ratio:.1f}:1 (review recommended)")
    
    if deletions == 0 and additions == 0:
        issues.append("No actual changes found in patch")
    
    if not has_proper_headers:
        issues.append("Missing or malformed diff headers")
    
    # Check for concatenated headers (bug indicator)
    for line in lines:
        if '---' in line and '+++' in line and line.startswith('---'):
            issues.append("CRITICAL: Concatenated --- and +++ headers on same line")
    
    return {
        'deletions': deletions,
        'additions': additions,
        'ratio': ratio,
        'has_proper_headers': has_proper_headers,
        'issues': issues
    }


def main():
    """Check all patch files in data/patches directory."""
    patch_dir = Path('data/patches')
    
    if not patch_dir.exists():
        print(f"[ERROR] Patch directory not found: {patch_dir}")
        return
    
    patch_files = list(patch_dir.glob('*.patch'))
    
    if not patch_files:
        print(f"[INFO] No patch files found in {patch_dir}")
        return
    
    print(f"Analyzing {len(patch_files)} patch file(s)...\n")
    
    total_issues = 0
    critical_issues = 0
    
    for patch_file in sorted(patch_files):
        metrics = analyze_patch_file(patch_file)
        
        # Determine status
        if metrics['issues']:
            if any('CRITICAL' in issue for issue in metrics['issues']):
                status = '[CRITICAL]'
                critical_issues += 1
            elif metrics['ratio'] > 10:
                status = '[WARNING]'
            else:
                status = '[REVIEW]'
            total_issues += 1
        else:
            status = '[OK]'
        
        print(f"{status} {patch_file.name}")
        print(f"  Deletions: {metrics['deletions']}, Additions: {metrics['additions']}")
        
        if metrics['additions'] > 0:
            print(f"  Ratio: {metrics['ratio']:.2f}:1")
        else:
            print(f"  Ratio: N/A (no additions)")
        
        if metrics['issues']:
            for issue in metrics['issues']:
                print(f"  ! {issue}")
        
        print()
    
    # Summary
    print("=" * 60)
    print(f"Summary:")
    print(f"  Total patches: {len(patch_files)}")
    print(f"  Issues found: {total_issues}")
    print(f"  Critical issues: {critical_issues}")
    print(f"  Clean patches: {len(patch_files) - total_issues}")
    
    if critical_issues > 0:
        print(f"\n[CRITICAL] {critical_issues} patch(es) need immediate attention!")
        print("   Run: python scripts/regenerate_patches.py")
    elif total_issues > 0:
        print(f"\n[WARNING] {total_issues} patch(es) may need review")
    else:
        print("\n[SUCCESS] All patches look good!")


if __name__ == '__main__':
    main()
