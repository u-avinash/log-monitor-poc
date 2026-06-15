"""Code fix generation node using LLM."""
import logging
from datetime import datetime
import yaml
import re
from agents.state import AgentState, WORKFLOW_TOTAL_STEPS
from integrations.llm_provider import LLMProvider
from utils.code_fetcher import CodeFetcher

logger = logging.getLogger(__name__)


def _trim_text(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    half = max(1, limit // 2)
    return f"{text[:half]}\n...\n{text[-half:]}"


def generate_fix_node(state: AgentState) -> AgentState:
    """
    Generate code fix using LLM.
    
    This node:
    1. Uses RCA summary to understand the issue
    2. Calls LLM to generate a code fix
    3. Parses the response for code and explanation
    4. Validates the fix for dangerous patterns
    
    Args:
        state: Current agent state with RCA
        
    Returns:
        Updated state with proposed fix and explanation
    """
    incident_id = state['incident_id']
    logger.info(f"[Fix Generation] Generating fix for incident {incident_id}")
    
    try:
        # Get file info from state (set by RCA generator)
        error_file_path = state.get('error_file_path')
        error_line_number = state.get('error_line_number')
        error_file_type = state.get('error_file_type')
        repo_full_name = state.get('repo_full_name')
        file_type_key = error_file_type or 'code'
        
        # Log metadata status
        logger.info(f"[Fix Generation] GitHub metadata from state:")
        logger.info(f"  - repo_full_name: {repo_full_name}")
        logger.info(f"  - error_file_path: {error_file_path}")
        logger.info(f"  - error_line_number: {error_line_number}")
        logger.info(f"  - error_file_type: {error_file_type}")
        
        # Persist GitHub metadata to database early for reliability
        if repo_full_name and error_file_path:
            try:
                from storage.database import get_session
                from storage.incident_repository import IncidentRepository

                with get_session() as session:
                    repo_db = IncidentRepository(session)
                    repo_db.update(
                        incident_id=incident_id,
                        repo_full_name=repo_full_name,
                        error_file_path=error_file_path,
                    )
                    logger.debug(f"[Fix Generation] ✓ Persisted GitHub metadata to database")
            except Exception as db_error:
                logger.warning(f"[Fix Generation] Failed to persist metadata (non-critical): {db_error}")
        else:
            logger.warning(f"[Fix Generation] ⚠️  GitHub metadata incomplete - some features may be limited")
        
        # Fetch actual code from GitHub using project context
        code_fetcher = CodeFetcher(project_id=state.get('project_id'))
        code_context = None
        
        # Load prompt template early (needed for both code fetch and fallback)
        with open('config/prompts.yaml', 'r', encoding='utf-8') as f:
            prompts = yaml.safe_load(f)
        
        # Initialize LLM provider early (needed for both approaches)
        llm_provider = LLMProvider(project_id=state.get('project_id'))
        logger.info(
            "[Fix Generation] Using LLM provider=%s model=%s project_id=%s",
            llm_provider.provider,
            llm_provider.model,
            llm_provider.project_id,
        )
        
        # Extract RCA summary early (needed for both approaches)
        rca_summary = state.get('rca_text', '')[:800] if state.get('rca_text') else 'No RCA available'
        
        if error_file_path and repo_full_name:
            logger.info(f"[Fix Generation] Fetching code: {repo_full_name}/{error_file_path}")
            try:
                code_context = code_fetcher.fetch_code_for_analysis(
                    repo_full_name=repo_full_name,
                    file_path=error_file_path,
                    line_number=error_line_number,
                    context_lines=30  # More context for fix generation
                )
                if code_context:
                    logger.info(f"[Fix Generation] ✓ Code fetched successfully ({code_context['line_count']} lines)")
                else:
                    logger.warning(f"[Fix Generation] ✗ Code fetch returned None")
            except Exception as fetch_error:
                logger.error(f"[Fix Generation] ✗ Code fetch failed: {fetch_error}")

        # Fetch full MuleSoft project context (all flow XML + DWL scripts) for richer fix
        project_files: dict = {}
        if repo_full_name:
            try:
                project_files = code_fetcher.fetch_mulesoft_project_context(repo_full_name)
                logger.info(f"[Fix Generation] ✓ Fetched {len(project_files)} MuleSoft project files")
            except Exception as ctx_err:
                logger.warning(f"[Fix Generation] Could not fetch project context: {ctx_err}")
        else:
            logger.warning(f"[Fix Generation] ✗ Missing GitHub metadata (repo={repo_full_name}, file={error_file_path})")
        
        if not code_context:
            logger.warning("[Fix Generation] Could not fetch code from GitHub, using fallback approach")
            # Generate fix based on error information alone
            return _generate_fallback_fix(
                state,
                prompts,
                llm_provider,
                error_file_path or '',
                error_line_number or 0,
                error_file_type or '',
                rca_summary,
            )
        
        assert code_context is not None
        code_context_data = code_context
        fix_prompt = prompts['code_fix_generation']
        
        # Determine file type specific instructions
        file_type_instructions = {
            'dataweave': "Fix the DataWeave transformation. Ensure proper null handling and type conversions.",
            'mule_xml': "Fix the Mule flow XML. Ensure proper error handling and flow logic.",
            'yaml': "Fix the YAML configuration. Ensure proper indentation and syntax.",
            'java': "Fix the Java code. Ensure proper null checks and exception handling."
        }
        
        specific_instruction = file_type_instructions.get(file_type_key, "Fix the code issue.")
        
        full_code_for_storage = code_context_data["full_content"]
        prompt_code_context = _trim_text(code_context_data["full_content"], 12000)
        prompt_error_snippet = _trim_text(code_context_data.get("context_snippet", ""), 4000)

        # Build project-wide context section for the prompt (other XML/DWL files)
        project_context_section = ""
        if project_files:
            other_files = {
                p: c for p, c in project_files.items()
                if p != code_context_data.get("file_path")
            }
            if other_files:
                parts = []
                char_budget = 8000
                for path, content in other_files.items():
                    snippet = _trim_text(content, 1500)
                    entry = f"### {path}\n```xml\n{snippet}\n```\n"
                    if len('\n'.join(parts)) + len(entry) > char_budget:
                        break
                    parts.append(entry)
                if parts:
                    project_context_section = (
                        "\n## Full MuleSoft Project Context\n"
                        "The following files are part of the same MuleSoft project. "
                        "Use them to understand the full flow before proposing the fix.\n\n"
                        + "\n".join(parts)
                    )

        # Format comprehensive prompt with actual code - TARGETED FIX APPROACH
        formatted_prompt = f"""{fix_prompt}

## Error Context
**Error Title:** {state['error_title']}
**File:** {error_file_path}
**Line:** {error_line_number}
**File Type:** {error_file_type}

## Root Cause Analysis Summary
{rca_summary}

## Actual Code from Repository
**Repository:** {repo_full_name}
**Full File Path:** {error_file_path}

```{file_type_key}
{prompt_code_context}
```

## Error Location (highlighted)
```
{prompt_error_snippet}
```
{project_context_section}

## Your Task
{specific_instruction}

**CRITICAL INSTRUCTIONS - TARGETED FIX APPROACH:**
1. **Identify the EXACT lines that need to be changed** (usually just 1-10 lines around the error)
2. **Provide ONLY those specific lines with the fix applied** (not the entire file)
3. **Include enough context** (2-3 lines before and after) to locate the exact position
4. **Add clear comments** explaining what was changed
5. **Be minimal** - only change what's absolutely necessary to fix the error
6. **The fix must be production-ready and safe**

**WHY THIS MATTERS:** Large files can be truncated. We need only the changed lines to generate accurate patches.

**REQUIRED Output Format (MUST MATCH THIS EXACTLY):**

**Original Code (with context):**
```{error_file_type}
[Include 3-5 lines BEFORE the problematic code for context]
[The EXACT lines from the original file that contain the bug]
[Include 3-5 lines AFTER the problematic code for context]
```

**Fixed Code (with same context):**
```{error_file_type}
[Same 3-5 lines BEFORE]
[The FIXED version of the problematic lines]
[Same 3-5 lines AFTER]
```

**CRITICAL:**
- The "Original Code" block MUST contain lines that appear EXACTLY in the original file above
- Both blocks MUST have the SAME context lines before and after
- Only the problematic lines in the middle should differ between original and fixed
- Include enough context (3-5 lines) to uniquely identify the location

**Example:**
**Original Code (with context):**
```xml
    </flow>

    <sub-flow name="validate-order-payload" doc:name="Validate Order Payload">
        <logger level="INFO" message="Validating incoming order payload" category="com.enterprise.retail.orders"/>

        <!-- Keep a consistent structure for downstream processing -->
        <ee:transform doc:name="Normalize Order Payload">
            <ee:message>
                <ee:set-payload><![CDATA[%dw 2.0
output application/json
---
{{
  orderId: payload.orderId default "N/A",
  orderDate: payload.orderDate,
  items: payload.items default []
}}]]></ee:set-payload>
            </ee:message>
        </ee:transform>
```

**Fixed Code (with same context):**
```xml
    </flow>

    <sub-flow name="validate-order-payload" doc:name="Validate Order Payload">
        <logger level="INFO" message="Validating incoming order payload" category="com.enterprise.retail.orders"/>

        <!-- Keep a consistent structure for downstream processing -->
        <ee:transform doc:name="Normalize Order Payload">
            <ee:message>
                <ee:set-payload><![CDATA[%dw 2.0
output application/json
---
{{
  orderId: payload.orderId default "N/A",
  orderDate: payload.orderDate default now(),
  items: payload.items default []
}}]]></ee:set-payload>
            </ee:message>
        </ee:transform>
```

## Explanation
[Explain what was changed and why it fixes the issue]
"""
        
        # Generate fix
        logger.info(f"[Fix Generation] Calling LLM ({llm_provider.provider})")
        fix_response = llm_provider.invoke(
            prompt=formatted_prompt,
            system_message="You are an expert software engineer specializing in Java, MuleSoft, and DataWeave. Generate safe, production-ready code fixes."
        )
        
        # Parse response to extract explanation but KEEP FULL RESPONSE for patch generation
        # The patch generator needs both Original and Fixed code blocks
        proposed_fix = fix_response  # Keep full response with both code blocks
        fix_explanation = _extract_explanation(fix_response)
        
        # Validate for dangerous patterns
        safety_check, concerns = _validate_fix_safety(proposed_fix)
        
        if not safety_check:
            logger.warning(f"[Fix Generation] Safety concerns detected: {concerns}")
        
        # Store the original code in STATE for patch generation (critical!)
        state['original_code'] = full_code_for_storage
        logger.info(f"[Fix Generation] ✓ Stored original_code in STATE ({len(full_code_for_storage)} chars)")
        
        # Also store in database for persistence
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo_db = IncidentRepository(session)
                repo_db.update(
                    incident_id=state['incident_id'],
                    fetched_code=full_code_for_storage
                )
            logger.info(f"[Fix Generation] ✓ Stored fetched_code in DATABASE")
        except Exception as db_error:
            logger.error(f"[Fix Generation] Failed to store fetched code in DB: {db_error}")
        
        # Update state
        state['proposed_fix'] = proposed_fix
        state['fix_explanation'] = fix_explanation
        state['affected_files'] = [error_file_path] if error_file_path else []
        state['current_node'] = 'generate_fix'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        # Update workflow tracking
        completed_steps = list(state.get('workflow_completed_steps') or [])
        if 'generate_fix' not in completed_steps:
            completed_steps.append('generate_fix')
        state['workflow_completed_steps'] = completed_steps
        
        state['workflow_progress_pct'] = len(completed_steps) / WORKFLOW_TOTAL_STEPS

        messages = [f"✓ Code fix generated successfully"]
        if not safety_check:
            messages.append(f"⚠️ Safety concerns: {', '.join(concerns)}")

        state['messages'] = state.get('messages', []) + messages

        # Send "Fix Generated" notification
        try:
            from agents.workflow import _send_event_notification
            _send_event_notification(
                event="Fix Generated",
                incident_id=state['incident_id'],
                severity=state.get('severity', 'HIGH'),
                app_name=state.get('app_name', ''),
                environment=state.get('environment', ''),
                details=(
                    f"Code fix generated for: {state.get('error_title', 'Unknown')}\n"
                    f"File: {error_file_path or 'N/A'}\n"
                    f"Explanation: {fix_explanation[:300]}{'...' if len(fix_explanation) > 300 else ''}"
                ),
                project_id=state.get('project_id'),
            )
        except Exception as _notify_err:
            logger.warning("[Fix Generation] Could not send fix-generated notification: %s", _notify_err)

        # Update database with workflow progress
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state['incident_id'],
                    current_workflow_node='generate_fix',
                    workflow_completed_steps=state['workflow_completed_steps'],
                    workflow_progress_pct=state['workflow_progress_pct'],
                    proposed_fix=proposed_fix,
                    fix_explanation=fix_explanation
                )
        except Exception as db_error:
            logger.warning(f"Failed to update workflow progress in DB: {db_error}")
        
        logger.info(f"[Fix Generation] Success for {state['incident_id']}")
        
    except Exception as e:
        logger.error(f"[Fix Generation] Failed for {state['incident_id']}: {str(e)}")
        error_text = str(e)
        if "Missing API key for provider" in error_text:
            error_text = (
                f"{error_text} "
                f"(incident={state['incident_id']}, app={state.get('app_name', 'unknown')}, project_id={state.get('project_id')})"
            )
        state['error_message'] = f"Fix generation failed: {error_text}"
        state['proposed_fix'] = None
        state['fix_explanation'] = f"Error generating fix: {error_text}"
        state['messages'] = state.get('messages', []) + [
            f"❌ Fix generation failed: {error_text}"
        ]
        
        # Update workflow tracking even on failure
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state['incident_id'],
                    current_workflow_node='generate_fix_failed',
                    workflow_progress_pct=state.get('workflow_progress_pct', 0.0)
                )
        except Exception as db_error:
            logger.warning(f"Failed to update failure status in DB: {db_error}")
    
    return state


