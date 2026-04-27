"""Patch file generation node for agent workflow."""
import logging
import os
from datetime import datetime
from pathlib import Path
import difflib
from agents.state import AgentState
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _validate_patch_format(patch_content: str) -> bool:
    """
    Validate that a patch file has proper format with correct line breaks.
    
    Checks that --- and +++ headers are on separate lines, not concatenated.
    
    Args:
        patch_content: The patch file content to validate
        
    Returns:
        True if patch format is valid, False otherwise
    """
    if not patch_content:
        return False
    
    lines = patch_content.split('\n')
    
    # Check that --- and +++ are on separate lines
    for i, line in enumerate(lines):
        if line.startswith('---'):
            # Check if +++ is incorrectly concatenated on the same line
            if '+++' in line:
                logger.error(f"Invalid patch format: --- and +++ concatenated on same line: {line[:80]}")
                return False
            
            # Check next line for proper +++ header
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if next_line.startswith('+++'):
                    # Found proper format
                    return True
    
    # If we found no --- line, check if patch is empty or has no diff
    has_diff_markers = any(line.startswith('---') or line.startswith('+++') for line in lines)
    if not has_diff_markers:
        logger.warning("No diff markers found in patch content")
        return False
    
    return True


def generate_patch_file_node(state: AgentState) -> AgentState:
    """
    Generate a .patch file from the proposed fix.
    
    This node:
    1. Takes the proposed fix code (targeted changes)
    2. Applies changes to original code to create complete fixed file
    3. Formats it as a unified diff patch
    4. Saves it to data/patches/ directory
    5. Updates state with patch file path
    
    Args:
        state: Current agent state with proposed fix
        
    Returns:
        Updated state with patch file path
    """
    logger.info(f"[Patch Generation] Creating patch file for incident {state['incident_id']}")
    
    try:
        incident_id = state['incident_id']
        proposed_fix = state.get('proposed_fix')
        original_code = state.get('original_code')
        fix_explanation = state.get('fix_explanation', 'Auto-generated fix')
        error_file_path = state.get('error_file_path')
        
        if not proposed_fix:
            logger.warning(f"[Patch Generation] No proposed fix available for incident {incident_id}")
            state['messages'] = state.get('messages', []) + ["⚠️ No fix available to create patch"]
            return state
        
        # Check if we have original code for a proper diff
        if not original_code:
            logger.warning(f"[Patch Generation] No original code available for incident {incident_id}, creating new file patch")
            # Create a patch that adds the new file content
            original_code = ""  # Empty original means we're adding new content
        
        if not error_file_path:
            logger.warning(f"[Patch Generation] No file path available for incident {incident_id}")
            state['messages'] = state.get('messages', []) + ["⚠️ No file path for patch"]
            return state
        
        # NEW: Apply targeted fix to original code to create complete fixed version
        # The LLM now returns only the changed lines, not the entire file
        # We need to intelligently merge the fix into the original code
        fixed_code = _apply_targeted_fix(original_code, proposed_fix, state)
        
        if not fixed_code:
            # CRITICAL: If we can't parse the targeted fix properly, we CANNOT create a valid patch
            # DO NOT use proposed_fix as complete file - this causes massive deletions
            logger.error(f"[Patch Generation] ❌ CRITICAL: Cannot parse targeted fix format for incident {incident_id}")
            logger.error(f"[Patch Generation] LLM did not return proper 'Original Code' + 'Fixed Code' blocks")
            logger.error(f"[Patch Generation] Creating conceptual fix document instead of patch file")
            
            # Create a conceptual fix document instead
            return _create_conceptual_fix_document(
                state=state,
                incident_id=incident_id,
                proposed_fix=proposed_fix,
                fix_explanation=fix_explanation,
                error_file_path=error_file_path
            )
        
        # Create patches directory if it doesn't exist
        patch_dir = Path(settings.patch_output_dir)
        patch_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate patch filename
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        patch_filename = f"incident_{incident_id}_{timestamp}.patch"
        patch_path = patch_dir / patch_filename
        
        # Generate unified diff from original vs complete fixed code
        original_lines = original_code.splitlines(keepends=True)
        fixed_lines = fixed_code.splitlines(keepends=True)
        
        # Ensure lines end with newlines for proper diff
        if original_lines and not original_lines[-1].endswith('\n'):
            original_lines[-1] += '\n'
        if fixed_lines and not fixed_lines[-1].endswith('\n'):
            fixed_lines[-1] += '\n'
        
        # Generate diff without a/ b/ prefixes for Anypoint Studio compatibility
        # Use default lineterm (newline) for proper formatting
        diff = difflib.unified_diff(
            original_lines,
            fixed_lines,
            fromfile=error_file_path,
            tofile=error_file_path
        )
        
        # Join diff lines with proper newlines
        diff_lines = list(diff)
        diff_content = ''.join(diff_lines)
        
        # Clean up the diff - remove multi-line comment blocks that confuse Studio
        # These comments are inside the code changes and can break the patch parser
        import re
        # Remove standalone comment markers that are part of added lines
        diff_content = re.sub(r'\+/\*\s*\n', '', diff_content)
        diff_content = re.sub(r'\+\s*\*/\s*\n', '', diff_content)
        diff_content = re.sub(r'\+\s*\*[^/\n]*\n', '', diff_content)
        
        # Validate patch format to ensure it's properly formatted
        if not _validate_patch_format(diff_content):
            logger.error(f"[Patch Generation] Generated patch has invalid format for incident {incident_id}")
            raise ValueError("Generated patch file has invalid format (missing line breaks in headers)")
        
        # Create comprehensive patch file with universal instructions
        patch_content = f"""{diff_content}

# ============================================================================
# Patch Information
# ============================================================================
# Incident ID: {incident_id}
# Application: {state.get('app_name', 'Unknown')}
# Environment: {state.get('environment', 'Unknown')}
# Severity: {state.get('severity', 'Unknown')}
# Repository: {state.get('repo_full_name', 'Unknown')}
# File: {error_file_path}
# Generated: {datetime.utcnow().isoformat()}
# Format: Universal (works with both Git and Anypoint Studio)
#
# Root Cause Analysis:
# {state.get('rca_text', 'N/A')[:500]}...
#
# Fix Explanation:
# {fix_explanation[:500]}...
#
# ============================================================================
# How to Apply This Patch (Universal - Works for Git and Studio)
# ============================================================================
#
# OPTION 1: Anypoint Studio (GUI Method)
# ----------------------------------------
# 1. In Package Explorer, right-click on your project
# 2. Select "Team" → "Apply Patch..."
# 3. Choose "File" option
# 4. Browse to this patch file
# 5. Keep "Ignore leading path name segments" at 0 (default)
# 6. Click "Next" to preview changes
# 7. Review the changes in the diff view
# 8. Click "Finish" to apply
#
# OPTION 2: Git Command Line
# ----------------------------
# From the root of your repository, run:
#   git apply -p0 {patch_filename}
#
# Note: The -p0 flag tells git not to strip any path components
#
# OPTION 3: Standard Patch Command
# ----------------------------------
# From the root of your repository, run:
#   patch -p0 < {patch_filename}
#
# OPTION 4: Manual Application
# ------------------------------
# - Review the diff above
# - Apply changes to {error_file_path}
# - Test thoroughly before committing
#
# ============================================================================
"""
        
        # Write patch file
        with open(patch_path, 'w', encoding='utf-8') as f:
            f.write(patch_content)
        
        # Update state
        state['patch_path'] = str(patch_path)
        state['fixed_file_content'] = fixed_code  # Store complete fixed file for PR creation
        state['current_node'] = 'generate_patch'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        # Update workflow tracking
        if 'workflow_completed_steps' not in state:
            state['workflow_completed_steps'] = []

        # Add step only if not already completed (prevent duplicates)
        if 'generate_patch' not in state['workflow_completed_steps']:
            state['workflow_completed_steps'].append('generate_patch')
        
        # Calculate progress based on 11 total workflow steps
        state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
        
        state['messages'] = state.get('messages', []) + [
            f"✓ Patch file created: {patch_filename}"
        ]
        
        # Update database
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=incident_id,
                    current_workflow_node='generate_patch',
                    workflow_completed_steps=state['workflow_completed_steps'],
                    workflow_progress_pct=state['workflow_progress_pct'],
                    patch_path=str(patch_path)
                )
        except Exception as db_error:
            logger.warning(f"Failed to update patch info in DB: {db_error}")
        
        logger.info(f"[Patch Generation] Success for incident {incident_id}: {patch_filename}")
        
    except Exception as e:
        logger.error(f"[Patch Generation] Failed for incident {state['incident_id']}: {str(e)}")
        state['error_message'] = f"Patch generation failed: {str(e)}"
        state['messages'] = state.get('messages', []) + [
            f"❌ Patch generation failed: {str(e)}"
        ]
    
    return state


