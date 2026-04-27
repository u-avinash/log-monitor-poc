"""Root Cause Analysis generation node using LLM."""
import logging
from datetime import datetime
import yaml
from agents.state import AgentState
from integrations.llm_provider import LLMProvider
from integrations.github_client import GitHubClient
from utils.code_fetcher import CodeFetcher
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


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
        # Initialize code fetcher
        code_fetcher = CodeFetcher()
        github_client = GitHubClient()
        
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
        
        if file_info and github_client.client:
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
            
            # Fallback to log parsing if not in metadata
            if not repo_full_name:
                app_name = state.get('app_name', '')
                repo_full_name = github_client.extract_repo_from_log(
                    state.get('raw_log', ''),
                    app_name
                )
            
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
        
        # Load prompt template
        with open('config/prompts.yaml', 'r', encoding='utf-8') as f:
            prompts = yaml.safe_load(f)
        
        rca_prompt = prompts['root_cause_analysis']
        
        # Prepare code context for prompt
        if code_context:
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
        
        # Format prompt with incident details and actual code
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

Now provide a comprehensive Root Cause Analysis (400-500 words) that:
1. Identifies the exact cause based on the actual code
2. Explains why this error occurred
3. Describes the impact
4. Provides technical context
"""
        
        # Initialize LLM provider
        llm_provider = LLMProvider()
        
        # Generate RCA
        logger.info(f"[RCA Generation] Calling LLM ({settings.llm_provider})")
        rca_text = llm_provider.invoke(
            prompt=formatted_prompt
        )
        
        # Calculate confidence based on response length and structure
        # Simple heuristic: longer, structured responses are more confident
        word_count = len(rca_text.split())
        confidence = min(word_count / 500.0, 1.0)  # Target: 400-500 words
        
        # Update state
        state['rca_text'] = rca_text
        state['rca_confidence'] = confidence
        state['current_node'] = 'generate_rca'
        state['updated_at'] = datetime.utcnow().isoformat()
        
        # Update workflow tracking
        if 'workflow_completed_steps' not in state:
            state['workflow_completed_steps'] = []
        
        # Add step only if not already completed (prevent duplicates)
        if 'generate_rca' not in state['workflow_completed_steps']:
            state['workflow_completed_steps'].append('generate_rca')
        
        # Calculate progress based on 11 total workflow steps
        state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
        
        state['messages'] = state.get('messages', []) + [
            f"✓ RCA generated successfully ({word_count} words, confidence: {confidence:.2f})"
        ]
        
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
        state['error_message'] = f"RCA generation failed: {str(e)}"
        state['rca_text'] = f"Error generating RCA: {str(e)}"
        state['rca_confidence'] = 0.0
        state['messages'] = state.get('messages', []) + [
            f"❌ RCA generation failed: {str(e)}"
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