def _extract_explanation(response: str) -> str:
    """
    Extract explanation section from LLM response.
    
    Args:
        response: Full LLM response
        
    Returns:
        Extracted explanation text
    """
    # Extract explanation section
    explanation_match = re.search(r'## Explanation\n(.*?)(?=##|$)', response, re.DOTALL)
    if explanation_match:
        return explanation_match.group(1).strip()
    
    # Try alternative heading
    explanation_match = re.search(r'\*\*Explanation\*\*:?\n(.*?)(?=##|\*\*|$)', response, re.DOTALL)
    if explanation_match:
        return explanation_match.group(1).strip()
    
    return "No explanation provided"


def _parse_fix_response(response: str, file_type: str) -> tuple[str, str]:
    """
    Parse LLM response to extract code and explanation.
    
    Expected format:
    ## Fixed Code
    ```filetype
    <code here>
    ```
    
    ## Explanation
    <explanation here>
    
    Returns:
        Tuple of (proposed_fix, explanation)
    """
    # Try to extract code block with file type
    patterns = [
        rf'```{file_type}\n(.*?)\n```',
        r'```(?:xml|java|python|yaml|dataweave)?\n(.*?)\n```',
        r'```\n(.*?)\n```'
    ]
    
    proposed_fix = None
    for pattern in patterns:
        code_match = re.search(pattern, response, re.DOTALL)
        if code_match:
            proposed_fix = code_match.group(1).strip()
            break
    
    if not proposed_fix:
        # Fallback: use entire response if no code block found
        proposed_fix = response
    
    # CRITICAL FIX: Remove markdown formatting that corrupts patch files
    # Remove markdown headers like "## Fixed Code", "## Code", etc.
    proposed_fix = re.sub(r'^##\s+.*$', '', proposed_fix, flags=re.MULTILINE)
    
    # Remove inline code block markers that might have been included
    proposed_fix = re.sub(r'^```\w*$', '', proposed_fix, flags=re.MULTILINE)
    
    # Remove leading/trailing whitespace from each line preservation
    lines = proposed_fix.split('\n')
    # Remove empty lines at start and end only
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    proposed_fix = '\n'.join(lines)
    
    # Extract explanation section
    explanation_match = re.search(r'## Explanation\n(.*?)(?=##|$)', response, re.DOTALL)
    explanation = explanation_match.group(1).strip() if explanation_match else "No explanation provided"
    
    return proposed_fix, explanation


