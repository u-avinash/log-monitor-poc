"""Fix quality reflection node using LLM self-critique."""
import logging
from datetime import datetime
from typing import Optional
import yaml
import json
import re
from agents.state import AgentState, WORKFLOW_TOTAL_STEPS
from integrations.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


def _safe_prompt_interpolate(template: str, values: dict[str, str]) -> str:
    """Safely replace known placeholders without interpreting JSON braces."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def _persist_processing_error(incident_id: str, message: str) -> None:
    """Persist a processing error to DB (best-effort)."""
    try:
        from storage.database import get_session
        from storage.incident_repository import IncidentRepository

        with get_session() as session:
            repo = IncidentRepository(session)
            repo.add_processing_error(incident_id=incident_id, error_message=message)
    except Exception as db_error:
        logger.warning(f"Failed to persist processing error in DB: {db_error}")


def reflect_on_fix_node(state: AgentState) -> AgentState:
    """
    Self-reflect on the generated fix to assess quality.
    
    This node:
    1. Uses LLM to critique its own fix
    2. Scores correctness, safety, code quality, completeness
    3. Provides concerns and recommendation
    4. Determines if fix should proceed to approval
    
    Args:
        state: Current agent state with proposed fix
        
    Returns:
        Updated state with quality scores and recommendation
    """
    logger.info(f"[Quality Reflection] Evaluating fix for incident {state['incident_id']}")
    
    try:
        # Load prompt template
        with open('config/prompts.yaml', 'r', encoding='utf-8') as f:
            prompts = yaml.safe_load(f)
        
        reflection_prompt = prompts['fix_quality_reflection']
        
        # Format prompt with truncated code snippet to reduce prompt size
        # Extract only relevant context from original code (max 2000 chars)
        original_code = state.get('original_code', '')
        if len(original_code) > 2000:
            # Take first 1000 and last 1000 characters for context
            original_code_snippet = (
                original_code[:1000] + 
                '\n\n... [middle section omitted for brevity] ...\n\n' + 
                original_code[-1000:]
            )
            logger.debug(f"[Quality Reflection] Truncated original code from {len(original_code)} to ~2000 chars")
        else:
            original_code_snippet = original_code
        
        formatted_prompt = _safe_prompt_interpolate(
            reflection_prompt,
            {
                'error_description': state['error_description'],
                'proposed_fix': state.get('proposed_fix', 'No fix generated'),
                'original_code_snippet': original_code_snippet if original_code_snippet else '[No original code available]',
            },
        )
        
        # Initialize LLM provider using project-scoped configuration
        llm_provider = LLMProvider(project_id=state.get('project_id'))
        logger.info(
            "[Quality Reflection] Using LLM provider=%s model=%s project_id=%s",
            llm_provider.provider,
            llm_provider.model,
            llm_provider.project_id,
        )
        
        # Create a clean system message with strong JSON instruction
        system_msg = """You are a senior code reviewer. Critically assess the proposed fix.

CRITICAL: You MUST respond with ONLY valid JSON. No markdown, no code blocks, no text before or after.

Your response must be a valid JSON object with this exact structure:
{
  "correctness_score": <number 0-10>,
  "safety_score": <number 0-10>,
  "code_quality_score": <number 0-10>,
  "completeness_score": <number 0-10>,
  "overall_score": <decimal 0.0-1.0>,
  "concerns": ["concern1", "concern2"],
  "recommendation": "APPROVE or REJECT or MANUAL_REVIEW",
  "reasoning": "explanation text"
}

Example:
{"correctness_score": 8, "safety_score": 9, "code_quality_score": 7, "completeness_score": 8, "overall_score": 0.8, "concerns": ["Minor edge case not handled"], "recommendation": "APPROVE", "reasoning": "The fix properly handles the null pointer exception"}

