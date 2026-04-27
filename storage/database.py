"""SQLAlchemy database setup and models."""
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, Boolean, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
from typing import Generator
from config.settings import get_settings
import logging

logger = logging.getLogger(__name__)

Base = declarative_base()
settings = get_settings()


class Incident(Base):
    """SQLAlchemy model for incidents table."""
    __tablename__ = "incidents"
    
    # Primary key (4-character alphanumeric ID, e.g., A7CB)
    incident_id = Column(String(4), primary_key=True, nullable=False)
    
    # Basic info
    app_name = Column(String(255), nullable=False, index=True)
    environment = Column(String(50), nullable=False, index=True)
    error_title = Column(String(500), nullable=False)
    error_description = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=False)
    raw_log = Column(Text, nullable=False)
    
    # Error detection
    error_fingerprint = Column(String(64), nullable=True, index=True)
    is_duplicate = Column(Boolean, default=False)
    existing_incident_id = Column(String(4), nullable=True)
    occurrence_count = Column(Integer, default=1, nullable=False)  # Track duplicate occurrences
    last_occurrence_at = Column(DateTime, default=datetime.utcnow, nullable=True)  # Last time this error occurred
    
    # Status and severity
    status = Column(String(50), nullable=False, default="DETECTED", index=True)
    severity = Column(String(20), nullable=True, index=True)
    
    # Workflow tracking
    current_workflow_node = Column(String(100), nullable=True)
    workflow_completed_steps = Column(JSON, nullable=True)
    workflow_progress_pct = Column(Float, nullable=True)
    
    # RCA
    rca_text = Column(Text, nullable=True)
    rca_confidence = Column(Float, nullable=True)
    pdf_path = Column(String(500), nullable=True)
    
    # Alerting
    alert_sent = Column(Boolean, default=False)
    alert_channels = Column(JSON, nullable=True)
    
    # Notification status
    slack_notification_sent = Column(Boolean, default=False, nullable=True)
    teams_notification_sent = Column(Boolean, default=False, nullable=True)
    notification_errors = Column(JSON, nullable=True)
    
    # Jira
    jira_ticket_key = Column(String(50), nullable=True, index=True)
    jira_ticket_url = Column(String(500), nullable=True)
    jira_error = Column(Text, nullable=True)
    
    # Auto-fix decision
    should_auto_fix = Column(Boolean, default=False)
    
    # Code fix
    proposed_fix = Column(Text, nullable=True)
    fix_explanation = Column(Text, nullable=True)
    patch_path = Column(String(500), nullable=True)
    fix_quality_score = Column(Float, nullable=True)
    fix_approved = Column(Boolean, nullable=True)
    fix_approval_comment = Column(Text, nullable=True)
    
    # Approval tracking (NEW - for HIGH severity approval workflow)
    approval_status = Column(String(20), nullable=True)  # 'approved', 'rejected', or None
    approved_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_notes = Column(Text, nullable=True)
    
    # GitHub metadata (for code fetching)
    repo_full_name = Column(String(255), nullable=True)
    error_file_path = Column(String(500), nullable=True)
    error_line_number = Column(Integer, nullable=True)
    error_file_type = Column(String(50), nullable=True)
    fetched_code = Column(Text, nullable=True)  # Original code from GitHub
    
    # OTLP metadata (store custom attributes and other telemetry data)
    # Using incident_metadata instead of 'metadata' (reserved name in SQLAlchemy)
    incident_metadata = Column(JSON, nullable=True)
    
    # Git operations
    repo_path = Column(String(500), nullable=True)
    fix_branch = Column(String(255), nullable=True)
    fix_committed = Column(Boolean, default=False)
    
    # Pull request
    pr_number = Column(Integer, nullable=True)
    pr_url = Column(String(500), nullable=True)
    commit_sha = Column(String(255), nullable=True)
    commit_url = Column(String(500), nullable=True)
    
    # Metadata
    processing_errors = Column(JSON, nullable=True)
    processing_duration_seconds = Column(Float, nullable=True)
    retries = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


def get_engine():
    """Create and return database engine."""
    database_url = f"sqlite:///{settings.database_path}"
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},  # Needed for SQLite
        echo=settings.log_level == "DEBUG"
    )
    return engine


def get_session_factory():
    """Create and return session factory."""
    engine = get_engine()
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Dependency to get database session."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session():
    """Context manager to get database session."""
    from contextlib import contextmanager
    
    @contextmanager
    def _session():
        SessionLocal = get_session_factory()
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    return _session()


def init_database():
    """Initialize database tables."""
    logger.info(f"Initializing database at {settings.database_path}")
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully")
