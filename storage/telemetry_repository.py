"""Repository helpers for telemetry log storage and querying."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional, cast

from sqlalchemy.orm import Session

from storage.database import Incident, TelemetryLog
from storage.models import TelemetryLogCreate
from utils.id_generator import generate_incident_id

logger = logging.getLogger(__name__)


class TelemetryLogRepository:
    """Repository for telemetry log persistence and filtered retrieval."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, log: TelemetryLogCreate, **kwargs) -> TelemetryLog:
        """Persist a telemetry log entry."""
        existing_log = self.find_existing(
            timestamp=log.timestamp,
            app_name=log.app_name,
            environment=log.environment,
            message=log.message,
            trace_id=log.trace_id,
        )
        if existing_log:
            return existing_log

        existing_ids = {row[0] for row in self.db.query(TelemetryLog.log_id).all()}
        log_id = generate_incident_id(existing_ids)

        db_log = TelemetryLog(
            log_id=log_id,
            timestamp=log.timestamp,
            observed_timestamp=log.observed_timestamp,
            app_name=log.app_name,
            environment=log.environment,
            deployment_type=log.deployment_type,
            severity=log.severity,
            severity_number=log.severity_number,
            message=log.message,
            error_type=log.error_type,
            error_message=log.error_message,
            trace_id=log.trace_id,
            span_id=log.span_id,
            flow_name=log.flow_name,
            logger_name=log.logger_name,
            service_name=log.service_name,
            source_scope=log.source_scope,
            raw_payload=log.raw_payload,
            attributes=log.attributes,
            incident_created=False,
            **kwargs,
        )
        self.db.add(db_log)
        self.db.commit()
        self.db.refresh(db_log)
        logger.info("Stored telemetry log %s for app=%s env=%s", db_log.log_id, db_log.app_name, db_log.environment)
        return db_log

    def get_by_id(self, log_id: str) -> Optional[TelemetryLog]:
        """Get telemetry log by ID."""
        return self.db.query(TelemetryLog).filter(TelemetryLog.log_id == log_id).first()

    def mark_incident_created(self, log_id: str, incident_id: str) -> Optional[TelemetryLog]:
        """Link a telemetry log to an incident."""
        log = self.get_by_id(log_id)
        if not log:
            return None

        setattr(log, "incident_created", True)
        setattr(log, "incident_id", incident_id)
        setattr(log, "updated_at", datetime.utcnow())
        self.db.commit()
        self.db.refresh(log)
        return log

    def find_existing(
        self,
        timestamp: datetime,
        app_name: str,
        environment: str,
        message: str,
        trace_id: Optional[str] = None,
    ) -> Optional[TelemetryLog]:
        """Return an existing log matching the same ingestion fingerprint."""
        query = self.db.query(TelemetryLog).filter(
            TelemetryLog.timestamp == timestamp,
            TelemetryLog.app_name == app_name,
            TelemetryLog.environment == environment,
            TelemetryLog.message == message,
        )
        if trace_id:
            query = query.filter(TelemetryLog.trace_id == trace_id)
        return query.first()

    def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        environment: Optional[str] = None,
        app_name: Optional[str] = None,
        severity: Optional[str] = None,
        search: Optional[str] = None,
        incident_created: Optional[bool] = None,
    ) -> list[TelemetryLog]:
        """Get telemetry logs with optional filters."""
        query = self.db.query(TelemetryLog)

        if environment:
            query = query.filter(TelemetryLog.environment == environment)
        if app_name:
            query = query.filter(TelemetryLog.app_name == app_name)
        if severity:
            query = query.filter(TelemetryLog.severity == severity)
        if incident_created is not None:
            query = query.filter(TelemetryLog.incident_created == incident_created)
        if search:
            like_term = f"%{search}%"
            query = query.filter(
                (TelemetryLog.message.ilike(like_term))
                | (TelemetryLog.error_message.ilike(like_term))
                | (TelemetryLog.error_type.ilike(like_term))
                | (TelemetryLog.flow_name.ilike(like_term))
                | (TelemetryLog.trace_id.ilike(like_term))
            )

        return (
            query.order_by(TelemetryLog.timestamp.desc(), TelemetryLog.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_filter_values(self) -> dict:
        """Return distinct filter values for log viewer dropdowns."""
        environments = [
            row[0]
            for row in self.db.query(TelemetryLog.environment)
            .filter(TelemetryLog.environment.isnot(None))
            .distinct()
            .order_by(TelemetryLog.environment.asc())
            .all()
            if row[0]
        ]
        apps = [
            row[0]
            for row in self.db.query(TelemetryLog.app_name)
            .filter(TelemetryLog.app_name.isnot(None))
            .distinct()
            .order_by(TelemetryLog.app_name.asc())
            .all()
            if row[0]
        ]
        return {"environments": environments, "applications": apps}

    def serialize(self, log: TelemetryLog) -> dict:
        """Serialize telemetry log for API/template use."""
        return {
            "log_id": log.log_id,
            "timestamp": log.timestamp.isoformat() if getattr(log, "timestamp", None) else None,
            "observed_timestamp": log.observed_timestamp.isoformat() if getattr(log, "observed_timestamp", None) else None,
            "app_name": log.app_name,
            "environment": log.environment,
            "deployment_type": log.deployment_type,
            "severity": log.severity,
            "severity_number": log.severity_number,
            "message": log.message,
            "error_type": log.error_type,
            "error_message": log.error_message,
            "trace_id": log.trace_id,
            "span_id": log.span_id,
            "flow_name": log.flow_name,
            "logger_name": log.logger_name,
            "service_name": log.service_name,
            "source_scope": log.source_scope,
            "incident_created": bool(log.incident_created),
            "incident_id": log.incident_id,
            "attributes": log.attributes or {},
            "raw_payload": self._safe_json(cast(Optional[str], getattr(log, "raw_payload", None))),
        }

    def _safe_json(self, value: Optional[str]) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return value


def get_incident_for_log(db: Session, log_id: str) -> Optional[Incident]:
    """Return incident linked to a telemetry log if present."""
    log = db.query(TelemetryLog).filter(TelemetryLog.log_id == log_id).first()
    if not log or not getattr(log, "incident_id", None):
        return None
    return db.query(Incident).filter(Incident.incident_id == log.incident_id).first()
