"""FastAPI ingestion server for log entries and incidents."""
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Deque, Dict, Any, Tuple
from datetime import datetime
from collections import deque
import sys
import os
import asyncio
import platform
import json

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.database import get_db, init_database
from storage.incident_repository import IncidentRepository
from storage.models import IncidentCreate, IncidentResponse, ApprovalRequest, IncidentStatus, TelemetryLogCreate
from storage.telemetry_repository import TelemetryLogRepository
from config.settings import get_settings
from utils.error_deduplication import deduplicate_error
from utils.severity_analyzer import analyze_severity
from ingestion.otlp_parser import OTLPParser
import logging

logger = logging.getLogger(__name__)


def _persist_telemetry_logs(payload: dict) -> Tuple[TelemetryLogRepository, List[TelemetryLogCreate]]:
    """Parse and persist telemetry logs from an OTLP payload."""
    parser = OTLPParser()
    db = next(get_db())
    telemetry_repo = TelemetryLogRepository(db)
    telemetry_logs = parser.parse_otlp_json_to_logs(payload)
    persisted_logs: List[TelemetryLogCreate] = []

    for telemetry_log in telemetry_logs:
        telemetry_repo.create(telemetry_log)
        persisted_logs.append(telemetry_log)

    return telemetry_repo, persisted_logs


def _link_incident_to_telemetry_log(
    telemetry_repo: TelemetryLogRepository,
    telemetry_logs: List[TelemetryLogCreate],
    incident_data: IncidentCreate,
    incident_id: str,
) -> None:
    """Link a created incident to the matching stored telemetry log."""
    try:
        raw_log = json.loads(incident_data.raw_log)
        raw_message = raw_log.get("message")
    except Exception:
        raw_message = None

    for telemetry_log in telemetry_logs:
        if telemetry_log.app_name != incident_data.app_name:
            continue
        if telemetry_log.environment != incident_data.environment:
            continue
        if raw_message and telemetry_log.message != raw_message:
            continue

        stored_log = telemetry_repo.find_existing(
            timestamp=telemetry_log.timestamp,
            app_name=telemetry_log.app_name,
            environment=telemetry_log.environment,
            message=telemetry_log.message,
            trace_id=telemetry_log.trace_id,
        )
        if stored_log:
            telemetry_repo.mark_incident_created(stored_log.log_id, incident_id)
            return

