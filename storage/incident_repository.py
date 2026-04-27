"""Repository pattern for incident CRUD operations."""
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
from storage.database import Incident
from storage.models import IncidentCreate, IncidentStatus, Severity
from utils.id_generator import generate_incident_id
import logging

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
        app_name: Optional[str] = None
    ) -> List[Incident]:
        """Get all incidents with optional filters."""
        query = self.db.query(Incident)
        
        if status:
            query = query.filter(Incident.status == status)
        if severity:
            query = query.filter(Incident.severity == severity)
        if app_name:
            query = query.filter(Incident.app_name == app_name)
        
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
        from sqlalchemy import func
        results = self.db.query(
            Incident.status,
            func.count(Incident.incident_id)
        ).group_by(Incident.status).all()
        
        return {status: count for status, count in results}
    
    def count_by_severity(self) -> dict:
        """Get count of incidents by severity."""
        from sqlalchemy import func
        results = self.db.query(
            Incident.severity,
            func.count(Incident.incident_id)
        ).filter(Incident.severity.isnot(None)).group_by(Incident.severity).all()
        
        return {severity: count for severity, count in results}
