"""Patch file generation node for agent workflow."""
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
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
            logger.warning(f"[Patch Generation] No file path available for incident {incident_id}, creating conceptual fix document")
            # Still create a conceptual fix guide even without a file path
            if proposed_fix:
                return _create_conceptual_fix_document(
                    state=state,
                    incident_id=incident_id,
                    proposed_fix=proposed_fix,
                    fix_explanation=fix_explanation or 'Auto-generated fix',
                    error_file_path='unknown',
                )
            state['messages'] = state.get('messages', []) + ["⚠️ No file path and no proposed fix available for patch"]
            return state
        
        # NEW: Apply targeted fix to original code to create complete fixed version
        # The LLM now returns only the changed lines, not the entire file
        # We need to intelligently merge the fix into the original code
        fixed_code = _apply_targeted_fix(original_code, proposed_fix, state)
        
        # Track whether we used a targeted block diff (fallback path)
        _targeted_block_diff_content = None

        if not fixed_code:
            # Could not match the targeted fix to the original code via _apply_targeted_fix.
            # Extract the two code blocks from the LLM response and diff them directly.
            code_blocks = re.findall(r'```(?:\w+)?\n(.*?)\n```', proposed_fix, re.DOTALL)
            if len(code_blocks) >= 2:
                original_block = code_blocks[0].strip()
                fixed_block = code_blocks[1].strip()
                logger.warning(
                    f"[Patch Generation] _apply_targeted_fix failed; generating targeted block diff "
                    f"for incident {incident_id}"
                )

                # 1. Try harder to apply the fix to the full original file (for PR creation)
                fixed_code_for_pr = _apply_block_to_file_fuzzy(
                    original_code, original_block, fixed_block
                )
                if fixed_code_for_pr:
                    fixed_code = fixed_code_for_pr
                    logger.info(
                        f"[Patch Generation] Fuzzy block application succeeded for incident {incident_id}"
                    )
                else:
                    # 2. Cannot apply to full file — diff only the two blocks.
                    #    The patch will show ONLY the changed lines (correct behaviour).
                    #    PR creation will be skipped because fixed_file_content is not set.
                    orig_blk_lines = original_block.splitlines(keepends=True)
                    fix_blk_lines  = fixed_block.splitlines(keepends=True)
                    if orig_blk_lines and not orig_blk_lines[-1].endswith('\n'):
                        orig_blk_lines[-1] += '\n'
                    if fix_blk_lines and not fix_blk_lines[-1].endswith('\n'):
                        fix_blk_lines[-1] += '\n'
                    _targeted_block_diff_content = ''.join(difflib.unified_diff(
                        orig_blk_lines, fix_blk_lines,
                        fromfile=error_file_path, tofile=error_file_path
                    ))
                    # Use fixed_block as a placeholder so later code doesn't crash,
                    # but we will override diff_content below.
                    fixed_code = fixed_block
                    logger.warning(
                        f"[Patch Generation] Using targeted block diff (no full-file apply) "
                        f"for incident {incident_id}"
                    )

            elif len(code_blocks) == 1:
                # Only one block — assume it is the complete fixed file
                fixed_code = code_blocks[0].strip()
                logger.warning(
                    f"[Patch Generation] Only one code block found; using it as fixed code for incident {incident_id}"
                )
            else:
                logger.error(f"[Patch Generation] ❌ CRITICAL: Cannot parse targeted fix format for incident {incident_id}")
                logger.error(f"[Patch Generation] LLM did not return proper 'Original Code' + 'Fixed Code' blocks")
                logger.error(f"[Patch Generation] Creating conceptual fix document instead of patch file")
                return _create_conceptual_fix_document(
                    state=state,
                    incident_id=incident_id,
                    proposed_fix=proposed_fix,
                    fix_explanation=fix_explanation or 'Auto-generated fix',
                    error_file_path=error_file_path,
                )

        # Create patches directory if it doesn't exist
        patch_dir = Path(settings.patch_output_dir)
        patch_dir.mkdir(parents=True, exist_ok=True)

        # Generate patch filename
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        patch_filename = f"incident_{incident_id}_{timestamp}.patch"
        patch_path = patch_dir / patch_filename

        if _targeted_block_diff_content is not None:
            # Use the pre-computed targeted diff (only the changed lines)
            diff_content = _targeted_block_diff_content
        else:
            # Generate unified diff from original vs complete fixed code
            original_lines = original_code.splitlines(keepends=True)
            fixed_lines = fixed_code.splitlines(keepends=True)

            # Ensure lines end with newlines for proper diff
            if original_lines and not original_lines[-1].endswith('\n'):
                original_lines[-1] += '\n'
            if fixed_lines and not fixed_lines[-1].endswith('\n'):
                fixed_lines[-1] += '\n'

            # Generate unified diff
            diff = difflib.unified_diff(
                original_lines,
                fixed_lines,
                fromfile=error_file_path,
                tofile=error_file_path
            )

            # Join diff lines
            diff_lines = list(diff)
            diff_content = ''.join(diff_lines)

        # Clean up the diff - remove multi-line comment blocks that confuse Studio
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
        # Only store fixed_file_content when it is genuinely the full file
        # (not just a partial code block).  If _targeted_block_diff_content was
        # used, fixed_file_content was never set here, so PR creation will be
        # skipped rather than committing a truncated file.
        if _targeted_block_diff_content is None and fixed_code:
            state['fixed_file_content'] = fixed_code  # full fixed file for PR creation
        state['fix_branch'] = state.get('fix_branch') or state.get('github_branch')
        state['current_node'] = 'generate_patch'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        # Update workflow tracking
        completed_steps = list(state.get('workflow_completed_steps') or [])
        if 'generate_patch' not in completed_steps:
            completed_steps.append('generate_patch')
        state['workflow_completed_steps'] = completed_steps
        state['workflow_progress_pct'] = len(completed_steps) / 11.0
        
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


