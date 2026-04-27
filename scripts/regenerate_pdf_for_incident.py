"""
Regenerate a PDF for a single incident ID.

Usage:
  python scripts/regenerate_pdf_for_incident.py O5HX
"""
import sys
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import get_session
from storage.incident_repository import IncidentRepository
from agents.nodes.pdf_report import generate_pdf_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)


def regenerate_pdf(incident_id: str) -> str:
    with get_session() as session:
        repo = IncidentRepository(session)
        inc = repo.get_by_id(incident_id)

        if not inc:
            raise SystemExit(f"Incident {incident_id} not found")

        # pdf_report.py reads these keys (others are optional)
        state = {
            "incident_id": getattr(inc, "id", None) or getattr(inc, "incident_id", None) or incident_id,
            "app_name": inc.app_name,
            "environment": inc.environment,
            "error_title": inc.error_title,
            "error_description": inc.error_description,
            "severity": inc.severity,
            "stack_trace": inc.stack_trace,
            "rca_text": inc.rca_text,
            "rca_confidence": getattr(inc, "rca_confidence", None),
            "proposed_fix": inc.proposed_fix,
            "fix_explanation": inc.fix_explanation,
            "overall_quality_score": getattr(inc, "overall_quality_score", None),
            "pr_url": getattr(inc, "github_pr_url", None),
            "jira_ticket_url": inc.jira_ticket_url,
            "created_at": (
                inc.created_at.isoformat()
                if hasattr(inc, "created_at") and inc.created_at
                else datetime.now().isoformat()
            ),
            "messages": [],
        }

        out = generate_pdf_report(state)
        return out.get("pdf_path")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/regenerate_pdf_for_incident.py <incident_id>")

    incident_id = sys.argv[1]
    pdf_path = regenerate_pdf(incident_id)
    logger.info("Generated PDF: %s", pdf_path)
    print(pdf_path)
