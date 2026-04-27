"""Regression tests for reflector node error persistence."""
from datetime import datetime

from agents.nodes.reflector import _persist_processing_error
from storage.database import get_session
from storage.incident_repository import IncidentRepository
from storage.models import IncidentCreate


def test_reflector_persists_processing_error():
    with get_session() as session:
        repo = IncidentRepository(session)
        incident = repo.create(
            incident=IncidentCreate(
                app_name="test-app",
                environment="test",
                error_title="TestError",
                error_description="Test description",
                stack_trace="",
                raw_log="",
                timestamp=datetime.utcnow(),
            ),
            error_fingerprint="test-fingerprint-reflector",
            severity="LOW",
        )
        incident_id = incident.incident_id

    _persist_processing_error(incident_id, "[reflect] unit test error")

    with get_session() as session:
        repo = IncidentRepository(session)
        updated = repo.get_by_id(incident_id)
        assert updated is not None
        assert updated.processing_errors is not None
        assert any(e.get("message") == "[reflect] unit test error" for e in updated.processing_errors)
