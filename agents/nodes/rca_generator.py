"""Root Cause Analysis generation node using LLM."""
import logging
from datetime import datetime
import yaml
from agents.state import AgentState, WORKFLOW_TOTAL_STEPS
from integrations.llm_provider import LLMProvider
from integrations.github_client import GitHubClient
from utils.code_fetcher import CodeFetcher

logger = logging.getLogger(__name__)


def generate_rca_node(state: AgentState) -> AgentState:
    """
    Generate Root Cause Analysis using LLM.
    
    This node:
    1. Loads the RCA prompt template
    2. Formats it with incident details
    3. Calls LLM to generate comprehensive RCA (400-500 words)
    4. Stores result in state
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state with RCA text and confidence score
    """
    logger.info(f"[RCA Generation] Generating analysis for incident {state['incident_id']}")
    
    try:
        # Initialize code fetcher with project context so GitHub token is loaded
        code_fetcher = CodeFetcher(project_id=state.get('project_id'))
        try:
            github_client = GitHubClient(project_id=state.get('project_id'))
            _github_available = True
        except ValueError as _gh_err:
            logger.warning("GitHub not configured for project, skipping code fetch: %s", _gh_err)
            github_client = None
            _github_available = False

        # Extract file information from error logs (with OTLP metadata priority)
        file_info = code_fetcher.extract_error_file_info(
            raw_log=state.get('raw_log', ''),
            stack_trace=state.get('stack_trace', ''),
            error_title=state.get('error_title', ''),
            metadata=state.get('metadata')
        )
        
        # Fetch actual code from GitHub if possible
        code_context = None
        repo_full_name = None
        
        # ALWAYS store file info in STATE if available (critical for next nodes!)
        if file_info:
            state['error_file_path'] = file_info.get('file_path')
            state['error_line_number'] = file_info.get('line_number')
            state['error_file_type'] = file_info.get('file_type')
            logger.info(f"[RCA Generation] ✓ Stored file info in STATE: {file_info.get('file_path')} line {file_info.get('line_number')}")
        
        if file_info and _github_available and github_client:
            # Determine repository - PRIORITY: metadata first, then log parsing
            metadata = state.get('metadata')
            if metadata and 'custom_attributes' in metadata:
                attrs = metadata['custom_attributes']
                repo_full_name = (
                    attrs.get('github_repo') or
                    attrs.get('github.repo') or
                    attrs.get('repo') or
                    attrs.get('repository')
                )
                if repo_full_name:
                    logger.info(f"[RCA Generation] ✓ Found repo in metadata: {repo_full_name}")
            
            # Validate: metadata value must look like "org/repo", not just an org name.
            # If it contains no slash it is likely the org/user only — discard and resolve properly.
            if repo_full_name and '/' not in repo_full_name:
                logger.warning(
                    "[RCA Generation] Metadata repo '%s' has no slash (looks like org-only); "
                    "falling through to extract_repo_from_log",
                    repo_full_name,
                )
                repo_full_name = None

            # Fallback to log parsing / DB mapping if metadata didn't give a full org/repo
            if not repo_full_name:
                app_name = state.get('app_name', '')
                try:
                    repo_full_name = github_client.extract_repo_from_log(
                        state.get('raw_log', ''),
                        app_name,
                    )
                except ValueError as resolve_err:
                    logger.warning("[RCA Generation] Could not resolve repo: %s", resolve_err)
                    repo_full_name = None
            
            # Store repo name in STATE immediately (critical for next nodes!)
            if repo_full_name:
                state['repo_full_name'] = repo_full_name
                logger.info(f"[RCA Generation] ✓ Stored repo in STATE: {repo_full_name}")
            
            if repo_full_name and file_info.get('file_path'):
                logger.info(f"[RCA Generation] Fetching code from {repo_full_name}/{file_info['file_path']}")
                try:
                    code_context = code_fetcher.fetch_code_for_analysis(
                        repo_full_name=repo_full_name,
                        file_path=file_info['file_path'],
                        line_number=file_info.get('line_number'),
                        context_lines=20
                    )
                    
                    if code_context:
                        logger.info(f"[RCA Generation] ✓ Code fetched successfully")
                    else:
                        logger.warning(f"[RCA Generation] ✗ Code fetch returned None")
                except Exception as fetch_error:
                    logger.warning(f"[RCA Generation] ✗ Code fetch failed: {fetch_error}")
            else:
                logger.warning(f"[RCA Generation] Missing repo or file path for code fetch")
        
        # Fetch Anypoint runtime context if the project has it configured
        anypoint_section = _fetch_anypoint_context(
            project_id=state.get('project_id'),
            app_name=state.get('app_name', ''),
            environment=state.get('environment', ''),
        )

        # Load prompt template
        with open('config/prompts.yaml', 'r', encoding='utf-8') as f:
            prompts = yaml.safe_load(f)
        
        rca_prompt = prompts['root_cause_analysis']
        
        # Prepare code context for prompt
        if code_context and file_info:
            code_section = f"""
## Actual Code from Repository
**Repository:** {repo_full_name}
**File:** {file_info['file_path']}
**Error at Line:** {file_info.get('line_number', 'N/A')}

```
{code_context['context_snippet']}
```

**Full File:** {code_context['line_count']} lines total
"""
        else:
            code_section = "[Code not available - GitHub not configured or file not found]"
        
        # Format prompt with incident details, code, and runtime context
        formatted_prompt = f"""{rca_prompt}

## Incident Details
**Application:** {state['app_name']}
**Environment:** {state['environment']}
**Error Title:** {state['error_title']}

## Error Message
{state['error_description']}

## Stack Trace
```
{state['stack_trace'][:2000]}
```

{code_section}

{anypoint_section}

Now provide a comprehensive Root Cause Analysis (400-500 words) that:
1. Identifies the exact cause based on the actual code
2. Explains why this error occurred
3. Describes the impact
4. Provides technical context
"""
        
        # Initialize LLM provider with project-scoped configuration
        llm_provider = LLMProvider(project_id=state.get('project_id'))
        
        # Generate RCA
        logger.info(
            "[RCA Generation] Calling LLM provider=%s model=%s project_id=%s",
            llm_provider.provider,
            llm_provider.model,
            llm_provider.project_id,
        )
        rca_text = llm_provider.invoke(
            prompt=formatted_prompt
        )

        word_count = len(rca_text.split())

        # Score RCA quality using a lightweight second LLM call.
        # Falls back to a word-count heuristic if scoring fails.
        confidence = _score_rca_confidence(
            llm_provider=llm_provider,
            rca_text=rca_text,
            error_title=state.get('error_title', ''),
            has_code_context=code_context is not None,
            word_count=word_count,
        )
        
        # Update state
        state['rca_text'] = rca_text
        state['rca_confidence'] = confidence
        state['current_node'] = 'generate_rca'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        # Update workflow tracking
        completed_steps = list(state.get('workflow_completed_steps') or [])
        if 'generate_rca' not in completed_steps:
            completed_steps.append('generate_rca')
        state['workflow_completed_steps'] = completed_steps
        
        state['workflow_progress_pct'] = len(completed_steps) / WORKFLOW_TOTAL_STEPS
        
        state['messages'] = state.get('messages', []) + [
            f"✓ RCA generated successfully ({word_count} words, confidence: {confidence:.2f})"
        ]

        # Send "RCA Generated" notification
        try:
            from agents.workflow import _send_event_notification
            _send_event_notification(
                event="RCA Generated",
                incident_id=state['incident_id'],
                severity=state.get('severity', 'HIGH'),
                app_name=state.get('app_name', ''),
                environment=state.get('environment', ''),
                details=(
                    f"Root Cause Analysis completed for: {state.get('error_title', 'Unknown')}\n"
                    f"Confidence: {confidence:.2f}  |  Length: {word_count} words\n\n"
                    f"Summary:\n{rca_text[:400]}{'...' if len(rca_text) > 400 else ''}"
                ),
                project_id=state.get('project_id'),
            )
        except Exception as _notify_err:
            logger.warning("[RCA Generation] Could not send RCA-generated notification: %s", _notify_err)

        # Update database with workflow progress
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo = IncidentRepository(session)
                update_data = {
                    'current_workflow_node': 'generate_rca',
                    'workflow_completed_steps': state['workflow_completed_steps'],
                    'workflow_progress_pct': state['workflow_progress_pct'],
                    'rca_text': rca_text,
                    'rca_confidence': confidence
                }
                
                # Add GitHub metadata if available
                if state.get('repo_full_name'):
                    update_data['repo_full_name'] = state['repo_full_name']
                if state.get('error_file_path'):
                    update_data['error_file_path'] = state['error_file_path']
                if state.get('error_line_number'):
                    update_data['error_line_number'] = state['error_line_number']
                if state.get('error_file_type'):
                    update_data['error_file_type'] = state['error_file_type']
                
                repo.update(incident_id=state['incident_id'], **update_data)
        except Exception as db_error:
            logger.warning(f"Failed to update workflow progress in DB: {db_error}")
        
        logger.info(f"[RCA Generation] Success for {state['incident_id']} - {word_count} words")
        
    except Exception as e:
        logger.error(f"[RCA Generation] Failed for {state['incident_id']}: {str(e)}")
        error_text = str(e)
        if "Missing API key for provider" in error_text:
            error_text = (
                f"{error_text} "
                f"(incident={state['incident_id']}, app={state.get('app_name', 'unknown')}, project_id={state.get('project_id')})"
            )
        state['error_message'] = f"RCA generation failed: {error_text}"
        state['rca_text'] = f"Error generating RCA: {error_text}"
        state['rca_confidence'] = 0.0
        state['messages'] = state.get('messages', []) + [
            f"❌ RCA generation failed: {error_text}"
        ]
        
        # Still update workflow tracking even on failure
        try:
            from storage.database import get_session
            from storage.incident_repository import IncidentRepository
            
            with get_session() as session:
                repo = IncidentRepository(session)
                repo.update(
                    incident_id=state['incident_id'],
                    current_workflow_node='generate_rca_failed',
                    workflow_progress_pct=state.get('workflow_progress_pct', 0.0)
                )
        except Exception as db_error:
            logger.warning(f"Failed to update failure status in DB: {db_error}")
    
    return state