# Initialize FastAPI app
app = FastAPI(
    title="Prism Ingestion API",
    description="API for ingesting logs and managing incidents",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

settings = get_settings()

# Ensure a clean or newly deleted SQLite DB is fully initialized before serving requests.
init_database()


@app.on_event("startup")
async def _install_asyncio_exception_handler():
    """
    Install an asyncio exception handler to reduce noisy Windows Proactor event-loop
    callback tracebacks like WinError 10054 ("connection forcibly closed").

    These errors are typically transient network disconnects from upstream LLM providers
    and often do not impact the API's ability to continue serving requests.
    """
    try:
        if platform.system().lower() != "windows":
            return

        loop = asyncio.get_running_loop()

        def _handler(loop, context):
            exc = context.get("exception")
            msg = str(context.get("message", ""))
            # Filter known noisy cases
            if exc and isinstance(exc, ConnectionResetError):
                logger.warning("[asyncio] Suppressed ConnectionResetError in event loop: %s", exc)
                return
            if "WinError 10054" in msg or (exc and "WinError 10054" in str(exc)):
                logger.warning("[asyncio] Suppressed WinError 10054 in event loop: %s", exc or msg)
                return

            # Fallback to default logging for other exceptions
            logger.error("[asyncio] Unhandled event loop exception: %s", context, exc_info=exc)

        loop.set_exception_handler(_handler)
        logger.info("[startup] Installed asyncio exception handler for Windows")
    except Exception as e:
        logger.warning("Failed to install asyncio exception handler: %s", e)


# --- Lightweight ingestion diagnostics (in-memory, resets on restart) ---
# Helps confirm whether Mule/OTel Collector is actually hitting this API instance.
_LAST_REQUESTS: Deque[Dict[str, Any]] = deque(maxlen=50)


@app.middleware("http")
async def record_request_middleware(request: Request, call_next):
    start = datetime.utcnow()
    try:
        response = await call_next(request)
        return response
    finally:
        try:
            duration_ms = (datetime.utcnow() - start).total_seconds() * 1000.0
            _LAST_REQUESTS.appendleft({
                "ts": start.isoformat() + "Z",
                "method": request.method,
                "path": request.url.path,
                "query": str(request.url.query) if request.url.query else "",
                "client": request.client.host if request.client else None,
                "content_length": request.headers.get("content-length"),
                "status_code": getattr(locals().get("response", None), "status_code", None),
                "duration_ms": round(duration_ms, 2),
            })
            logger.info(
                "[HTTP] %s %s client=%s status=%s len=%s dur=%.2fms",
                request.method,
                request.url.path,
                request.client.host if request.client else None,
                getattr(locals().get("response", None), "status_code", None),
                request.headers.get("content-length"),
                duration_ms,
            )
        except Exception:
            # Never break ingestion due to diagnostics logging
            pass


async def process_incident_workflow(incident_id: str):
    """
    Background task to process incident through agent workflow.

    Uses run_incident_workflow which properly resolves project_id from the
    incident's app_name so that the correct project's integration configs
    (LLM, Jira, GitHub, etc.) are used.

    Args:
        incident_id: 4-character alphanumeric incident ID (e.g., A7CB)
    """
    try:
        logger.info(f"Starting workflow for incident {incident_id}")

        from agents.workflow import run_incident_workflow, _resolve_project_id_for_incident

        db = next(get_db())
        repo = IncidentRepository(db)
        incident = repo.get_by_id(incident_id)

        if not incident:
            logger.error(f"Incident {incident_id} not found")
            return

        # Resolve the project this incident belongs to so the workflow can load
        # the correct LLM / integration configs even when the app name doesn't
        # directly match a project name.
        project_id = _resolve_project_id_for_incident(
            None,
            incident.app_name,
            incident.environment,
        )

        if not project_id:
            logger.warning(
                "[Workflow] Could not resolve project for incident=%s app=%s env=%s. "
                "Attempting to use any available project LLM config as fallback. "
                "To fix permanently, ensure app names match a project name/repo or configure "
                "app_names in project settings.",
                incident_id,
                incident.app_name,
                incident.environment,
            )
        else:
            logger.info(
                "[Workflow] Resolved project_id=%s for incident=%s app=%s",
                project_id, incident_id, incident.app_name,
            )

        await run_incident_workflow(
            incident_id=incident.incident_id,
            app_name=incident.app_name or "",
            environment=incident.environment or "",
            error_title=incident.error_title or "",
            error_description=incident.error_description or "",
            stack_trace=incident.stack_trace or "",
            raw_log=incident.raw_log or "",
            fingerprint=incident.error_fingerprint or "",
            severity=incident.severity or "HIGH",
            is_duplicate=False,
            metadata=incident.incident_metadata,
            project_id=project_id,
        )

        logger.info(f"Workflow completed for incident {incident_id}")

    except Exception as e:
        logger.error(f"Workflow failed for incident {incident_id}: {str(e)}")
        try:
            db = next(get_db())
            repo = IncidentRepository(db)
            repo.update_status(incident_id, IncidentStatus.FAILED)
            incident = repo.get_by_id(incident_id)
            if incident:
                if not incident.processing_errors:
                    incident.processing_errors = []
                incident.processing_errors.append(f"Workflow error: {str(e)}")
                db.commit()
        except Exception as inner_e:
            logger.error(f"Failed to update error status: {str(inner_e)}")




# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "prism-ingestion-api",
        "db_path": settings.database_path,
        "last_request_count": len(_LAST_REQUESTS),
    }


@app.get("/debug/last-requests")
async def debug_last_requests(limit: int = 20):
    """Return the last inbound HTTP requests observed by this API process."""
    limit = max(1, min(limit, 50))
    return {
        "count": len(_LAST_REQUESTS),
        "items": list(_LAST_REQUESTS)[:limit],
    }


