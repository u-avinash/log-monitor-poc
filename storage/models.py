"""Pydantic models for data validation and serialization."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class Severity(str, Enum):
    """Incident severity levels."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    """Incident processing status."""
    DETECTED = "DETECTED"
    ANALYZING = "ANALYZING"
    RCA_COMPLETE = "RCA_COMPLETE"
    ALERTED = "ALERTED"
    JIRA_CREATED = "JIRA_CREATED"
    FIX_GENERATED = "FIX_GENERATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PR_CREATED = "PR_CREATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IncidentCreate(BaseModel):
    """Model for creating a new incident."""
    app_name: str
    environment: str
    error_title: str
    error_description: str
    stack_trace: str
    raw_log: str
    severity: Optional[str] = None  # Allow OTLP parser to pass calculated severity
    metadata: Optional[dict] = None  # OTLP custom attributes and telemetry data
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TelemetryLogCreate(BaseModel):
    """Model for persisting a telemetry log independently of incident creation."""
    app_name: str
    environment: str
    message: str
    severity: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    observed_timestamp: Optional[datetime] = None
    deployment_type: Optional[str] = None
    severity_number: Optional[int] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    flow_name: Optional[str] = None
    logger_name: Optional[str] = None
    service_name: Optional[str] = None
    source_scope: Optional[str] = None
    raw_payload: str
    attributes: Dict[str, Any] = Field(default_factory=dict)


class TelemetryLogResponse(BaseModel):
    """Model for telemetry log API responses."""
    log_id: str
    app_name: str
    environment: str
    message: str
    severity: str
    timestamp: datetime
    observed_timestamp: Optional[datetime] = None
    deployment_type: Optional[str] = None
    severity_number: Optional[int] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    flow_name: Optional[str] = None
    logger_name: Optional[str] = None
    service_name: Optional[str] = None
    source_scope: Optional[str] = None
    raw_payload: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    incident_created: bool = False
    incident_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class IncidentResponse(BaseModel):
    """Model for incident API responses."""
    incident_id: str  # 4-character alphanumeric ID (e.g., A7CB)
    app_name: str
    environment: str
    error_title: str
    error_description: str
    stack_trace: str
    error_fingerprint: Optional[str] = None
    severity: Optional[str] = None
    status: str
    occurrence_count: int = 1
    last_occurrence_at: Optional[datetime] = None
    
    # Workflow tracking
    current_workflow_node: Optional[str] = None
    workflow_completed_steps: List[str] = Field(default_factory=list)
    workflow_progress_pct: Optional[float] = None
    
    # RCA fields
    rca_text: Optional[str] = None
    rca_confidence: Optional[float] = None
    pdf_path: Optional[str] = None
    
    # Integration fields
    jira_ticket_key: Optional[str] = None
    jira_ticket_url: Optional[str] = None
    jira_error: Optional[str] = None
    alert_channels: List[str] = Field(default_factory=list)
    
    # Notification status
    slack_notification_sent: Optional[bool] = None
    teams_notification_sent: Optional[bool] = None
    notification_errors: List[str] = Field(default_factory=list)
    
    # Fix fields
    proposed_fix: Optional[str] = None
    fix_explanation: Optional[str] = None
    patch_path: Optional[str] = None
    fix_quality_score: Optional[float] = None
    fix_approved: Optional[bool] = None
    
    # Approval fields
    approval_status: Optional[str] = None  # 'approved', 'rejected', or None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approval_notes: Optional[str] = None
    
    # GitHub metadata
    repo_full_name: Optional[str] = None
    error_file_path: Optional[str] = None
    error_line_number: Optional[int] = None
    error_file_type: Optional[str] = None
    fetched_code: Optional[str] = None
    
    # Git fields
    fix_branch: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    commit_sha: Optional[str] = None
    commit_url: Optional[str] = None
    
    # Metadata
    created_at: datetime
    updated_at: datetime
    processing_errors: List[str] = Field(default_factory=list)
    
    class Config:
        from_attributes = True


class ApprovalRequest(BaseModel):
    """Model for fix approval request."""
    incident_id: str  # 4-character alphanumeric ID (e.g., A7CB)
    approved: bool
    comment: Optional[str] = None