def _apply_targeted_fix(original_code: str, proposed_fix: str, state: AgentState) -> str:
    """
    Apply a targeted fix (only changed lines) to the original code.
    
    The LLM provides:
    - Original Code block (with context)
    - Fixed Code block (with same context)
    
    This function:
    1. Extracts both code blocks from the LLM response
    2. Finds where the original block appears in the full original code
    3. Replaces it with the fixed block
    4. Returns the complete fixed file
    
    Args:
        original_code: Complete original file content
        proposed_fix: LLM response with targeted fix (original + fixed blocks)
        state: Current agent state
        
    Returns:
        Complete fixed code, or None if parsing fails
    """
    logger.info(f"[Patch Generation] Applying targeted fix to original code")
    
    try:
        # Parse the LLM response to extract original and fixed code blocks
        # Expected format:
        # **Original Code (with context):**
        # ```lang
        # [code]
        # ```
        # **Fixed Code (with same context):**
        # ```lang
        # [code]
        # ```
        
        import re
        
        # Extract code blocks
        code_blocks = re.findall(r'```(?:\w+)?\n(.*?)\n```', proposed_fix, re.DOTALL)
        
        if len(code_blocks) >= 2:
            # First block is original (with context), second is fixed (with context)
            original_block = code_blocks[0].strip()
            fixed_block = code_blocks[1].strip()
            
            logger.info(f"[Patch Generation] Extracted code blocks - original: {len(original_block)} chars, fixed: {len(fixed_block)} chars")
            
            # Find the original block in the full original code
            # Try exact match first
            if original_block in original_code:
                logger.info(f"[Patch Generation] Found exact match for original block")
                # Replace the first occurrence
                fixed_code = original_code.replace(original_block, fixed_block, 1)
                logger.info(f"[Patch Generation] ✓ Successfully applied targeted fix")
                return fixed_code
            else:
                # Try fuzzy matching - the block might have slight whitespace differences
                logger.info(f"[Patch Generation] Exact match failed, trying normalized matching")
                
                # Normalize whitespace for comparison
                def normalize(text):
                    # Normalize line endings and collapse multiple spaces
                    return '\n'.join(line.rstrip() for line in text.split('\n'))
                
                normalized_original_code = normalize(original_code)
                normalized_original_block = normalize(original_block)
                normalized_fixed_block = normalize(fixed_block)
                
                if normalized_original_block in normalized_original_code:
                    logger.info(f"[Patch Generation] Found normalized match for original block")
                    # Apply the fix in the normalized version
                    fixed_normalized = normalized_original_code.replace(normalized_original_block, normalized_fixed_block, 1)
                    logger.info(f"[Patch Generation] ✓ Successfully applied targeted fix (normalized)")
                    return fixed_normalized
                else:
                    logger.warning(f"[Patch Generation] Could not find original block in source code")
                    logger.warning(f"[Patch Generation] Original block preview: {original_block[:200]}...")
                    # Return None to trigger fallback
                    return None
        else:
            logger.error(f"[Patch Generation] ❌ CRITICAL: Could not extract 2 code blocks (found {len(code_blocks)})")
            logger.error(f"[Patch Generation] Expected format: **Original Code** and **Fixed Code** blocks")
            logger.error(f"[Patch Generation] This means the LLM did not follow the prompt format!")
            logger.error(f"[Patch Generation] Cannot create valid patch without proper code blocks")
            # DO NOT use proposed_fix as complete file - this causes bad patches
            return None
            
    except Exception as e:
        logger.error(f"[Patch Generation] Error applying targeted fix: {str(e)}")
        return None