def _apply_block_to_file_fuzzy(
    original_code: str,
    original_block: str,
    fixed_block: str
) -> Optional[str]:
    """
    Try to apply a code block fix to the original file using fuzzy/sequence matching.

    This is called when exact and normalised matching in _apply_targeted_fix both fail.
    It uses difflib.SequenceMatcher to locate the region of the original file that most
    closely corresponds to the LLM's "Original Code" block, then replaces that region
    with the "Fixed Code" block.

    Args:
        original_code:  Complete original file content.
        original_block: The "Original Code" block returned by the LLM (with context lines).
        fixed_block:    The "Fixed Code" block returned by the LLM (with the fix applied).

    Returns:
        Complete fixed file content, or None if a reliable match could not be found.
    """
    try:
        orig_lines  = original_code.splitlines()
        block_lines = [line.rstrip() for line in original_block.splitlines()]

        if not block_lines or not orig_lines:
            return None

        # Strip each line for comparison (ignore leading/trailing whitespace)
        orig_stripped = [line.strip() for line in orig_lines]
        block_stripped = [line.strip() for line in block_lines]

        # Remove fully-blank lines from block for matching purposes
        block_nonblank = [l for l in block_stripped if l]
        if not block_nonblank:
            return None

        # Slide a window over orig_stripped to find the best match
        win_size  = len(block_stripped)
        best_ratio = 0.0
        best_start = -1

        for start in range(max(1, len(orig_lines) - win_size + 1)):
            end    = start + win_size
            window = orig_stripped[start:end]
            ratio  = difflib.SequenceMatcher(
                None, block_stripped, window, autojunk=False
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = start

        # Require at least 70 % similarity to consider the match reliable
        if best_ratio < 0.70 or best_start < 0:
            logger.warning(
                f"[Patch Generation] Fuzzy match best ratio {best_ratio:.2f} below threshold; "
                "skipping full-file apply"
            )
            return None

        best_end = best_start + win_size
        fix_lines = fixed_block.splitlines()

        # Preserve the original indentation of the matched region's first line
        result_lines = orig_lines[:best_start] + fix_lines + orig_lines[best_end:]
        return '\n'.join(result_lines)

    except Exception as exc:
        logger.warning(f"[Patch Generation] _apply_block_to_file_fuzzy error: {exc}")
        return None


def _apply_targeted_fix(original_code: str, proposed_fix: str, state: AgentState) -> Optional[str]:
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
        code_blocks = re.findall(r'```(?:\w+)?\n(.*?)\n```', proposed_fix, re.DOTALL)
        
        if len(code_blocks) >= 2:
            original_block = code_blocks[0].strip()
            fixed_block = code_blocks[1].strip()
            
            logger.info(f"[Patch Generation] Extracted code blocks - original: {len(original_block)} chars, fixed: {len(fixed_block)} chars")
            
            if original_block in original_code:
                logger.info(f"[Patch Generation] Found exact match for original block")
                fixed_code = original_code.replace(original_block, fixed_block, 1)
                logger.info(f"[Patch Generation] ✓ Successfully applied targeted fix")
                return fixed_code

            logger.info(f"[Patch Generation] Exact match failed, trying line-normalized matching")

            original_file_lines = original_code.splitlines()
            original_block_lines = original_block.splitlines()
            fixed_block_lines = fixed_block.splitlines()

            if len(original_block_lines) != len(fixed_block_lines):
                logger.warning(
                    "[Patch Generation] Original and fixed blocks have different line counts; "
                    "skipping line-normalized replacement to avoid broad rewrites"
                )
                return None

            def normalize_line(line: str) -> str:
                return line.strip()

            normalized_file_lines = [normalize_line(line) for line in original_file_lines]
            normalized_original_block_lines = [normalize_line(line) for line in original_block_lines]

            match_start = -1
            block_len = len(normalized_original_block_lines)
            for idx in range(len(normalized_file_lines) - block_len + 1):
                if normalized_file_lines[idx:idx + block_len] == normalized_original_block_lines:
                    match_start = idx
                    break

            if match_start < 0:
                logger.warning(f"[Patch Generation] Could not find original block in source code")
                logger.warning(f"[Patch Generation] Original block preview: {original_block[:200]}...")
                return None

            logger.info(f"[Patch Generation] Found line-normalized match for original block at line %d", match_start + 1)

            replacement_lines = original_file_lines[match_start:match_start + block_len]
            for i, (orig_line, fixed_line) in enumerate(zip(original_block_lines, fixed_block_lines)):
                # Compare stripped versions: if the only difference is surrounding
                # whitespace (trailing spaces, etc.) treat it as a context line and
                # leave the original file's line completely untouched.  This prevents
                # spurious delete+add hunks in the PR for lines like </mule> that the
                # LLM included as context but slightly mangled in whitespace.
                if orig_line.strip() != fixed_line.strip():
                    actual_line = replacement_lines[i]
                    actual_indent = actual_line[:len(actual_line) - len(actual_line.lstrip())]
                    fixed_content = fixed_line.strip()
                    replacement_lines[i] = f"{actual_indent}{fixed_content}" if fixed_content else actual_line

            fixed_file_lines = (
                original_file_lines[:match_start]
                + replacement_lines
                + original_file_lines[match_start + block_len:]
            )
            logger.info(f"[Patch Generation] ✓ Successfully applied targeted fix (line-normalized)")
            return '\n'.join(fixed_file_lines)
        else:
            logger.error(f"[Patch Generation] ❌ CRITICAL: Could not extract 2 code blocks (found {len(code_blocks)})")
            logger.error(f"[Patch Generation] Expected format: **Original Code** and **Fixed Code** blocks")
            logger.error(f"[Patch Generation] This means the LLM did not follow the prompt format!")
            logger.error(f"[Patch Generation] Cannot create valid patch without proper code blocks")
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
    Create a minimal .patch file when GitHub original code is not available.

    Uses whatever code blocks the LLM returned to produce a best-effort unified diff.
    The file extension is always .patch (never .md) so downstream tooling can apply it.
    """
    try:
        patch_dir = Path(settings.patch_output_dir)
        patch_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        patch_filename = f"incident_{incident_id}_{timestamp}.patch"
        patch_path = patch_dir / patch_filename

        # Extract code blocks from the LLM response
        code_blocks = re.findall(r'```(?:\w+)?\n(.*?)\n```', proposed_fix, re.DOTALL)
        original_block = (code_blocks[0].strip() if len(code_blocks) >= 1 else "").splitlines(keepends=True)
        fixed_block    = (code_blocks[1].strip() if len(code_blocks) >= 2 else
                          code_blocks[0].strip() if len(code_blocks) == 1 else "").splitlines(keepends=True)

        # Ensure trailing newlines for correct diff output
        if original_block and not original_block[-1].endswith('\n'):
            original_block[-1] += '\n'
        if fixed_block and not fixed_block[-1].endswith('\n'):
            fixed_block[-1] += '\n'

        diff_lines = list(difflib.unified_diff(
            original_block, fixed_block,
            fromfile=error_file_path or 'original',
            tofile=error_file_path or 'fixed',
        ))
        diff_content = ''.join(diff_lines) if diff_lines else (
            # No diff produced (identical or empty) - emit a comment-only patch
            f"# No diff available\n# File: {error_file_path}\n"
        )

        patch_content = f"""{diff_content}
# ============================================================================
# Patch Information (Conceptual Fix - original code was not fetched)
# ============================================================================
# Incident ID: {incident_id}
# Application: {state.get('app_name', 'Unknown')}
# Environment: {state.get('environment', 'Unknown')}
# Severity:    {state.get('severity', 'Unknown')}
# Repository:  {state.get('repo_full_name', 'Unknown')}
# File:        {error_file_path or 'Unknown'}
# Generated:   {datetime.utcnow().isoformat()}
#
# NOTE: This patch was generated without direct access to the repository.
#       Review and adapt before applying.
#
# Fix Explanation:
# {fix_explanation[:500]}
#
# How to apply:
#   git apply {patch_filename}
#   patch -p0 < {patch_filename}
# ============================================================================
"""

        with open(patch_path, 'w', encoding='utf-8') as f:
            f.write(patch_content)

        # Store fixed block for potential PR creation
        if fixed_block:
            state['fixed_file_content'] = ''.join(fixed_block).rstrip('\n')  # type: ignore[typeddict-unknown-key]

        state['patch_path'] = str(patch_path)
        state['current_node'] = 'generate_patch'
        state['updated_at'] = datetime.utcnow().isoformat()

        _csteps = list(state.get('workflow_completed_steps') or [])
        if 'generate_patch' not in _csteps:
            _csteps.append('generate_patch')
        state['workflow_completed_steps'] = _csteps
        state['workflow_progress_pct'] = len(_csteps) / 11.0

        state['messages'] = state.get('messages', []) + [
            f"⚠️ Patch created from LLM code blocks (original code not available): {patch_filename}"
        ]

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
                    patch_path=str(patch_path),
                )
        except Exception as db_error:
            logger.warning(f"Failed to update patch info in DB: {db_error}")

        logger.info(f"[Patch Generation] Conceptual patch file created: {patch_filename}")

    except Exception as e:
        logger.error(f"[Patch Generation] Failed to create conceptual patch: {str(e)}")
        state['error_message'] = f"Patch creation failed: {str(e)}"
        state['messages'] = state.get('messages', []) + [f"❌ Patch creation failed: {str(e)}"]

    return state