@app.get("/api/logs")
async def get_logs(
    limit: int = 100,
    offset: int = 0,
    environment: Optional[str] = None,
    app_name: Optional[str] = None,
    severity: Optional[str] = None,
    search: Optional[str] = None,
    incident_created: Optional[bool] = None,
):
    """Return persisted telemetry logs with filtering."""
    try:
        db = next(get_db())
        repo = TelemetryLogRepository(db)
        logs = repo.get_all(
            limit=limit,
            offset=offset,
            environment=environment,
            app_name=app_name,
            severity=severity,
            search=search,
            incident_created=incident_created,
        )
        return {
            "items": [repo.serialize(log) for log in logs],
            "filters": repo.get_filter_values(),
            "count": len(logs),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get logs: {str(e)}")


@app.get("/api/logs/filters")
async def get_log_filters():
    """Return available log filter values."""
    try:
        db = next(get_db())
        repo = TelemetryLogRepository(db)
        return repo.get_filter_values()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get log filters: {str(e)}")


@app.get("/debug/incident/{incident_id}/raw")
async def debug_incident_raw(incident_id: str):
    """
    Debug endpoint to view raw incident data including all custom attributes.
    
    This helps verify that custom OTLP fields are being received and stored.
    """
    try:
        db = next(get_db())
        repo = IncidentRepository(db)
        
        incident = repo.get_by_id(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
        
        # Parse raw_log to show OTLP structure
        raw_log_data = None
        if incident.raw_log:
            try:
                raw_log_data = json.loads(incident.raw_log)
            except:
                raw_log_data = incident.raw_log
        
        return {
            "incident_id": incident.incident_id,
            "app_name": incident.app_name,
            "environment": incident.environment,
            "severity": incident.severity,
            "error_title": incident.error_title,
            "metadata": incident.incident_metadata,
            "raw_log": raw_log_data,
            "custom_attributes_count": len(incident.incident_metadata.get("custom_attributes", {})) if incident.incident_metadata else 0,
            "all_custom_attributes": incident.incident_metadata.get("custom_attributes", {}) if incident.incident_metadata else {}
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get debug data: {str(e)}")


# Ingest log entry
@app.post("/ingest/log", response_model=dict)
async def ingest_log(incident: IncidentCreate, background_tasks: BackgroundTasks):
    """
    Ingest a log entry and create an incident.
    
    This endpoint:
    1. Creates an incident in the database
    2. Performs deduplication to check for similar errors
    3. Analyzes severity
    4. (Future) Triggers agent workflow for RCA and auto-fix
    """
    try:
        # Get database session
        db = next(get_db())
        repo = IncidentRepository(db)
        
        # Check for duplicates
        fingerprint = deduplicate_error(
            error_title=incident.error_title,
            stack_trace=incident.stack_trace,
            app_name=incident.app_name
        )
        
        # Check if similar incident exists
        existing_incident = repo.get_by_fingerprint(fingerprint)
        if existing_incident:
            # Increment occurrence count and update last occurrence time
            existing_incident.occurrence_count += 1
            existing_incident.last_occurrence_at = datetime.utcnow()
            existing_incident.updated_at = datetime.utcnow()
            db.commit()
            
            logger.info(f"Duplicate error detected for incident {existing_incident.incident_id}, occurrence count: {existing_incident.occurrence_count}")
            
            return {
                "status": "duplicate",
                "message": f"Similar incident already exists (occurrence #{existing_incident.occurrence_count})",
                "incident_id": existing_incident.incident_id,
                "fingerprint": fingerprint,
                "occurrence_count": existing_incident.occurrence_count
            }
        
        # Use severity from parser if provided, otherwise analyze it
        if incident.severity:
            from storage.models import Severity
            severity = Severity[incident.severity]
            logger.info(f"Using severity from OTLP parser: {severity.value}")
        else:
            from utils.severity_analyzer import SeverityAnalyzer
            severity_analyzer = SeverityAnalyzer()
            severity, confidence = severity_analyzer.analyze_severity(
                error_title=incident.error_title,
                error_description=incident.error_description,
                stack_trace=incident.stack_trace,
                app_name=incident.app_name
            )
        
        # Create new incident
        new_incident = repo.create(
            incident=incident,
            error_fingerprint=fingerprint,
            severity=severity
        )
        
        # Mark as analyzing immediately so the UI does not sit in DETECTED
        # while the background workflow is queued and starting up.
        if settings.auto_fix_enabled:
            repo.update(
                new_incident.incident_id,
                status=IncidentStatus.ANALYZING.value,
                current_workflow_node="assess_severity",
            )
            logger.info(f"Triggering workflow for incident {new_incident.incident_id}")
            background_tasks.add_task(process_incident_workflow, new_incident.incident_id)
        
        return {
            "status": "created",
            "message": "Incident created successfully",
            "incident_id": new_incident.incident_id,
            "severity": severity,
            "fingerprint": fingerprint
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to ingest log: {str(e)}")


# Standard OTLP v1 endpoints

@app.post("/v1/logs")
async def ingest_v1_logs(payload: dict, background_tasks: BackgroundTasks):
    """
    Standard OTLP v1 logs endpoint.
    
    Accepts OTLP/JSON log data according to OpenTelemetry specification.
    This is the standard endpoint that OpenTelemetry SDKs and collectors use.
    
    Expected payload structure:
    {
      "resourceLogs": [{
        "resource": {"attributes": [...]},
        "scopeLogs": [{
          "scope": {"name": "..."},
          "logRecords": [...]
        }]
      }]
    }
    """
    try:
        logger.info("="*80)
        logger.info("RECEIVED OTLP v1 LOGS REQUEST")
        logger.info("="*80)
        logger.info("Full OTLP Payload:")
        logger.info(json.dumps(payload, indent=2))
        logger.info("="*80)
        
        parser = OTLPParser()
        telemetry_repo, telemetry_logs = _persist_telemetry_logs(payload)

        incidents = parser.parse_otlp_json(payload)
        
        if not incidents:
            logger.info(f"No incidents created from OTLP logs. Parser stats: {parser.stats}")
            return {
                "status": "success",
                "message": "OTLP logs processed but no incidents created (all logs below ERROR threshold)",
                "stats": parser.stats
            }
        
        # Get database session
        db = next(get_db())
        repo = IncidentRepository(db)
        
        # Process each incident
        created_incidents = []
        duplicate_incidents = []
        failed_incidents = []
        
        for incident_data in incidents:
            try:
                # Check for duplicates
                fingerprint = deduplicate_error(
                    error_title=incident_data.error_title,
                    stack_trace=incident_data.stack_trace,
                    app_name=incident_data.app_name
                )
                
                # Check if similar incident exists
                existing_incident = repo.get_by_fingerprint(fingerprint)
                if existing_incident:
                    # Increment occurrence count
                    existing_incident.occurrence_count += 1
                    existing_incident.last_occurrence_at = datetime.utcnow()
                    existing_incident.updated_at = datetime.utcnow()
                    db.commit()
                    
                    duplicate_incidents.append({
                        "incident_id": existing_incident.incident_id,
                        "occurrence_count": existing_incident.occurrence_count
                    })
                    
                    logger.info(f"Duplicate OTLP error for incident {existing_incident.incident_id}, occurrence: {existing_incident.occurrence_count}")
                    continue
                
                # Use severity from OTLP parser if provided, otherwise analyze it
                if incident_data.severity:
                    from storage.models import Severity
                    severity_enum = Severity[incident_data.severity]
                    severity = severity_enum.value  # Convert enum to string for database
                    logger.info(f"Using severity from OTLP: {severity} (from severityNumber in OTLP payload)")
                else:
                    severity = analyze_severity(
                        error_title=incident_data.error_title,
                        error_description=incident_data.error_description,
                        stack_trace=incident_data.stack_trace,
                        environment=incident_data.environment
                    )
                    logger.info(f"Analyzed severity (no OTLP severity provided): {severity}")
                
                # Create new incident
                new_incident = repo.create(
                    incident=incident_data,
                    error_fingerprint=fingerprint,
                    severity=severity
                )
                
                created_incidents.append({
                    "incident_id": new_incident.incident_id,
                    "severity": severity,
                    "app_name": new_incident.app_name,
                    "error_title": new_incident.error_title[:100]
                })

                _link_incident_to_telemetry_log(
                    telemetry_repo=telemetry_repo,
                    telemetry_logs=telemetry_logs,
                    incident_data=incident_data,
                    incident_id=new_incident.incident_id,
                )
                
                # Mark as analyzing immediately so status moves off DETECTED
                # before the async workflow finishes bootstrapping.
                if settings.auto_fix_enabled:
                    repo.update(
                        new_incident.incident_id,
                        status=IncidentStatus.ANALYZING.value,
                        current_workflow_node="assess_severity",
                    )
                    logger.info(f"Triggering workflow for OTLP incident {new_incident.incident_id}")
                    background_tasks.add_task(process_incident_workflow, new_incident.incident_id)
                
            except Exception as e:
                logger.error(f"Failed to process OTLP incident: {str(e)}", exc_info=True)
                failed_incidents.append({
                    "error_title": incident_data.error_title[:100],
                    "error": str(e)
                })
        
        # Build response
        response = {
            "status": "success",
            "message": f"OTLP logs processed: {len(created_incidents)} new incidents, {len(duplicate_incidents)} duplicates",
            "stats": {
                "total_log_records": parser.stats["total_parsed"],
                "incidents_created": len(created_incidents),
                "duplicates_found": len(duplicate_incidents),
                "failed": len(failed_incidents),
                "skipped_low_severity": parser.stats["skipped"]
            },
            "created_incidents": created_incidents,
            "duplicate_incidents": duplicate_incidents
        }
        
        if failed_incidents:
            response["failed_incidents"] = failed_incidents
        
        logger.info(f"OTLP v1 logs ingestion complete: {response['stats']}")
        
        return response
        
    except Exception as e:
        logger.error(f"OTLP v1 logs ingestion failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to ingest OTLP logs: {str(e)}")


@app.post("/v1/traces")
async def ingest_v1_traces(payload: dict):
    """
    Standard OTLP v1 traces endpoint.
    
    Accepts OTLP/JSON trace data according to OpenTelemetry specification.
    Currently stores trace data for future correlation with log incidents.
    
    Expected payload structure:
    {
      "resourceSpans": [{
        "resource": {"attributes": [...]},
        "scopeSpans": [{
          "scope": {"name": "..."},
          "spans": [...]
        }]
      }]
    }
    """
    try:
        logger.info("Received OTLP v1 traces request")
        
        # Extract basic statistics
        resource_spans = payload.get("resourceSpans", [])
        total_spans = 0
        error_spans = 0
        
        for resource_span in resource_spans:
            for scope_span in resource_span.get("scopeSpans", []):
                spans = scope_span.get("spans", [])
                total_spans += len(spans)
                
                # Count spans with errors
                for span in spans:
                    status = span.get("status", {})
                    if status.get("code") == 2:  # STATUS_CODE_ERROR
                        error_spans += 1
        
        # TODO: Future implementation
        # - Store traces in database for correlation
        # - Link error spans to incidents via trace_id/span_id
        # - Analyze distributed trace patterns
        # - Detect cascade failures
        
        logger.info(f"Processed {total_spans} spans ({error_spans} errors)")
        
        return {
            "status": "success",
            "message": f"Traces received and acknowledged: {total_spans} spans processed",
            "stats": {
                "total_spans": total_spans,
                "error_spans": error_spans
            }
        }
        
    except Exception as e:
        logger.error(f"OTLP v1 traces ingestion failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to ingest OTLP traces: {str(e)}")


@app.post("/v1/metrics")
async def ingest_v1_metrics(payload: dict):
    """
    Standard OTLP v1 metrics endpoint.
    
    Accepts OTLP/JSON metrics data according to OpenTelemetry specification.
    Currently stores metrics for future analysis and alerting.
    
    Expected payload structure:
    {
      "resourceMetrics": [{
        "resource": {"attributes": [...]},
        "scopeMetrics": [{
          "scope": {"name": "..."},
          "metrics": [...]
        }]
      }]
    }
    """
    try:
        logger.info("Received OTLP v1 metrics request")
        
        # Extract basic statistics
        resource_metrics = payload.get("resourceMetrics", [])
        total_metrics = 0
        metric_types = {"gauge": 0, "sum": 0, "histogram": 0, "summary": 0}
        
        for resource_metric in resource_metrics:
            for scope_metric in resource_metric.get("scopeMetrics", []):
                metrics = scope_metric.get("metrics", [])
                total_metrics += len(metrics)
                
                # Count by metric type
                for metric in metrics:
                    if "gauge" in metric:
                        metric_types["gauge"] += 1
                    elif "sum" in metric:
                        metric_types["sum"] += 1
                    elif "histogram" in metric:
                        metric_types["histogram"] += 1
                    elif "summary" in metric:
                        metric_types["summary"] += 1
        
        # TODO: Future implementation
        # - Store metrics in time-series database
        # - Correlate metrics with incidents (e.g., CPU spike before error)
        # - Set up alerting rules based on metrics
        # - Create dashboards for metrics visualization
        
        logger.info(f"Processed {total_metrics} metrics: {metric_types}")
        
        return {
            "status": "success",
            "message": f"Metrics received and acknowledged: {total_metrics} metrics processed",
            "stats": {
                "total_metrics": total_metrics,
                "by_type": metric_types
            }
        }
        
    except Exception as e:
        logger.error(f"OTLP v1 metrics ingestion failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to ingest OTLP metrics: {str(e)}")


# Legacy OTLP endpoint (for backward compatibility)
@app.post("/ingest/otlp")
async def ingest_otlp(payload: dict, background_tasks: BackgroundTasks):
    """
    Legacy OTLP log ingestion endpoint (for backward compatibility).
    
    This endpoint is maintained for backward compatibility.
    New integrations should use the standard /v1/logs endpoint.
    
    This endpoint parses OTLP format and converts to incidents.
    Supports both OTLP/JSON and processes log records according to
    OpenTelemetry semantic conventions.
    
    Expected payload structure:
    {
      "resourceLogs": [{
        "resource": {"attributes": [...]},
        "scopeLogs": [{
          "scope": {"name": "..."},
          "logRecords": [...]
        }]
      }]
    }
    """
    try:
        logger.info("Received OTLP log ingestion request")
        
        parser = OTLPParser()
        telemetry_repo, telemetry_logs = _persist_telemetry_logs(payload)

        incidents = parser.parse_otlp_json(payload)
        
        if not incidents:
            logger.info(f"No incidents created from OTLP payload. Parser stats: {parser.stats}")
            return {
                "status": "success",
                "message": "OTLP logs processed but no incidents created (all logs below ERROR threshold)",
                "stats": parser.stats
            }
        
        # Get database session
        db = next(get_db())
        repo = IncidentRepository(db)
        
        # Process each incident
        created_incidents = []
        duplicate_incidents = []
        failed_incidents = []
        
        for incident_data in incidents:
            try:
                # Check for duplicates
                fingerprint = deduplicate_error(
                    error_title=incident_data.error_title,
                    stack_trace=incident_data.stack_trace,
                    app_name=incident_data.app_name
                )
                
                # Check if similar incident exists
                existing_incident = repo.get_by_fingerprint(fingerprint)
                if existing_incident:
                    # Increment occurrence count
                    existing_incident.occurrence_count += 1
                    existing_incident.last_occurrence_at = datetime.utcnow()
                    existing_incident.updated_at = datetime.utcnow()
                    db.commit()
                    
                    duplicate_incidents.append({
                        "incident_id": existing_incident.incident_id,
                        "occurrence_count": existing_incident.occurrence_count
                    })
                    
                    logger.info(f"Duplicate OTLP error for incident {existing_incident.incident_id}, occurrence: {existing_incident.occurrence_count}")
                    continue
                
                # Use severity from OTLP parser if provided, otherwise analyze it
                if incident_data.severity:
                    from storage.models import Severity
                    severity_enum = Severity[incident_data.severity]
                    severity = severity_enum.value  # Convert enum to string for database
                    logger.info(f"Using severity from OTLP: {severity} (from severityNumber in OTLP payload)")
                else:
                    severity = analyze_severity(
                        error_title=incident_data.error_title,
                        error_description=incident_data.error_description,
                        stack_trace=incident_data.stack_trace,
                        environment=incident_data.environment
                    )
                    logger.info(f"Analyzed severity (no OTLP severity provided): {severity}")
                
                # Create new incident
                new_incident = repo.create(
                    incident=incident_data,
                    error_fingerprint=fingerprint,
                    severity=severity
                )
                
                created_incidents.append({
                    "incident_id": new_incident.incident_id,
                    "severity": severity,
                    "app_name": new_incident.app_name,
                    "error_title": new_incident.error_title[:100]
                })

                _link_incident_to_telemetry_log(
                    telemetry_repo=telemetry_repo,
                    telemetry_logs=telemetry_logs,
                    incident_data=incident_data,
                    incident_id=new_incident.incident_id,
                )
                
                # Mark as analyzing immediately so status moves off DETECTED
                # before the async workflow finishes bootstrapping.
                if settings.auto_fix_enabled:
                    repo.update(
                        new_incident.incident_id,
                        status=IncidentStatus.ANALYZING.value,
                        current_workflow_node="assess_severity",
                    )
                    logger.info(f"Triggering workflow for OTLP incident {new_incident.incident_id}")
                    background_tasks.add_task(process_incident_workflow, new_incident.incident_id)
                
            except Exception as e:
                logger.error(f"Failed to process OTLP incident: {str(e)}", exc_info=True)
                failed_incidents.append({
                    "error_title": incident_data.error_title[:100],
                    "error": str(e)
                })
        
        # Build response
        response = {
            "status": "success",
            "message": f"OTLP logs processed: {len(created_incidents)} new incidents, {len(duplicate_incidents)} duplicates",
            "stats": {
                "total_log_records": parser.stats["total_parsed"],
                "incidents_created": len(created_incidents),
                "duplicates_found": len(duplicate_incidents),
                "failed": len(failed_incidents),
                "skipped_low_severity": parser.stats["skipped"]
            },
            "created_incidents": created_incidents,
            "duplicate_incidents": duplicate_incidents
        }
        
        if failed_incidents:
            response["failed_incidents"] = failed_incidents
        
        logger.info(f"OTLP ingestion complete: {response['stats']}")
        
        return response
        
    except Exception as e:
        logger.error(f"OTLP ingestion failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to ingest OTLP logs: {str(e)}")


# Get all incidents
@app.get("/incidents", response_model=List[IncidentResponse])
async def get_incidents(
    limit: int = 100,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    app_name: Optional[str] = None
):
    """
    Get all incidents with optional filtering.
    """
    try:
        db = next(get_db())
        repo = IncidentRepository(db)
        
        incidents = repo.get_all(limit=limit)
        
        # Apply filters
        if severity:
            incidents = [i for i in incidents if i.severity == severity]
        if status:
            incidents = [i for i in incidents if i.status == status]
        if app_name:
            incidents = [i for i in incidents if app_name.lower() in i.app_name.lower()]
        
        # Fix None list fields to prevent validation errors
        for incident in incidents:
            if incident.alert_channels is None:
                incident.alert_channels = []
            if incident.processing_errors is None:
                incident.processing_errors = []
            if incident.notification_errors is None:
                incident.notification_errors = []
            if incident.workflow_completed_steps is None:
                incident.workflow_completed_steps = []
        
        return incidents
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get incidents: {str(e)}")


# Get incident by ID
@app.get("/incidents/{incident_id}", response_model=IncidentResponse)
async def get_incident(incident_id: str):
    """
    Get a specific incident by 4-character alphanumeric ID (e.g., A7CB).
    """
    try:
        db = next(get_db())
        repo = IncidentRepository(db)
        
        incident = repo.get_by_id(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
        
        return incident
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get incident: {str(e)}")


# Approve/reject auto-fix
@app.post("/incidents/{incident_id}/approve")
async def approve_fix(incident_id: str, approval: ApprovalRequest, background_tasks: BackgroundTasks):
    """
    Approve or reject an auto-fix for an incident.

    If approved, resumes the post-approval workflow (patch → notifications →
    Jira → PR → finalize) via run_post_approval_workflow().
    If rejected, the incident status is updated and the workflow finalized.

    Prefer the UI server endpoint (POST /api/incidents/{id}/approve followed by
    POST /api/incidents/{id}/continue-post-approval) for browser-initiated
    approvals — it provides richer SSE feedback.

    Args:
        incident_id: 4-character alphanumeric incident ID (e.g., A7CB)
    """
    try:
        db = next(get_db())
        repo = IncidentRepository(db)

        # Get incident
        incident = repo.get_by_id(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

        # Update approval status in database
        approval_status = 'approved' if approval.approved else 'rejected'
        repo.update(
            incident_id,
            approval_status=approval_status,
            approved_by='user',
            approved_at=datetime.utcnow(),
            approval_notes=approval.comment or ''
        )

        if approval.approved:
            logger.info(f"Fix approved for incident {incident_id}, starting post-approval workflow")

            # Run post-approval workflow: patch → notifications → Jira → PR → finalize
            from agents.workflow import run_post_approval_workflow
            background_tasks.add_task(run_post_approval_workflow, incident_id)

            return {
                "status": "approved",
                "message": "Fix approved. Post-approval workflow started (patch → notifications → Jira → PR → finalize).",
                "incident_id": incident_id
            }
        else:
            logger.info(f"Fix rejected for incident {incident_id}")

            # Finalize with rejection status
            from agents.state import create_initial_state
            from agents.nodes.finalizer import finalize_node

            state = create_initial_state(
                incident_id=str(incident_id),
                app_name=incident.app_name,
                environment=incident.environment,
                error_title=incident.error_title,
                error_description=incident.error_description or "",
                stack_trace=incident.stack_trace or "",
                raw_log=incident.raw_log or "",
                fingerprint=incident.error_fingerprint or "",
                severity=incident.severity or "MEDIUM",
                is_duplicate=False,
                created_at=incident.created_at.isoformat() if incident.created_at else datetime.utcnow().isoformat()
            )
            state['approval_status'] = 'rejected'
            state['approved_by'] = 'user'
            state['approved_at'] = datetime.utcnow().isoformat()
            state['approval_notes'] = approval.comment or ''

            finalize_node(state)

            return {
                "status": "rejected",
                "message": "Fix rejected. Workflow finalized.",
                "incident_id": incident_id
            }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process approval: {str(e)}")


# Update incident (for agent to use)
@app.patch("/incidents/{incident_id}")
async def update_incident(incident_id: str, updates: dict):
    """
    Update incident fields (used by agent workflow).
    
    Args:
        incident_id: 4-character alphanumeric incident ID (e.g., A7CB)
    """
    try:
        db = next(get_db())
        repo = IncidentRepository(db)
        
        incident = repo.get_by_id(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
        
        # Update allowed fields
        allowed_fields = [
            'status', 'rca_text', 'rca_confidence', 'pdf_path',
            'jira_ticket_key', 'jira_ticket_url', 'alert_channels',
            'proposed_fix', 'fix_explanation', 'patch_path',
            'fix_quality_score', 'fix_branch', 'pr_number', 'pr_url'
        ]
        
        update_data = {k: v for k, v in updates.items() if k in allowed_fields}
        
        # Perform update
        for field, value in update_data.items():
            setattr(incident, field, value)
        
        db.commit()
        db.refresh(incident)
        
        return {
            "status": "updated",
            "incident_id": incident_id,
            "updated_fields": list(update_data.keys())
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update incident: {str(e)}")


# Get incident statistics
@app.get("/stats")
async def get_statistics():
    """
    Get statistics about incidents.
    """
    try:
        db = next(get_db())
        repo = IncidentRepository(db)
        
        all_incidents = repo.get_all(limit=10000)
        
        stats = {
            "total_incidents": len(all_incidents),
            "by_severity": {
                "CRITICAL": len([i for i in all_incidents if i.severity == "CRITICAL"]),
                "HIGH": len([i for i in all_incidents if i.severity == "HIGH"]),
                "MEDIUM": len([i for i in all_incidents if i.severity == "MEDIUM"]),
                "LOW": len([i for i in all_incidents if i.severity == "LOW"]),
            },
            "by_status": {},
            "pending_approval": len([i for i in all_incidents if i.status == "PENDING_APPROVAL"]),
            "prs_created": len([i for i in all_incidents if i.pr_url is not None]),
            "with_rca": len([i for i in all_incidents if i.rca_text is not None]),
        }
        
        # Count by status
        from collections import Counter
        status_counts = Counter([i.status for i in all_incidents])
        stats["by_status"] = dict(status_counts)
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get statistics: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host=settings.ingestion_api_host,
        port=settings.ingestion_api_port,
        reload=True
    )
