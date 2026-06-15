"""Severity assessment node - determines if auto-fix should be triggered."""
import logging
from datetime import datetime
from agents.state import AgentState
from utils.severity_analyzer import SeverityAnalyzer
from storage.models import Severity

logger = logging.getLogger(__name__)


def assess_severity_node(state: AgentState) -> AgentState:
    """
    Assess error severity and determine if auto-fix workflow should proceed.
    
    This node:
    1. Uses OTLP severity if available, otherwise keyword-based classification
    2. Determines if auto-fix should be triggered
    3. Sets requires_approval flag based on severity
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state with severity assessment results
    """
    logger.info(f"[Severity Assessment] Processing incident {state['incident_id']}")
    
    analyzer = SeverityAnalyzer()
    
    # Check if severity was already provided from OTLP
    existing_severity = state.get('severity')
    
    if existing_severity and existing_severity in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
        # Use OTLP severity - DO NOT override
        try:
            severity = Severity[existing_severity]
            confidence = 1.0  # OTLP severity has 100% confidence
            logger.info(f"[Severity Assessment] Using OTLP severity: {severity.value}")
        except (KeyError, ValueError):
            # Fallback to analyzer if invalid severity
            logger.warning(f"[Severity Assessment] Invalid OTLP severity '{existing_severity}', re-analyzing")
            severity, confidence = analyzer.analyze_severity(
                error_title=state['error_title'],
                error_description=state['error_description'],
                stack_trace=state['stack_trace'],
                app_name=state['app_name']
            )
    else:
        # No OTLP severity - use keyword-based analyzer
        logger.info(f"[Severity Assessment] No OTLP severity found, analyzing...")
        severity, confidence = analyzer.analyze_severity(
            error_title=state['error_title'],
            error_description=state['error_description'],
            stack_trace=state['stack_trace'],
            app_name=state['app_name']
        )
    
    # Check if auto-fix should be triggered
    should_fix, reason = analyzer.should_auto_fix(
        severity=severity,
        recent_error_count=0,  # TODO: Query from database
        is_duplicate=state['is_duplicate']
    )
    
    # Update state
    state['severity'] = severity.value
    state['current_node'] = 'assess_severity'
    state['updated_at'] = datetime.utcnow().isoformat()
    
    # Update workflow tracking
    if 'workflow_completed_steps' not in state:
        state['workflow_completed_steps'] = []
    
    # Add step only if not already completed (prevent duplicates)
    if 'assess_severity' not in state['workflow_completed_steps']:
        state['workflow_completed_steps'].append('assess_severity')
    
    # Calculate progress based on 11 total workflow steps
    state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
    
    # Determine if human approval is required
    # HIGH and CRITICAL always require approval
    state['requires_approval'] = severity in [Severity.HIGH, Severity.CRITICAL]
    
    # Update database with workflow progress
    try:
        from storage.database import get_session
        from storage.incident_repository import IncidentRepository
        
        with get_session() as session:
            repo = IncidentRepository(session)
            repo.update(
                incident_id=state['incident_id'],
                current_workflow_node='assess_severity',
                workflow_completed_steps=state['workflow_completed_steps'],
                workflow_progress_pct=state['workflow_progress_pct'],
                severity=severity.value
            )
    except Exception as e:
        logger.warning(f"Failed to update workflow progress in DB: {e}")
    
    if should_fix:
        state['messages'] = [
            f"✓ Severity assessed as {severity.value} (confidence: {confidence:.2f})",
            f"Auto-fix triggered: {reason}"
        ]
        logger.info(f"[Severity Assessment] Auto-fix triggered for {state['incident_id']}: {reason}")
    else:
        state['messages'] = [
            f"✓ Severity assessed as {severity.value} (confidence: {confidence:.2f})",
            f"Auto-fix NOT triggered: {reason}"
        ]
        logger.info(f"[Severity Assessment] Auto-fix skipped for {state['incident_id']}: {reason}")

    # Send "Incident Created" notification so the team is alerted immediately
    try:
        from agents.workflow import _send_event_notification
        _send_event_notification(
            event="Incident Created",
            incident_id=state['incident_id'],
            severity=severity.value,
            app_name=state.get('app_name', ''),
            environment=state.get('environment', ''),
            details=(
                f"New incident detected: {state.get('error_title', 'Unknown')}\n"
                f"Severity: {severity.value}\n"
                f"Auto-fix: {'triggered' if should_fix else 'not triggered'} — {reason}"
            ),
            project_id=state.get('project_id'),
        )
    except Exception as _notify_err:
        logger.warning("[Severity Assessment] Could not send incident-created notification: %s", _notify_err)

    return state