def _validate_fix_safety(code: str) -> tuple[bool, list[str]]:
    """
    Validate generated code for dangerous patterns.
    
    Args:
        code: Generated code to validate
        
    Returns:
        Tuple of (is_safe, list_of_concerns)
    """
    concerns = []
    
    # Dangerous patterns to check
    dangerous_patterns = [
        (r'\beval\s*\(', 'Uses eval() function'),
        (r'\bexec\s*\(', 'Uses exec() function'),
        (r'os\.system\s*\(', 'Uses os.system()'),
        (r'subprocess\.call\s*\(', 'Uses subprocess without validation'),
        (r'rm\s+-rf\s+/', 'Contains dangerous shell command'),
        (r'DROP\s+TABLE', 'Contains SQL DROP statement'),
        (r'DELETE\s+FROM.*WHERE\s+1=1', 'Contains dangerous SQL DELETE'),
    ]
    
    for pattern, concern in dangerous_patterns:
        if re.search(pattern, code, re.IGNORECASE):
            concerns.append(concern)
    
    is_safe = len(concerns) == 0
    return is_safe, concerns


def _generate_fallback_fix(
    state: AgentState,
    prompts: dict,
    llm_provider: LLMProvider,
    error_file_path: str,
    error_line_number: int,
    error_file_type: str,
    rca_summary: str
) -> AgentState:
    """
    Generate fix without GitHub code access using error information only.
    
    This fallback approach:
    1. Uses error message, stack trace, and RCA
    2. Generates conceptual fix with clear instructions
    3. Provides guidance for manual implementation
    
    Args:
        state: Current agent state
        prompts: Loaded prompts from YAML
        llm_provider: LLM provider instance
        error_file_path: Path to file with error
        error_line_number: Line number of error
        error_file_type: Type of file (dataweave, mule_xml, etc.)
        rca_summary: Root cause analysis summary
        
    Returns:
        Updated state with conceptual fix
    """
    logger.info(f"[Fix Generation] Using fallback approach - no code access")
    
    try:
        fix_prompt = prompts.get('code_fix_generation', '')
        
        # Create fallback prompt that doesn't require code
        fallback_prompt = f"""You are an expert software engineer. Generate a fix for this error without access to the actual code.

## Error Details
**Application:** {state.get('app_name', 'Unknown')}
**Environment:** {state.get('environment', 'Unknown')}
**Error Title:** {state.get('error_title', 'Unknown')}
**File:** {error_file_path or 'Unknown'}
**Line:** {error_line_number or 'Unknown'}
**File Type:** {error_file_type or 'Unknown'}

## Error Message
{state.get('error_description', 'No description')}

## Stack Trace
```
{state.get('stack_trace', 'No stack trace')[:1500]}
```

## Root Cause Analysis
{rca_summary}

## Your Task
Since the actual code is not available, provide:

1. **Conceptual Fix**: Describe what needs to be changed and why
2. **Code Template**: Provide a code template/example showing the fix
3. **Step-by-Step Instructions**: Clear steps to implement the fix
4. **Common Patterns**: Show common fix patterns for this type of error

**Output Format:**
## Conceptual Fix
[Description of what needs to be fixed]

## Code Template
```{error_file_type or 'code'}
[Example code showing the fix pattern]
```

## Implementation Steps
1. [Step 1]
2. [Step 2]
...

## Explanation
[Why this fix resolves the issue]
"""
        
        # Generate fix
        logger.info(f"[Fix Generation] Calling LLM for fallback fix")
        fix_response = llm_provider.invoke(
            prompt=fallback_prompt,
            system_message=f"You are an expert in {error_file_type or 'software engineering'}. Provide clear, actionable fix guidance.",
            temperature=0.4
        )
        
        # Parse response
        proposed_fix, fix_explanation = _parse_fix_response(fix_response, error_file_type or 'code')
        
        # Validate safety
        safety_check, concerns = _validate_fix_safety(proposed_fix)
        
        # Update state
        state['proposed_fix'] = proposed_fix
        state['fix_explanation'] = fix_explanation
        state['affected_files'] = [error_file_path] if error_file_path else []
        state['fix_type'] = 'conceptual'  # Mark as conceptual fix
        
        # Set placeholder original_code for patch generation compatibility
        # This allows patch/PR generators to handle conceptual fixes appropriately
        state['original_code'] = f"""# Original code not available (GitHub access failed)
# This is a conceptual fix based on error analysis
# File: {error_file_path or 'Unknown'}
# Error: {state.get('error_title', 'Unknown')}

# NOTE: Manual review required - apply fix template below to actual code
"""
        
        state['current_node'] = 'generate_fix'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        # Update workflow tracking
        completed_steps = list(state.get('workflow_completed_steps') or [])
        if 'generate_fix' not in completed_steps:
            completed_steps.append('generate_fix')
        state['workflow_completed_steps'] = completed_steps
        
        state['workflow_progress_pct'] = len(completed_steps) / WORKFLOW_TOTAL_STEPS

        messages = [
            "⚠️ Conceptual fix generated (GitHub access not available)",
            "✓ Fix provides guidance and code templates"
        ]
        
        if not safety_check:
            messages.append(f"⚠️ Safety concerns: {', '.join(concerns)}")
        
        state['messages'] = state.get('messages', []) + messages

        # Send "Fix Generated" notification (fallback/conceptual fix)
        try:
            from agents.workflow import _send_event_notification
            _send_event_notification(
                event="Fix Generated (Conceptual)",
                incident_id=state['incident_id'],
                severity=state.get('severity', 'HIGH'),
                app_name=state.get('app_name', ''),
                environment=state.get('environment', ''),
                details=(
                    f"Conceptual fix generated for: {state.get('error_title', 'Unknown')}\n"
                    f"File: {error_file_path or 'N/A'} (GitHub access not available)\n"
                    f"Explanation: {fix_explanation[:300]}{'...' if len(fix_explanation) > 300 else ''}"
                ),
                project_id=state.get('project_id'),
            )
        except Exception as _notify_err:
            logger.warning("[Fix Generation] Could not send fallback fix notification: %s", _notify_err)

        # Update database
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state['incident_id'],
                    current_workflow_node='generate_fix',
                    workflow_completed_steps=state['workflow_completed_steps'],
                    workflow_progress_pct=state['workflow_progress_pct'],
                    proposed_fix=proposed_fix,
                    fix_explanation=fix_explanation
                )
        except Exception as db_error:
            logger.warning(f"Failed to update workflow progress in DB: {db_error}")
        
        logger.info(f"[Fix Generation] Fallback fix generated for {state['incident_id']}")
        
    except Exception as e:
        logger.error(f"[Fix Generation] Fallback approach failed: {str(e)}")
        state['proposed_fix'] = None
        state['fix_explanation'] = f"Fix generation failed: {str(e)}"
        state['messages'] = state.get('messages', []) + [
            f"❌ Fix generation failed: {str(e)}"
        ]
    
    return state