Output ONLY the JSON object. Start with { and end with }."""
        
        # Generate reflection with retry logic for connection errors
        logger.info(f"[Quality Reflection] Calling LLM for self-critique")
        max_retries = 3
        reflection_response = None
        last_error = None
        
        for attempt in range(max_retries):
            try:
                logger.debug(f"[Quality Reflection] Attempt {attempt + 1}/{max_retries}")
                reflection_response = llm_provider.invoke(
                    prompt=formatted_prompt,
                    system_message=system_msg,
                    temperature=0.2,  # Lower temperature for more consistent JSON
                    json_mode=True  # Enable JSON mode for supported providers
                )
                
                # Validate response is not empty
                if not reflection_response or len(reflection_response.strip()) < 10:
                    raise ValueError(f"Empty or too short response (len={len(reflection_response or '')})")
                
                # Clean the response immediately
                reflection_response = reflection_response.strip()
                
                # Validate it looks like JSON before proceeding
                if not ('{' in reflection_response and '}' in reflection_response):
                    raise ValueError("Response does not contain JSON braces")
                
                # Remove any markdown code blocks that LLM might add
                reflection_response = re.sub(r'```json\s*', '', reflection_response)
                reflection_response = re.sub(r'```\s*$', '', reflection_response)
                reflection_response = re.sub(r'```', '', reflection_response)
                
                # Remove leading whitespace and newlines before opening brace
                reflection_response = re.sub(r'^[\s\n]*({)', r'\1', reflection_response)
                
                logger.info(f"[Quality Reflection] ✓ Received valid response (len={len(reflection_response)})")
                logger.debug(f"[Quality Reflection] Raw LLM response (first 200 chars): {reflection_response[:200]}")
                break  # Success, exit retry loop
                
            except ConnectionError as conn_err:
                last_error = conn_err
                logger.warning(f"[Quality Reflection] Connection error on attempt {attempt + 1}: {str(conn_err)}")
                if attempt < max_retries - 1:
                    import time
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    logger.info(f"[Quality Reflection] Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"[Quality Reflection] All retry attempts exhausted")
                    
            except Exception as llm_error:
                last_error = llm_error
                logger.error(f"[Quality Reflection] LLM invocation failed on attempt {attempt + 1}: {str(llm_error)}")
                if attempt < max_retries - 1:
                    import time
                    wait_time = 2 ** attempt
                    logger.info(f"[Quality Reflection] Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"[Quality Reflection] All retry attempts exhausted")
        
        # If all retries failed, return safe defaults but ALLOW WORKFLOW TO CONTINUE
        if reflection_response is None:
            logger.error(f"[Quality Reflection] Failed after {max_retries} attempts: {str(last_error)}")
            logger.warning(f"[Quality Reflection] Returning safe defaults to allow workflow to continue")

            _persist_processing_error(
                state["incident_id"],
                f"[reflect] Quality reflection failed after {max_retries} attempts: {str(last_error)}",
            )
            
            # Update state with safe defaults but mark for manual review
            state['correctness_score'] = 5.0
            state['safety_score'] = 5.0
            state['code_quality_score'] = 5.0
            state['completeness_score'] = 5.0
            state['overall_quality_score'] = 0.5
            state['quality_concerns'] = [f'Quality reflection failed after {max_retries} attempts: {str(last_error)}']
            state['quality_recommendation'] = 'MANUAL_REVIEW'
            state['reflection_failed'] = True  # Flag for tracking
            state['current_node'] = 'reflect'
            state['updated_at'] = datetime.utcnow().isoformat()
            
            state['messages'] = state.get('messages', []) + [
                f"⚠️ Quality reflection failed: {str(last_error)[:100]}",
                "Using safe default scores - MANUAL REVIEW REQUIRED"
            ]
            
            completed_steps = list(state.get('workflow_completed_steps') or [])
            if 'reflect' not in completed_steps:
                completed_steps.append('reflect')
            state['workflow_completed_steps'] = completed_steps
            state['workflow_progress_pct'] = len(completed_steps) / WORKFLOW_TOTAL_STEPS

            # Update database
            try:
                from storage.database import get_session
                from storage.incident_repository import IncidentRepository

                with get_session() as session:
                    repo = IncidentRepository(session)
                    repo.update(
                        incident_id=state['incident_id'],
                        current_workflow_node='reflect',
                        workflow_completed_steps=state['workflow_completed_steps'],
                        workflow_progress_pct=state['workflow_progress_pct']
                    )
            except Exception as db_error:
                logger.warning(f"Failed to update workflow progress in DB: {db_error}")

            return state
        
        # Parse JSON response with full response logging for debugging
        logger.info(f"[Quality Reflection] Full LLM response for {state['incident_id']}: {reflection_response[:500]}")
        quality_scores = _parse_quality_scores(reflection_response, state['incident_id'])

        if quality_scores.get("parse_failed") or state.get("reflection_failed"):
            _persist_processing_error(
                state["incident_id"],
                f"[reflect] Quality reflection parse failed: {quality_scores.get('concerns', ['Unknown parse error'])[0]}",
            )
        
        # Update state with scores
        state['correctness_score'] = quality_scores.get('correctness_score', 5.0)
        state['safety_score'] = quality_scores.get('safety_score', 5.0)
        state['code_quality_score'] = quality_scores.get('code_quality_score', 5.0)
        state['completeness_score'] = quality_scores.get('completeness_score', 5.0)
        state['overall_quality_score'] = quality_scores.get('overall_score', 0.5)
        state['quality_concerns'] = quality_scores.get('concerns', [])
        state['quality_recommendation'] = quality_scores.get('recommendation', 'REJECT')
        
        state['current_node'] = 'reflect'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        completed_steps = list(state.get('workflow_completed_steps') or [])
        
        # Add step only if not already completed (prevent duplicates)
        if 'reflect' not in completed_steps:
            completed_steps.append('reflect')
        state['workflow_completed_steps'] = completed_steps
        
        state['workflow_progress_pct'] = len(completed_steps) / WORKFLOW_TOTAL_STEPS

        # Update database with workflow progress
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository

            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state['incident_id'],
                    current_workflow_node='reflect',
                    workflow_completed_steps=state['workflow_completed_steps'],
                    workflow_progress_pct=state['workflow_progress_pct']
                )
        except Exception as db_error:
            logger.warning(f"Failed to update workflow progress in DB: {db_error}")

        # Determine approval path
        overall_score = state['overall_quality_score']
        recommendation = state['quality_recommendation']
        
        if overall_score >= 0.7 and recommendation == 'APPROVE':
            status_msg = f"✅ Fix quality acceptable (score: {overall_score:.2f})"
        else:
            status_msg = f"⚠️ Fix quality concerns (score: {overall_score:.2f}, {len(state['quality_concerns'])} issues)"
        
        state['messages'] = state.get('messages', []) + [
            status_msg,
            f"Recommendation: {recommendation}"
        ]
        
        logger.info(f"[Quality Reflection] {status_msg} for {state['incident_id']}")
        
    except Exception as e:
        logger.error(f"[Quality Reflection] Failed for {state['incident_id']}: {str(e)}")
        logger.warning("[Quality Reflection] Returning safe defaults to allow workflow to continue")

        _persist_processing_error(state["incident_id"], f"[reflect] Quality reflection exception: {str(e)}")
        # Default to safe values on error but do NOT block workflow
        state['correctness_score'] = 5.0
        state['safety_score'] = 5.0
        state['code_quality_score'] = 5.0
        state['completeness_score'] = 5.0
        state['overall_quality_score'] = 0.5
        state['quality_concerns'] = [f"Reflection failed: {str(e)}"]
        state['quality_recommendation'] = 'MANUAL_REVIEW'
        state['reflection_failed'] = True
        state['messages'] = state.get('messages', []) + [
            f"Quality reflection failed: {str(e)}",
            "Using safe default scores - MANUAL REVIEW REQUIRED",
        ]
    
    # ALWAYS update workflow tracking, even on error
    completed_steps = list(state.get('workflow_completed_steps') or [])
    if 'reflect' not in completed_steps:
        completed_steps.append('reflect')
    state['workflow_completed_steps'] = completed_steps
    state['workflow_progress_pct'] = len(completed_steps) / WORKFLOW_TOTAL_STEPS

    # Update database with workflow progress
    try:
        from storage.database import get_session
        from storage.incident_repository import IncidentRepository
        
        with get_session() as session:
            repo = IncidentRepository(session)
            repo.update(
                incident_id=state['incident_id'],
                current_workflow_node='reflect',
                workflow_completed_steps=state['workflow_completed_steps'],
                workflow_progress_pct=state['workflow_progress_pct']
            )
    except Exception as db_error:
        logger.warning(f"Failed to update workflow progress in DB: {db_error}")
    
    return state


def _parse_quality_scores(response: str, incident_id: str = "unknown") -> dict:
    """
    Parse quality assessment response with robust error handling.
    
    Expected JSON format:
    {
        "correctness_score": 8,
        "safety_score": 9,
        "code_quality_score": 7,
        "completeness_score": 8,
        "overall_score": 0.8,
        "concerns": ["concern1", "concern2"],
        "recommendation": "APPROVE",
        "reasoning": "explanation"
    }
    
    Returns:
        Dict with quality scores
    """
    original_response = response
    json_str: Optional[str] = None

    try:
        # Aggressively clean the response
        response = response.strip()
        
        logger.debug(f"[JSON Parse] Original response length: {len(original_response)}")
        logger.debug(f"[JSON Parse] First 300 chars: {repr(response[:300])}")
        
        # Strategy 1: Try to find JSON object by matching balanced braces (most reliable)
        brace_count = 0
        start_idx = -1
        end_idx = -1
        
        for i, char in enumerate(response):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx != -1:
                    end_idx = i
                    break
        
        json_str = None
        
        if start_idx != -1 and end_idx != -1:
            json_str = response[start_idx:end_idx + 1]
            logger.debug(f"[JSON Parse] Strategy 1: Extracted by brace matching (len={len(json_str)})")
        else:
            # Strategy 2: Try to extract JSON from code fence
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1).strip()
                logger.debug(f"[JSON Parse] Strategy 2: Extracted from code fence (len={len(json_str)})")
                # Now try to find braces within this
                if not json_str.startswith('{'):
                    first_brace = json_str.find('{')
                    if first_brace != -1:
                        last_brace = json_str.rfind('}')
                        if last_brace != -1:
                            json_str = json_str[first_brace:last_brace + 1]
            else:
                # Strategy 3: Check if response looks like JSON fields without outer braces
                # Look for patterns like: "field": value
                if '"' in response and ':' in response:
                    # Find all content between first quote and last brace or quote
                    # This handles cases like: "correctness_score": 8, "safety_score": 9...
                    logger.warning(f"[JSON Parse] Strategy 3: Response appears to have JSON fields without braces")
                    
                    # Try to intelligently wrap it
                    # Remove any leading non-JSON characters
                    first_quote = response.find('"')
                    if first_quote > 0:
                        response = response[first_quote:]
                    
                    # Remove any trailing non-JSON characters after last brace or number
                    last_brace = response.rfind('}')
                    last_bracket = response.rfind(']')
                    last_quote = response.rfind('"')
                    
                    end_pos = max(last_brace, last_bracket, last_quote)
                    if end_pos > 0 and end_pos < len(response) - 1:
                        # Find the actual end - could be after closing quote
                        if response[end_pos] == '"':
                            end_pos += 1
                        response = response[:end_pos]
                    
                    # Now wrap in braces
                    json_str = '{' + response + '}'
                    logger.debug(f"[JSON Parse] Strategy 3: Wrapped response in braces (len={len(json_str)})")
                else:
                    logger.error(f"[JSON Parse] No JSON-like content found in response")
                    return _get_default_scores('Could not find JSON in LLM response')
        
        if not json_str:
            logger.error(f"[JSON Parse] All strategies failed to extract JSON")
            return _get_default_scores('No JSON content found after all extraction attempts')
        
        # Clean and normalize the extracted JSON string
        json_str = json_str.strip()
        
        # Remove any BOM or special characters at the start
        json_str = json_str.lstrip('\ufeff\n\r\t ')
        
        # Ensure it starts with opening brace
        if not json_str.startswith('{'):
            logger.warning(f"[JSON Parse] JSON doesn't start with brace: {repr(json_str[:50])}")
            first_brace = json_str.find('{')
            if first_brace != -1:
                json_str = json_str[first_brace:]
                logger.debug(f"[JSON Parse] Adjusted to start at first brace")
            else:
                logger.error(f"[JSON Parse] No opening brace found")
                return _get_default_scores('No opening brace in extracted content')
        
        # Ensure it ends with closing brace
        if not json_str.endswith('}'):
            logger.warning(f"[JSON Parse] JSON doesn't end with brace: {repr(json_str[-50:])}")
            last_brace = json_str.rfind('}')
            if last_brace != -1:
                json_str = json_str[:last_brace + 1]
                logger.debug(f"[JSON Parse] Adjusted to end at last brace")
        
        # Try parsing with json.loads
        logger.debug(f"[JSON Parse] Attempting json.loads on cleaned string (len={len(json_str)})")
        logger.debug(f"[JSON Parse] JSON content: {repr(json_str[:200])}")
        
        scores = json.loads(json_str)
        logger.info(f"[JSON Parse] ✓ Successfully parsed JSON for {incident_id}")
        
        # Validate and normalize scores
        scores['correctness_score'] = float(scores.get('correctness_score', 5.0))
        scores['safety_score'] = float(scores.get('safety_score', 5.0))
        scores['code_quality_score'] = float(scores.get('code_quality_score', 5.0))
        scores['completeness_score'] = float(scores.get('completeness_score', 5.0))
        
        # Calculate overall score if not provided
        if 'overall_score' not in scores:
            avg = (
                scores['correctness_score'] +
                scores['safety_score'] +
                scores['code_quality_score'] +
                scores['completeness_score']
            ) / 40.0  # Normalize to 0-1 (scores are out of 10)
            scores['overall_score'] = avg
        else:
            scores['overall_score'] = float(scores['overall_score'])
        
        # Ensure concerns is a list
        if 'concerns' not in scores or not isinstance(scores['concerns'], list):
            scores['concerns'] = []
        
        # Ensure recommendation is valid
        if 'recommendation' not in scores or scores['recommendation'] not in ['APPROVE', 'REJECT', 'MANUAL_REVIEW']:
            scores['recommendation'] = 'MANUAL_REVIEW'
        
        logger.info(f"[JSON Parse] ✓ Parsed quality scores: overall={scores.get('overall_score', 0.5):.2f}, recommendation={scores.get('recommendation', 'UNKNOWN')}")
        return scores
        
    except json.JSONDecodeError as e:
        logger.error(f"[JSON Parse] ✗ JSON decode error for {incident_id}: {str(e)}")
        logger.error(f"[JSON Parse] Position: line {e.lineno} column {e.colno}")
        logger.error(f"[JSON Parse] Original response (first 500): {original_response[:500]}")
        logger.error(f"[JSON Parse] Extracted JSON (first 300): {json_str[:300] if json_str else 'N/A'}")
        
        # Fallback: attempt to salvage values from truncated/partial JSON using regex.
        # This addresses common failure mode where the provider connection resets mid-response
        # and we only receive fragments like: '\n  "correctness_score"'
        try:
            text = original_response or ""
            salvaged: dict = {}
            
            def _find_number(key: str) -> Optional[float]:
                m = re.search(rf'"{re.escape(key)}"\s*:\s*(-?\d+(?:\.\d+)?)', text)
                return float(m.group(1)) if m else None
            
            for k in ["correctness_score", "safety_score", "code_quality_score", "completeness_score", "overall_score"]:
                v = _find_number(k)
                if v is not None:
                    salvaged[k] = v
            
            m_rec = re.search(r'"recommendation"\s*:\s*"([^"]+)"', text)
            if m_rec:
                salvaged["recommendation"] = m_rec.group(1).strip()
            
            # If we salvaged anything meaningful, return a normalized dict
            if salvaged:
                logger.warning(f"[JSON Parse] Salvaged partial scores for {incident_id}: {salvaged}")
                out = _get_default_scores(f"Partial JSON salvage after decode error: {str(e)}")
                out.update(salvaged)
                # Ensure recommendation is valid
                if out.get("recommendation") not in ["APPROVE", "REJECT", "MANUAL_REVIEW"]:
                    out["recommendation"] = "MANUAL_REVIEW"
                # If overall_score missing, compute from component scores
                if "overall_score" not in out or out["overall_score"] is None:
                    out["overall_score"] = (
                        float(out.get("correctness_score", 5.0))
                        + float(out.get("safety_score", 5.0))
                        + float(out.get("code_quality_score", 5.0))
                        + float(out.get("completeness_score", 5.0))
                    ) / 40.0
                return out
        except Exception as salvage_err:
            logger.warning(f"[JSON Parse] Salvage attempt failed for {incident_id}: {salvage_err}")
        
        return _get_default_scores(f'JSON parsing failed at line {e.lineno}: {str(e)}')
        
    except Exception as e:
        logger.error(f"[JSON Parse] ✗ Unexpected error for {incident_id}: {str(e)}")
        logger.error(f"[JSON Parse] Original response (first 500): {original_response[:500]}")
        logger.error(f"[JSON Parse] Extracted JSON (first 300): {json_str[:300] if json_str else 'N/A'}")
        return _get_default_scores(f'Unexpected error: {str(e)}')


def _get_default_scores(error_msg: str) -> dict:
    """Return safe default scores when parsing fails."""
    logger.warning(f"[JSON Parse] Returning default scores due to: {error_msg}")
    return {
        "parse_failed": True,
        'correctness_score': 5.0,
        'safety_score': 5.0,
        'code_quality_score': 5.0,
        'completeness_score': 5.0,
        'overall_score': 0.5,
        'concerns': [error_msg],
        'recommendation': 'MANUAL_REVIEW',
        'reasoning': 'Could not parse quality assessment response'
    }