# ---------------------------------------------------------------------------
# RCA confidence scoring helper
# ---------------------------------------------------------------------------

def _score_rca_confidence(
    llm_provider: LLMProvider,
    rca_text: str,
    error_title: str,
    has_code_context: bool,
    word_count: int,
) -> float:
    """
    Score the quality of an RCA using a lightweight LLM self-evaluation call.

    Scores four dimensions (1-5 each):
        - accuracy:      Does the analysis correctly identify the root cause?
        - specificity:   Does it reference specific code, files, or flow names?
        - actionability: Are the recommended actions clear and implementable?
        - completeness:  Are all relevant sections present (cause, impact, fix)?

    Returns a confidence value in [0.0, 1.0].
    Falls back to a word-count heuristic if the LLM call fails.
    """
    import json
    import re

    fallback_confidence = min(word_count / 500.0, 1.0)

    if not rca_text or word_count < 20:
        logger.debug("[RCA Confidence] RCA too short for LLM scoring, using word-count fallback")
        return fallback_confidence

    scoring_prompt = f"""You are evaluating the quality of a Root Cause Analysis (RCA) written by an AI system.

ERROR TITLE: {error_title}
CODE CONTEXT AVAILABLE: {"Yes" if has_code_context else "No"}

RCA TEXT (first 1500 chars):
{rca_text[:1500]}

Score this RCA on four dimensions from 1 (poor) to 5 (excellent):
- accuracy:      Does it correctly identify the root cause based on the available context?
- specificity:   Does it reference specific code paths, files, flow names, or line numbers?
- actionability: Are the recommended fixes clear and immediately implementable?
- completeness:  Does it cover cause, impact, affected components, and prevention?

Respond ONLY with valid JSON (no markdown, no extra text):
{{"accuracy": <1-5>, "specificity": <1-5>, "actionability": <1-5>, "completeness": <1-5>, "reasoning": "<one sentence>"}}"""

    try:
        response = llm_provider.invoke(
            prompt=scoring_prompt,
            system_message="You are a senior engineer evaluating RCA quality. Respond ONLY with the JSON object.",
            temperature=0.1,
            json_mode=True,
        )

        # Extract JSON from response
        response = response.strip()
        # Remove markdown code fences if present
        response = re.sub(r'```json\s*', '', response)
        response = re.sub(r'```\s*$', '', response)

        # Find JSON object
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in scoring response")

        scores = json.loads(match.group(0))

        accuracy = float(scores.get('accuracy', 3))
        specificity = float(scores.get('specificity', 3))
        actionability = float(scores.get('actionability', 3))
        completeness = float(scores.get('completeness', 3))

        # Normalize: average of four 1-5 scores → 0.0-1.0
        raw_avg = (accuracy + specificity + actionability + completeness) / 4.0
        confidence = (raw_avg - 1.0) / 4.0  # map [1,5] → [0.0,1.0]
        confidence = round(max(0.0, min(1.0, confidence)), 3)

        reasoning = scores.get('reasoning', '')
        logger.info(
            "[RCA Confidence] LLM scores — accuracy=%.1f specificity=%.1f actionability=%.1f "
            "completeness=%.1f → confidence=%.3f | %s",
            accuracy, specificity, actionability, completeness, confidence, reasoning,
        )
        return confidence

    except Exception as exc:
        logger.warning(
            "[RCA Confidence] LLM scoring failed (%s), falling back to word-count heuristic (%.2f)",
            exc, fallback_confidence,
        )
        return fallback_confidence


