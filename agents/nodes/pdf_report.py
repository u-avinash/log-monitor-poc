"""PDF report generation node for agent workflow."""
import logging
from typing import Any, Dict
from agents.state import AgentState
from utils.pdf_generator import IncidentPDFGenerator
from datetime import datetime

logger = logging.getLogger(__name__)


def generate_pdf_report(state: AgentState) -> Dict[str, Any]:
    """
    Generate PDF report for incident.
    
    This node:
    1. Collects all incident data from state
    2. Generates professional PDF report
    3. Updates state with PDF path
    
    Args:
        state: Current agent state
        
    Returns:
        Updated state with pdf_path
    """
    logger.info(f"Generating PDF report for incident {state.get('incident_id')}")
    
    try:
        # Initialize PDF generator
        pdf_generator = IncidentPDFGenerator()
        
        # Extract data from state
        incident_id = state.get("incident_id", 0)
        app_name = state.get("app_name", "Unknown")
        environment = state.get("environment", "Unknown")
        error_title = state.get("error_title", "Unknown Error")
        error_description = state.get("error_description")
        severity = state.get("severity", "MEDIUM")
        stack_trace = state.get("stack_trace")
        rca_text = state.get("rca_text")
        rca_confidence = state.get("rca_confidence")
        proposed_fix = state.get("proposed_fix")
        fix_explanation = state.get("fix_explanation")
        fix_quality_score = state.get("overall_quality_score")
        pr_url = state.get("pr_url")
        jira_ticket_url = state.get("jira_ticket_url")
        
        # Parse created_at if present
        created_at = None
        if state.get("created_at"):
            try:
                created_at = datetime.fromisoformat(state["created_at"])
            except:
                created_at = datetime.now()
        
        # Generate PDF
        pdf_path = pdf_generator.generate_incident_report(
            incident_id=incident_id,
            app_name=app_name,
            environment=environment,
            error_title=error_title,
            error_description=error_description,
            severity=severity,
            stack_trace=stack_trace,
            rca_text=rca_text,
            rca_confidence=rca_confidence,
            proposed_fix=proposed_fix,
            fix_explanation=fix_explanation,
            fix_quality_score=fix_quality_score,
            pr_url=pr_url,
            jira_ticket_url=jira_ticket_url,
            created_at=created_at
        )
        
        if pdf_path:
            state["pdf_path"] = pdf_path
            logger.info(f"PDF report generated: {pdf_path}")
            
            # Update workflow tracking
            if 'workflow_completed_steps' not in state:
                state['workflow_completed_steps'] = []
            
            # Add step only if not already completed (prevent duplicates)
            if 'generate_pdf' not in state['workflow_completed_steps']:
                state['workflow_completed_steps'].append('generate_pdf')
            
            # Calculate progress based on 11 total workflow steps
            state['workflow_progress_pct'] = len(state['workflow_completed_steps']) / 11.0
            
            # Update database with PDF path and workflow progress
            try:
                from storage.database import get_session
                from storage.incident_repository import IncidentRepository
                
                with get_session() as session:
                    repo = IncidentRepository(session)
                    repo.update(
                        incident_id=incident_id,
                        pdf_path=pdf_path,
                        current_workflow_node='generate_pdf',
                        workflow_completed_steps=state['workflow_completed_steps'],
                        workflow_progress_pct=state['workflow_progress_pct']
                    )
                logger.info(f"PDF path saved to database: {pdf_path}")
            except Exception as db_error:
                logger.warning(f"Failed to update PDF path in DB: {db_error}")
            
            state["messages"].append({
                "role": "system",
                "content": f"✓ PDF report generated: {pdf_path}"
            })
        else:
            logger.error(f"Failed to generate PDF report for incident {incident_id}")
            state["messages"].append({
                "role": "system",
                "content": "Failed to generate PDF report"
            })
        
        return state
        
    except Exception as e:
        logger.error(f"Error generating PDF report: {str(e)}")
        state["messages"].append({
            "role": "system",
            "content": f"Error generating PDF report: {str(e)}"
        })
        return state
