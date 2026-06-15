"""Repository pattern for incident CRUD operations."""
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from storage.database import Incident, IncidentComment
from storage.models import IncidentCreate, IncidentStatus, Severity
from utils.id_generator import generate_incident_id
import logging
import secrets
import string

logger = logging.getLogger(__name__)


class IncidentRepository:
    """Repository for incident database operations."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, incident: IncidentCreate, **kwargs) -> Incident:
        """Create a new incident with a unique alphanumeric ID."""
        # Get existing IDs to avoid collisions
        existing_ids = {inc.incident_id for inc in self.db.query(Incident.incident_id).all()}
        
        # Generate unique incident ID
        incident_id = generate_incident_id(existing_ids)
        
        # Extract metadata from incident if present
        incident_metadata = getattr(incident, 'metadata', None)
        
        # Extract GitHub metadata from custom_attributes if available
        github_repo = None
        github_file_path = None
        github_line_number = None
        
        if incident_metadata and isinstance(incident_metadata, dict):
            custom_attrs = incident_metadata.get('custom_attributes', {})
            if custom_attrs:
                # Extract GitHub info from custom attributes
                github_repo = custom_attrs.get('github.repo')
                github_file_path = custom_attrs.get('github.file_path')
                line_number_str = custom_attrs.get('error.line_number')
                if line_number_str:
                    try:
                        github_line_number = int(line_number_str)
                    except (ValueError, TypeError):
                        pass
                
                logger.info(f"Extracted GitHub metadata - repo: {github_repo}, file: {github_file_path}, line: {github_line_number}")
        
        db_incident = Incident(
            incident_id=incident_id,
            app_name=incident.app_name,
            environment=incident.environment,
            error_title=incident.error_title,
            error_description=incident.error_description,
            stack_trace=incident.stack_trace,
            raw_log=incident.raw_log,
            incident_metadata=incident_metadata,
            repo_full_name=github_repo,
            error_file_path=github_file_path,
            error_line_number=github_line_number,
            created_at=incident.timestamp,
            **kwargs
        )
        self.db.add(db_incident)
        self.db.commit()
        self.db.refresh(db_incident)
        logger.info(f"Created incident {db_incident.incident_id} with metadata: {bool(incident_metadata)}, GitHub repo: {github_repo}")
        return db_incident
    
    def get_by_id(self, incident_id: str) -> Optional[Incident]:
        """Get incident by ID."""
        return self.db.query(Incident).filter(Incident.incident_id == incident_id).first()
    
    def get_by_fingerprint(self, fingerprint: str) -> Optional[Incident]:
        """Find existing incident with same fingerprint."""
        return self.db.query(Incident).filter(
            Incident.error_fingerprint == fingerprint
        ).order_by(Incident.created_at.desc()).first()
    
    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        app_name: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Incident]:
        """Get all incidents with optional filters and full-text search.

        The ``search`` parameter performs a case-insensitive LIKE match across
        ``error_title``, ``error_description``, ``rca_text``, and
        ``stack_trace``.  SQLite does not support full FTS without extensions,
        so we use OR-LIKE which is fast enough for POC scale.
        """
        query = self.db.query(Incident)

        if status:
            query = query.filter(Incident.status == status)
        if severity:
            query = query.filter(Incident.severity == severity)
        if app_name:
            query = query.filter(Incident.app_name.ilike(f"%{app_name}%"))
        if search:
            term = f"%{search}%"
            query = query.filter(
                Incident.error_title.ilike(term)
                | Incident.error_description.ilike(term)
                | Incident.rca_text.ilike(term)
                | Incident.stack_trace.ilike(term)
                | Incident.app_name.ilike(term)
            )

        return query.order_by(Incident.created_at.desc()).offset(offset).limit(limit).all()
    
    def get_recent_by_app(self, app_name: str, minutes: int = 10) -> List[Incident]:
        """Get recent incidents for an app within time window."""
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        return self.db.query(Incident).filter(
            Incident.app_name == app_name,
            Incident.created_at >= cutoff
        ).order_by(Incident.created_at.desc()).all()
    
    def update(self, incident_id: str, **kwargs) -> Optional[Incident]:
        """Update incident fields."""
        incident = self.get_by_id(incident_id)
        if not incident:
            return None
        
        for key, value in kwargs.items():
            if hasattr(incident, key):
                setattr(incident, key, value)
        
        incident.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(incident)
        logger.info(f"Updated incident {incident_id}: {list(kwargs.keys())}")
        return incident
    
    def update_status(self, incident_id: str, status: IncidentStatus) -> Optional[Incident]:
        """Update incident status."""
        return self.update(incident_id, status=status.value)
    
    def add_processing_error(self, incident_id: str, error_message: str) -> Optional[Incident]:
        """Add a processing error to the incident."""
        incident = self.get_by_id(incident_id)
        if not incident:
            return None
        
        errors = incident.processing_errors or []
        errors.append({
            "timestamp": datetime.utcnow().isoformat(),
            "message": error_message
        })
        return self.update(incident_id, processing_errors=errors)
    
    def count_by_status(self) -> dict:
        """Get count of incidents by status."""
        results = self.db.query(
            Incident.status,
            func.count(Incident.incident_id)
        ).group_by(Incident.status).all()
        return {status: count for status, count in results}

    def count_by_severity(self) -> dict:
        """Get count of incidents by severity."""
        results = self.db.query(
            Incident.severity,
            func.count(Incident.incident_id)
        ).filter(Incident.severity.isnot(None)).group_by(Incident.severity).all()
        return {severity: count for severity, count in results}

    def get_stats(self) -> Dict:
        """
        Return aggregate statistics using SQL COUNT queries.

        This is O(1) in memory — no incident rows are loaded into Python.
        """
        total = self.db.query(func.count(Incident.incident_id)).scalar() or 0

        severity_rows = self.count_by_severity()
        status_rows = self.count_by_status()

        pending_approval = (
            self.db.query(func.count(Incident.incident_id))
            .filter(
                (Incident.status == IncidentStatus.PENDING_APPROVAL.value)
                | (Incident.approval_status == "pending")
            )
            .scalar()
            or 0
        )
        prs_created = (
            self.db.query(func.count(Incident.incident_id))
            .filter(Incident.pr_url.isnot(None))
            .scalar()
            or 0
        )
        with_rca = (
            self.db.query(func.count(Incident.incident_id))
            .filter(Incident.rca_text.isnot(None))
            .scalar()
            or 0
        )
        jira_created = (
            self.db.query(func.count(Incident.incident_id))
            .filter(Incident.jira_ticket_url.isnot(None))
            .scalar()
            or 0
        )
        completed = (status_rows.get("COMPLETED", 0) + status_rows.get("PR_CREATED", 0))

        return {
            "total_incidents": total,
            "by_severity": {
                "CRITICAL": severity_rows.get("CRITICAL", 0),
                "HIGH": severity_rows.get("HIGH", 0),
                "MEDIUM": severity_rows.get("MEDIUM", 0),
                "LOW": severity_rows.get("LOW", 0),
            },
            "by_status": status_rows,
            "pending_approval": pending_approval,
            "prs_created": prs_created,
            "with_rca": with_rca,
            "jira_created": jira_created,
            "completed": completed,
        }

    def get_daily_trends(self, days: int = 14) -> List[Dict]:
        """
        Return per-day incident counts and fix-acceptance rates for the last
        ``days`` calendar days.  Uses SQL GROUP BY — no Python-side aggregation.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Daily total counts
        daily_totals = (
            self.db.query(
                cast(Incident.created_at, Date).label("day"),
                func.count(Incident.incident_id).label("total"),
            )
            .filter(Incident.created_at >= cutoff)
            .group_by(cast(Incident.created_at, Date))
            .order_by(cast(Incident.created_at, Date))
            .all()
        )

        # Daily approved counts
        daily_approved = (
            self.db.query(
                cast(Incident.created_at, Date).label("day"),
                func.count(Incident.incident_id).label("approved"),
            )
            .filter(
                Incident.created_at >= cutoff,
                Incident.approval_status == "approved",
            )
            .group_by(cast(Incident.created_at, Date))
            .order_by(cast(Incident.created_at, Date))
            .all()
        )

        # Daily rejected counts
        daily_rejected = (
            self.db.query(
                cast(Incident.created_at, Date).label("day"),
                func.count(Incident.incident_id).label("rejected"),
            )
            .filter(
                Incident.created_at >= cutoff,
                Incident.approval_status == "rejected",
            )
            .group_by(cast(Incident.created_at, Date))
            .order_by(cast(Incident.created_at, Date))
            .all()
        )

        approved_by_day = {str(row.day): row.approved for row in daily_approved}
        rejected_by_day = {str(row.day): row.rejected for row in daily_rejected}

        trends = []
        for row in daily_totals:
            day_str = str(row.day)
            approved = approved_by_day.get(day_str, 0)
            rejected = rejected_by_day.get(day_str, 0)
            total = row.total
            acceptance_rate = round((approved / total) * 100, 1) if total > 0 else 0.0
            trends.append(
                {
                    "date": day_str,
                    "total": total,
                    "approved": approved,
                    "rejected": rejected,
                    "acceptance_rate": acceptance_rate,
                }
            )

        return trends

    def get_mttr_stats(self) -> Dict:
        """
        Compute Mean Time To Resolve (MTTR) for completed incidents.

        MTTR = average seconds from ``created_at`` to ``updated_at``
               for incidents in COMPLETED or PR_CREATED status.
        """
        completed_incidents = (
            self.db.query(Incident.created_at, Incident.updated_at)
            .filter(
                Incident.status.in_(
                    [IncidentStatus.COMPLETED.value, IncidentStatus.PR_CREATED.value]
                ),
                Incident.updated_at.isnot(None),
            )
            .all()
        )

        if not completed_incidents:
            return {"mttr_seconds": None, "mttr_minutes": None, "sample_size": 0}

        durations = [
            (row.updated_at - row.created_at).total_seconds()
            for row in completed_incidents
            if row.updated_at and row.created_at and row.updated_at > row.created_at
        ]

        if not durations:
            return {"mttr_seconds": None, "mttr_minutes": None, "sample_size": 0}

        avg_seconds = sum(durations) / len(durations)
        return {
            "mttr_seconds": round(avg_seconds, 1),
            "mttr_minutes": round(avg_seconds / 60, 1),
            "sample_size": len(durations),
        }

    # ── Comment Thread ────────────────────────────────────────────────────────

    def _generate_comment_id(self) -> str:
        """Generate a short unique comment ID (16 hex chars)."""
        return secrets.token_hex(8)

    def add_comment(
        self,
        incident_id: str,
        content: str,
        author: str = "user",
        comment_type: str = "comment",
    ) -> IncidentComment:
        """Add a comment to an incident thread."""
        comment = IncidentComment(
            comment_id=self._generate_comment_id(),
            incident_id=incident_id,
            author=author,
            content=content.strip(),
            comment_type=comment_type,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(comment)
        self.db.commit()
        self.db.refresh(comment)
        logger.info("Added comment %s to incident %s by %s", comment.comment_id, incident_id, author)
        return comment

    def get_comments(self, incident_id: str) -> List[IncidentComment]:
        """Return all comments for an incident ordered chronologically."""
        return (
            self.db.query(IncidentComment)
            .filter(IncidentComment.incident_id == incident_id)
            .order_by(IncidentComment.created_at.asc())
            .all()
        )

    def delete_comment(self, comment_id: str) -> bool:
        """Delete a comment by ID. Returns True if deleted, False if not found."""
        comment = (
            self.db.query(IncidentComment)
            .filter(IncidentComment.comment_id == comment_id)
            .first()
        )
        if not comment:
            return False
        self.db.delete(comment)
        self.db.commit()
        return True

    def serialize_comment(self, comment: IncidentComment) -> dict:
        """Serialize a comment to a plain dict for JSON responses."""
        return {
            "comment_id": comment.comment_id,
            "incident_id": comment.incident_id,
            "author": comment.author,
            "content": comment.content,
            "comment_type": comment.comment_type,
            "created_at": comment.created_at.isoformat() if comment.created_at else None,
            "updated_at": comment.updated_at.isoformat() if comment.updated_at else None,
        }