# ---------------------------------------------------------------------------
# Anypoint runtime context helper
# ---------------------------------------------------------------------------

def _fetch_anypoint_context(
    project_id: str | None,
    app_name: str,
    environment: str,
) -> str:
    """
    Fetch CloudHub/ARM deployment context from Anypoint Platform for the
    failing application and return it as a formatted Markdown section.

    Returns an empty string (not an error) if Anypoint is not configured,
    the app is not found, or any network call fails.  This function is
    intentionally best-effort so it never blocks RCA generation.
    """
    if not project_id:
        return ""

    try:
        from storage.auth_store import get_project_config
        from integrations.anypoint_client import AnypointClient

        config = get_project_config(project_id) or {}
        anypoint_cfg = config.get("anypoint") or {}

        org_id = (anypoint_cfg.get("org_id") or "").strip()
        client_id = (anypoint_cfg.get("client_id") or "").strip()
        client_secret = (anypoint_cfg.get("client_secret") or "").strip()

        if not org_id or not client_id or not client_secret:
            logger.debug("[RCA] Anypoint not configured for project %s — skipping runtime context", project_id)
            return ""

        client = AnypointClient(
            org_id=org_id,
            client_id=client_id,
            client_secret=client_secret,
        )

        # Find environment ID by matching name
        env_id: str | None = None
        try:
            environments = client.list_environments()
            for env in environments:
                env_name = (env.get("name") or "").lower()
                if environment.lower() in env_name or env_name in environment.lower():
                    env_id = env.get("id")
                    break
            if not env_id and environments:
                env_id = environments[0].get("id")
        except Exception as env_err:
            logger.debug("[RCA] Anypoint env list failed: %s", env_err)

        if not env_id:
            return ""

        # Try CloudHub app first
        app_data = client.get_cloudhub_application(env_id, app_name)

        if not app_data:
            # Try hybrid/ARM apps
            hybrid_apps = client.list_hybrid_applications(env_id)
            for hybrid_app in hybrid_apps:
                name = (hybrid_app.get("name") or "").lower()
                if app_name.lower() in name or name in app_name.lower():
                    app_data = hybrid_app
                    break

        if not app_data:
            logger.debug("[RCA] App '%s' not found in Anypoint env %s", app_name, env_id)
            return ""

        # Extract key deployment details
        raw_status = (app_data.get("status") or "UNKNOWN").upper()
        mule_version_raw = app_data.get("muleVersion", {})
        mule_version = (
            mule_version_raw.get("version", "")
            if isinstance(mule_version_raw, dict)
            else str(mule_version_raw or "")
        )
        region = app_data.get("region", "")
        last_update = app_data.get("lastUpdateTime", "")
        workers = app_data.get("workers", {})
        worker_type = workers.get("type", {}).get("name", "") if isinstance(workers, dict) else ""
        worker_count = workers.get("amount", 1) if isinstance(workers, dict) else 1
        domain = app_data.get("domain", "")

        # Format last update timestamp if it's a Unix ms timestamp
        last_update_str = str(last_update)
        try:
            if str(last_update).isdigit():
                from datetime import timezone
                ts = int(last_update) / 1000
                last_update_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass

        section = f"""## Anypoint Runtime Context
**Application:** {app_name}
**CloudHub Domain:** {domain or 'N/A'}
**Deployment Status:** {raw_status}
**Mule Runtime Version:** {mule_version or 'N/A'}
**Region:** {region or 'N/A'}
**Workers:** {worker_count}x {worker_type}
**Last Deployed:** {last_update_str or 'N/A'}

*Note: This runtime context was fetched live from Anypoint Platform at the time of RCA generation.*
"""
        logger.info("[RCA] ✓ Anypoint runtime context fetched for app=%s status=%s", app_name, raw_status)
        return section

    except Exception as exc:
        logger.debug("[RCA] Anypoint context fetch failed (non-critical): %s", exc)
        return ""
