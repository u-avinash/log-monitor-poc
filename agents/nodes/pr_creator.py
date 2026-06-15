"""GitHub PR creation node for agent workflow."""
import logging
from datetime import datetime
from agents.state import AgentState
from config.settings import get_settings
from integrations.github_client import GitHubClient

_settings = get_settings()

logger = logging.getLogger(__name__)


def create_pr_node(state: AgentState) -> AgentState:
    """
    Create GitHub Pull Request with the proposed fix.
    
    This node:
    1. Creates a new branch for the fix
    2. Commits the proposed fix
    3. Creates a PR with detailed description
    4. Updates state with branch and PR information
    5. Links back to Jira ticket
    
    Args:
        state: Current agent state with approved fix
        
    Returns:
        Updated state with PR details
    """
    logger.info(f"[PR Creation] Creating GitHub PR for incident {state['incident_id']}")
    
    # Only create PR if fix was approved
    if state.get('approval_status') != 'approved':
        logger.info(f"[PR Creation] Skipping - fix not approved for incident {state['incident_id']}")
        state['messages'] = state.get('messages', []) + ["ℹ️ PR creation skipped - fix not approved"]
        return state
    
    if not state.get('proposed_fix'):
        logger.warning(f"[PR Creation] No proposed fix available for incident {state['incident_id']}")
        state['messages'] = state.get('messages', []) + ["⚠️ PR creation skipped - no fix available"]
        return state
    
    try:
        try:
            github_client = GitHubClient(project_id=state.get("project_id"))
        except ValueError as cfg_err:
            logger.warning("GitHub not configured, skipping PR creation: %s", cfg_err)
            state['messages'] = state.get('messages', []) + [
                f"ℹ️ GitHub PR creation skipped: {cfg_err}"
            ]
            # Mark that PR was skipped because GitHub is simply not configured
            # (not because of a data/content problem). finalize_node uses this flag
            # to decide whether COMPLETED or JIRA_CREATED is the right terminal status.
            state['pr_github_not_configured'] = True  # type: ignore[typeddict-unknown-key]
            return state

        incident_id = state["incident_id"]
        jira_key = state.get("jira_ticket_key")
        jira_issue_type = (state.get("jira_issue_type") or "Bug").strip().lower()

        # Map Jira work type -> branch prefix (use same prefix everywhere)
        type_to_prefix = {
            "bug": "bug",
            "feature": "feature",
            "request": "request",
            "story": "story",
            "task": "task",
            "epic": "epic",
        }
        work_prefix = type_to_prefix.get(jira_issue_type, "fix")

        # Branch naming:
        # <work_prefix>/<JIRAKEY> when Jira key exists
        # otherwise fallback: fix/incident-<id>
        if jira_key:
            branch_name = f"{work_prefix}/{jira_key}"
        else:
            branch_name = f"fix/incident-{incident_id}"
        
        # Get the actual file path from state (set by RCA/Fix generators)
        error_file_path = state.get('error_file_path')
        repo_full_name = state.get('repo_full_name')
        
        if not error_file_path:
            logger.warning(f"[PR Creation] No file path available for incident {incident_id}")
            state['messages'] = state.get('messages', []) + ["⚠️ PR creation skipped - no file path"]
            return state
        
        if not repo_full_name:
            logger.warning(f"[PR Creation] No repository name available for incident {incident_id}")
            state['messages'] = state.get('messages', []) + ["⚠️ PR creation skipped - no repository"]
            return state
        
        # Create PR with fix to the actual error file
        # Use the complete fixed file content (not just the proposed fix block)
        fixed_file_content = state.get('fixed_file_content')
        
        if not fixed_file_content:
            logger.warning(f"[PR Creation] No fixed file content available, cannot create PR for incident {incident_id}")
            state['messages'] = state.get('messages', []) + ["⚠️ PR creation skipped - no fixed file content"]
            return state
        
        # Commit message: lead with the bare Jira key so Jira smart-commit
        # detection and the DevInfo API both recognise it unambiguously.
        # Format: "KAN-619: Fix: <title>\n\nFixes incident #... in <file>"
        if jira_key:
            commit_msg = (
                f"{jira_key}: Fix: {state.get('error_title', 'Auto-generated fix')}"
                f"\n\nFixes incident #{incident_id} in {error_file_path}"
            )
        else:
            commit_msg = (
                f"Fix: {state.get('error_title', 'Auto-generated fix')}"
                f"\n\nFixes incident #{incident_id} in {error_file_path}"
            )

        # Use only the bare Jira key in the title prefix (not the full branch
        # path like "bug/KAN-619") so that Jira's smart-commit PR scanner and
        # the GitHub-for-Jira webhook handler can recognise the issue key
        # unambiguously. "bug/KAN-619" looks like a file path to the scanner;
        # "[KAN-619]" is the canonical format Jira expects.
        pr_title_prefix = f"[{jira_key}] " if jira_key else ""
        pr_info = github_client.create_fix_pr(
            incident_id=incident_id,
            branch_name=branch_name,
            file_path=error_file_path,  # Use actual file path from error
            file_content=fixed_file_content,  # Use complete fixed file content
            commit_message=commit_msg,
            pr_title=f"{pr_title_prefix}[Auto-Fix] Incident #{incident_id}: {state.get('error_title', 'Fix')}",
            repo_full_name=repo_full_name,  # Use actual repository
            pr_body=f"""## 🤖 Auto-Generated Fix for Incident #{incident_id}

**Repository:** {repo_full_name}  
**File Modified:** `{error_file_path}`  
**Severity:** {state.get('severity', 'UNKNOWN')}  
**Application:** {state.get('app_name', 'Unknown')}  
**Environment:** {state.get('environment', 'Unknown')}  
**Branch:** `{branch_name}`  
**Error Line:** {state.get('error_line_number', 'N/A')}

### 🐛 Error Description
**Type:** {state.get('error_title', 'Unknown Error')}

```
{state.get('error_description', 'N/A')[:300]}
```

### 📋 Root Cause Analysis
{state.get('rca_text', 'N/A')[:1000]}...

### 🔧 Fix Explanation
{state.get('fix_explanation', 'Auto-generated fix based on error analysis')}

### 📊 Changes Summary
- **Lines Changed:** See diff below
- **File Type:** {state.get('error_file_type', 'Unknown')}
- **Original Code:** Fetched from `{branch_name.replace('fix/', '')}`
- **Fixed Code:** Applied automated fix with null checks and proper error handling

### 📎 Related Resources
- **Jira Ticket:** {state.get('jira_ticket_url', 'N/A')}
- **Patch File:** `{state.get('patch_path', 'N/A')}`
- **Dashboard:** {_settings.ui_base_url}
- **Incident ID:** #{incident_id}

### ⚠️ Review Checklist
Before merging this PR, please verify:
- [ ] The fix correctly addresses the root cause
- [ ] Code follows project standards and best practices
- [ ] No unintended side effects introduced
- [ ] Proper error handling is in place
- [ ] Comments are clear and helpful
- [ ] Tests pass successfully
- [ ] No security vulnerabilities introduced
- [ ] Documentation updated if needed

### 🧪 Testing Recommendations
1. Unit test the modified code path
2. Integration test with sample data that caused the error
3. Verify error scenarios are handled gracefully
4. Check performance impact if applicable

---
**🤖 This PR was automatically generated by Prism AI**
*Reviewed and approved by: {state.get('approved_by', 'system')}*  
*Generated at: {datetime.utcnow().isoformat()}*
"""
        )
        
        if pr_info is not None:
            # Update state with PR details
            state['pr_url'] = pr_info['pr_url']
            state['pr_number'] = pr_info['pr_number']
            state['branch_name'] = branch_name
            state['fix_branch'] = branch_name
            state['github_branch'] = branch_name
            state['github_pr_url'] = pr_info['pr_url']
            state['github_pr_number'] = pr_info['pr_number']
            state['commit_sha'] = pr_info.get('commit_sha')
            state['commit_url'] = pr_info.get('commit_url')
            state['current_node'] = 'create_pr'
            state['updated_at'] = datetime.utcnow().isoformat()
            
            # Update workflow tracking
            completed_steps = list(state.get('workflow_completed_steps') or [])
            if 'create_pr' not in completed_steps:
                completed_steps.append('create_pr')
            state['workflow_completed_steps'] = completed_steps
            
            # Calculate progress based on 11 total workflow steps
            state['workflow_progress_pct'] = len(completed_steps) / 11.0
            
            state['messages'] = state.get('messages', []) + [
                f"✓ GitHub PR created: #{pr_info['pr_number']}",
                f"✓ Branch created: {branch_name}"
            ]
            
            # Update database
            try:
                from storage.database import get_session
                from storage.incident_repository import IncidentRepository
                
                with get_session() as session:
                    repo = IncidentRepository(session)
                    repo.update(
                        incident_id=incident_id,
                        current_workflow_node='create_pr',
                        workflow_completed_steps=completed_steps,
                        workflow_progress_pct=state['workflow_progress_pct'],
                        pr_url=pr_info['pr_url'],
                        pr_number=pr_info['pr_number'],
                        fix_branch=branch_name,
                        commit_sha=pr_info.get('commit_sha'),
                        commit_url=pr_info.get('commit_url')
                    )
            except Exception as db_error:
                logger.warning(f"Failed to update PR info in DB: {db_error}")
            
            logger.info(f"[PR Creation] Success for incident {incident_id}: PR #{pr_info['pr_number']}")
        else:
            logger.warning(f"[PR Creation] Failed to create PR for incident {incident_id}")
            state['messages'] = state.get('messages', []) + [
                "⚠️ GitHub PR creation failed - check credentials and permissions"
            ]
    
    except Exception as e:
        logger.error(f"[PR Creation] Failed for incident {state['incident_id']}: {str(e)}")
        state['error_message'] = f"PR creation failed: {str(e)}"
        state['messages'] = state.get('messages', []) + [
            f"❌ PR creation failed: {str(e)}"
        ]
    
    return state