def _create_conceptual_fix_document(
    state: AgentState,
    incident_id: str,
    proposed_fix: str,
    fix_explanation: str,
    error_file_path: str
) -> AgentState:
    """
    Create a conceptual fix document when GitHub code is not available.
    
    This creates a comprehensive guide for manual fix implementation.
    
    Args:
        state: Current agent state
        incident_id: Incident identifier
        proposed_fix: Conceptual fix template
        fix_explanation: Explanation of the fix
        error_file_path: Path to the file with error
        
    Returns:
        Updated state with document path
    """
    try:
        # Create patches directory if it doesn't exist
        patch_dir = Path(settings.patch_output_dir)
        patch_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate filename
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        doc_filename = f"incident_{incident_id}_{timestamp}_CONCEPTUAL_FIX.md"
        doc_path = patch_dir / doc_filename
        
        # Create comprehensive guidance document
        doc_content = f"""# Conceptual Fix Guide: {incident_id}

## ⚠️ Important Notice
This is a **conceptual fix** generated without direct access to the repository code.
**Manual review and adaptation required** before implementation.

---

## Incident Information

- **Incident ID:** {incident_id}
- **Application:** {state.get('app_name', 'Unknown')}
- **Environment:** {state.get('environment', 'Unknown')}
- **Severity:** {state.get('severity', 'Unknown')}
- **Repository:** {state.get('repo_full_name', 'Unknown')}
- **File Path:** {error_file_path or 'Unknown'}
- **Error Line:** {state.get('error_line_number', 'Unknown')}
- **Generated:** {datetime.utcnow().isoformat()}

---

## Error Details

### Error Title
{state.get('error_title', 'Unknown')}

### Error Description
{state.get('error_description', 'N/A')}

### Stack Trace
```
{state.get('stack_trace', 'N/A')[:1000]}
```

---

## Root Cause Analysis
{state.get('rca_text', 'N/A')[:1000]}...

---

## Proposed Fix

{fix_explanation}

---

## Code Template

```{state.get('error_file_type', 'code')}
{proposed_fix}
```

---

## Implementation Steps

1. **Review the error** - Understand the root cause from the RCA above
2. **Locate the file** - Navigate to `{error_file_path or 'the error location'}`
3. **Identify the issue** - Find the code causing the error around line {state.get('error_line_number', 'N/A')}
4. **Apply the template** - Use the code template above as guidance
5. **Adapt to context** - Modify the fix to match your actual code structure
6. **Test thoroughly** - Verify the fix resolves the issue
7. **Review changes** - Have another developer review before deploying

---

## Safety Considerations

- ⚠️ **Manual Review Required**: This fix was generated without seeing the actual code
- 🔍 **Context Awareness**: Ensure the fix fits your specific code context
- ✅ **Testing**: Thoroughly test in a non-production environment first
- 👥 **Peer Review**: Have another developer review the changes
- 📝 **Documentation**: Update comments and documentation as needed

---

## Next Steps

1. Apply the conceptual fix to your codebase following the guidance above
2. Test the fix thoroughly in a development environment
3. Create a pull request with proper review process
4. Monitor the application after deployment

---

## Need Help?

If you need assistance implementing this fix:
- Review the full error logs and stack trace
- Consult with your team's subject matter experts
- Consider pair programming for complex changes
- Reference the RCA for understanding the root cause

---

Generated by Log Monitor POC - Conceptual Fix Generator
"""
        
        # Write document
        with open(doc_path, 'w', encoding='utf-8') as f:
            f.write(doc_content)
        
        # Update state
        state['patch_path'] = str(doc_path)
        state['current_node'] = 'generate_patch'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        # Update workflow tracking
        if 'workflow_completed_steps' not in state:
            state['workflow_completed_steps'] = []
        
        if 'generate_patch' not in state['workflow_completed_steps']:
            state['workflow_completed_steps'].append('generate_patch')
        
        state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
        
        state['messages'] = state.get('messages', []) + [
            f"📋 Conceptual fix document created: {doc_filename}",
            "⚠️ Manual implementation required"
        ]
        
        # Update database
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=incident_id,
                    current_workflow_node='generate_patch',
                    workflow_completed_steps=state['workflow_completed_steps'],
                    workflow_progress_pct=state['workflow_progress_pct'],
                    patch_path=str(doc_path)
                )
        except Exception as db_error:
            logger.warning(f"Failed to update patch info in DB: {db_error}")
        
        logger.info(f"[Patch Generation] Conceptual fix document created: {doc_filename}")
        
    except Exception as e:
        logger.error(f"[Patch Generation] Failed to create conceptual fix document: {str(e)}")
        state['error_message'] = f"Conceptual fix document creation failed: {str(e)}"
        state['messages'] = state.get('messages', []) + [
            f"❌ Document creation failed: {str(e)}"
        ]
    
    return state
