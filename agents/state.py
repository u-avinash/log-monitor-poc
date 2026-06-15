"""Agent state definition for LangGraph workflow."""
from typing import TypedDict, Annotated, Optional, List
import operator

# ---------------------------------------------------------------------------
# Workflow constants
# ---------------------------------------------------------------------------

# Total number of steps in the full incident processing workflow.
# Used to calculate workflow_progress_pct consistently across all nodes.
# Update this value when adding or removing workflow nodes.
WORKFLOW_TOTAL_STEPS = 11

# Canonical ordered list of workflow step IDs (matches LangGraph node names)
WORKFLOW_STEP_IDS = [
    "assess_severity",
    "generate_rca",
    "generate_fix",
    "generate_pdf",
    "reflect",
    "await_approval",
    "generate_patch",
    "send_notifications",
    "create_jira",
    "create_pr",
    "finalize",
]


class AgentState(TypedDict):
    """
    State maintained throughout the agent workflow.
    All fields must be JSON-serializable for LangGraph checkpointing.
    """
    # Incident identification
    incident_id: str
    project_id: Optional[str]
    app_name: str
    environment: str
    
    # Error details
    error_title: str
    error_description: str
    stack_trace: str
    raw_log: str
    
    # Processing metadata
    fingerprint: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    is_duplicate: bool
    metadata: Optional[dict]  # OTLP metadata including custom attributes
    
    # Agent outputs
    rca_text: Optional[str]  # Root Cause Analysis
    rca_confidence: Optional[float]  # 0-1

    # Extracted code location / GitHub metadata (populated during RCA)
    repo_full_name: Optional[str]  # e.g. "org/repo"
    error_file_path: Optional[str]  # path in repo
    error_line_number: Optional[int]
    error_file_type: Optional[str]  # "dataweave" | "mule_xml" | "yaml" | "java" | "unknown"

    # Fetched code context (populated during fix generation)
    original_code: Optional[str]  # full file content fetched from GitHub
    fix_type: Optional[str]  # "full" | "conceptual"
    
    proposed_fix: Optional[str]  # Generated code fix
    fix_explanation: Optional[str]  # Human-readable explanation
    affected_files: Optional[List[str]]  # Files to modify
    
    # Quality assessment
    correctness_score: Optional[float]  # 0-10
    safety_score: Optional[float]  # 0-10
    code_quality_score: Optional[float]  # 0-10
    completeness_score: Optional[float]  # 0-10
    overall_quality_score: Optional[float]  # 0-1
    quality_concerns: Optional[List[str]]  # Issues found
    quality_recommendation: Optional[str]  # "APPROVE" or "REJECT"
    reflection_failed: Optional[bool]  # True when quality reflection could not complete normally
    
    # Approval workflow
    requires_approval: bool
    approval_status: Optional[str]  # "pending", "approved", "rejected"
    approval_notes: Optional[str]  # Human feedback
    approved_by: Optional[str]  # User who approved
    approved_at: Optional[str]  # ISO timestamp
    
    # Integration outputs
    github_branch: Optional[str]
    github_pr_url: Optional[str]
    github_pr_number: Optional[int]
    jira_ticket_key: Optional[str]
    jira_ticket_url: Optional[str]
    pdf_path: Optional[str]
    patch_path: Optional[str]

    # Patch / PR artefacts (populated during post-approval workflow)
    fixed_file_content: Optional[str]   # Complete fixed file content for PR commit
    fix_branch: Optional[str]           # Branch name used for the fix
    branch_name: Optional[str]          # GitHub branch name created for PR
    pr_url: Optional[str]               # Pull request URL
    pr_number: Optional[int]            # Pull request number
    commit_sha: Optional[str]           # Commit SHA for the fix
    commit_url: Optional[str]           # Commit URL
    
    # Notifications
    slack_notified: bool
    email_notified: bool
    
    # Workflow control
    current_node: Optional[str]  # Current processing node
    workflow_completed_steps: Optional[List[str]]  # List of completed workflow steps
    workflow_progress_pct: Optional[float]  # Workflow progress percentage (0.0 to 1.0)
    error_message: Optional[str]  # Error if workflow fails
    messages: Annotated[List[str], operator.add]  # Audit trail
    
    # Timestamps (ISO format strings)
    created_at: str
    updated_at: str
    completed_at: Optional[str]


def create_initial_state(
    incident_id: str,
    app_name: str,
    environment: str,
    error_title: str,
    error_description: str,
    stack_trace: str,
    raw_log: str,
    fingerprint: str,
    severity: str,
    is_duplicate: bool,
    created_at: str,
    metadata: Optional[dict] = None,
    project_id: Optional[str] = None
) -> AgentState:
    """
    Create initial agent state from incident data.
    
    Args:
        incident_id: Unique incident identifier
        app_name: Application name
        environment: Environment (production, staging, etc.)
        error_title: Error title/message
        error_description: Detailed error description
        stack_trace: Stack trace
        raw_log: Raw log content
        fingerprint: Error fingerprint for deduplication
        severity: Severity level
        is_duplicate: Whether this is a duplicate error
        created_at: ISO timestamp
        metadata: OTLP metadata including custom attributes
        
    Returns:
        Initial AgentState
    """
    return AgentState(
        # Identification
        incident_id=incident_id,
        project_id=project_id,
        app_name=app_name,
        environment=environment,
        
        # Error details
        error_title=error_title,
        error_description=error_description,
        stack_trace=stack_trace,
        raw_log=raw_log,
        
        # Processing metadata
        fingerprint=fingerprint,
        severity=severity,
        is_duplicate=is_duplicate,
        metadata=metadata,
        
        # Agent outputs (None initially)
        rca_text=None,
        rca_confidence=None,

        # Extracted code location / GitHub metadata
        repo_full_name=None,
        error_file_path=None,
        error_line_number=None,
        error_file_type=None,

        # Fetched code context
        original_code=None,
        fix_type=None,

        proposed_fix=None,
        fix_explanation=None,
        affected_files=None,
        
        # Quality scores (None initially)
        correctness_score=None,
        safety_score=None,
        code_quality_score=None,
        completeness_score=None,
        overall_quality_score=None,
        quality_concerns=None,
        quality_recommendation=None,
        reflection_failed=None,
        
        # Approval workflow
        requires_approval=False,
        approval_status=None,
        approval_notes=None,
        approved_by=None,
        approved_at=None,
        
        # Integration outputs (None initially)
        github_branch=None,
        github_pr_url=None,
        github_pr_number=None,
        jira_ticket_key=None,
        jira_ticket_url=None,
        pdf_path=None,
        patch_path=None,

        # Patch / PR artefacts
        fixed_file_content=None,
        fix_branch=None,
        branch_name=None,
        pr_url=None,
        pr_number=None,
        commit_sha=None,
        commit_url=None,
        
        # Notifications
        slack_notified=False,
        email_notified=False,
        
        # Workflow control
        current_node=None,
        workflow_completed_steps=[],
        workflow_progress_pct=0.0,
        error_message=None,
        messages=[],
        
        # Timestamps
        created_at=created_at,
        updated_at=created_at,
        completed_at=None
    )
