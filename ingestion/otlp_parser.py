"""OTLP (OpenTelemetry Protocol) parser for log ingestion."""
from typing import Dict, List, Optional, Any
from datetime import datetime
from storage.models import IncidentCreate, TelemetryLogCreate
import json
import logging

logger = logging.getLogger(__name__)


class OTLPParser:
    """Parser for OTLP log payloads (JSON and Protobuf)."""
    
    # Severity mapping (OTLP severity numbers to string)
    SEVERITY_MAP = {
        0: "UNSPECIFIED",
        1: "TRACE",
        2: "TRACE2",
        3: "TRACE3",
        4: "TRACE4",
        5: "DEBUG",
        6: "DEBUG2",
        7: "DEBUG3",
        8: "DEBUG4",
        9: "INFO",
        10: "INFO2",
        11: "INFO3",
        12: "INFO4",
        13: "WARN",
        14: "WARN2",
        15: "WARN3",
        16: "WARN4",
        17: "ERROR",
        18: "ERROR2",
        19: "ERROR3",
        20: "ERROR4",
        21: "FATAL",
        22: "FATAL2",
        23: "FATAL3",
        24: "FATAL4",
    }
    
    def __init__(self):
        """Initialize parser."""
        self.stats = {
            "total_parsed": 0,
            "errors": 0,
            "skipped": 0,
            "incidents_created": 0
        }
    
    def parse_otlp_json(self, payload: Dict[str, Any]) -> List[IncidentCreate]:
        """
        Parse OTLP JSON format.
        
        Expected structure:
        {
          "resourceLogs": [{
            "resource": {"attributes": [...]},
            "scopeLogs": [{
              "scope": {"name": "..."},
              "logRecords": [...]
            }]
          }]
        }
        
        Args:
            payload: OTLP JSON payload
            
        Returns:
            List of IncidentCreate objects
        """
        incidents = []
        
        try:
            resource_logs = payload.get("resourceLogs", [])
            
            for resource_log in resource_logs:
                # Extract resource attributes (service-level metadata)
                resource_attrs = self._extract_attributes(
                    resource_log.get("resource", {}).get("attributes", [])
                )
                
                # Process scope logs (instrumentation scopes)
                scope_logs = resource_log.get("scopeLogs", [])
                
                for scope_log in scope_logs:
                    # Extract scope information
                    scope = scope_log.get("scope", {})
                    scope_name = scope.get("name", "unknown")
                    
                    # Process individual log records
                    log_records = scope_log.get("logRecords", [])
                    
                    for log_record in log_records:
                        incident = self._parse_log_record(
                            log_record, 
                            resource_attrs,
                            scope_name
                        )
                        if incident:
                            incidents.append(incident)
                            self.stats["incidents_created"] += 1
                        else:
                            self.stats["skipped"] += 1
                        
                        self.stats["total_parsed"] += 1
            
            logger.info(f"OTLP parsing complete: {len(incidents)} incidents created from {self.stats['total_parsed']} log records")
            return incidents
            
        except Exception as e:
            logger.error(f"OTLP parsing error: {str(e)}", exc_info=True)
            self.stats["errors"] += 1
            return []
    
    def parse_otlp_json_to_logs(self, payload: Dict[str, Any]) -> List[TelemetryLogCreate]:
        """
        Parse OTLP JSON format into telemetry log records.

        Args:
            payload: OTLP JSON payload

        Returns:
            List of TelemetryLogCreate objects
        """
        telemetry_logs = []

        try:
            resource_logs = payload.get("resourceLogs", [])

            for resource_log in resource_logs:
                resource_attrs = self._extract_attributes(
                    resource_log.get("resource", {}).get("attributes", [])
                )

                scope_logs = resource_log.get("scopeLogs", [])

                for scope_log in scope_logs:
                    scope = scope_log.get("scope", {})
                    scope_name = scope.get("name", "unknown")

                    log_records = scope_log.get("logRecords", [])

                    for log_record in log_records:
                        telemetry_log = self._parse_telemetry_log_record(
                            log_record,
                            resource_attrs,
                            scope_name
                        )
                        if telemetry_log:
                            telemetry_logs.append(telemetry_log)

            return telemetry_logs

        except Exception as e:
            logger.error(f"OTLP telemetry log parsing error: {str(e)}", exc_info=True)
            return []

    def _parse_log_record(
        self,
        log_record: Dict[str, Any],
        resource_attrs: Dict[str, Any],
        scope_name: str
    ) -> Optional[IncidentCreate]:
        """
        Parse individual log record into incident.
        
        Args:
            log_record: Individual log record from OTLP
            resource_attrs: Resource-level attributes
            scope_name: Instrumentation scope name
            
        Returns:
            IncidentCreate object or None if log should be skipped
        """
        try:
            # Extract log-level attributes
            log_attrs = self._extract_attributes(log_record.get("attributes", []))
            
            # Merge resource and log attributes (log attrs take precedence)
            all_attrs = {**resource_attrs, **log_attrs}
            
            # Log all received attributes for debugging
            logger.info(f"[OTLP Parser] Received {len(all_attrs)} attributes: {list(all_attrs.keys())}")
            if all_attrs:
                logger.debug(f"[OTLP Parser] Full attributes: {json.dumps(all_attrs, indent=2, default=str)}")
            
            telemetry_log = self._parse_telemetry_log_record(
                log_record,
                resource_attrs,
                scope_name
            )
            if not telemetry_log:
                return None

            severity_number = telemetry_log.severity_number or 0
            severity = telemetry_log.severity
            
            # Only process ERROR and above (severity_number >= 17)
            if severity_number < 17:
                logger.debug(f"Skipping log with severity {severity} (number: {severity_number})")
                return None
            
            exception_message = telemetry_log.error_message or telemetry_log.message
            exception_type = telemetry_log.error_type or "UnknownException"
            stack_trace = telemetry_log.attributes.get("exception.stacktrace", "")
            cloudhub_app = telemetry_log.attributes.get("cloudhub.application.name", "")
            cloudhub_org = telemetry_log.attributes.get("cloudhub.org.id", telemetry_log.attributes.get("organization.id", ""))
            cloudhub_region = telemetry_log.attributes.get("cloudhub.region", "")
            trace_flags = log_record.get("flags", 0)

            if exception_type and exception_message:
                error_title = f"{exception_type}: {exception_message[:100]}"
            else:
                error_title = telemetry_log.message[:150] if telemetry_log.message else "Unknown Error"

            error_description = self._build_error_description(
                exception_message,
                telemetry_log.attributes,
                cloudhub_app,
                cloudhub_org,
                cloudhub_region
            )

            raw_log = telemetry_log.raw_payload
            
            # Build comprehensive metadata including ALL custom attributes
            metadata = {
                # OTLP-specific fields
                "otlp_trace_id": telemetry_log.trace_id,
                "otlp_span_id": telemetry_log.span_id,
                "otlp_severity_number": severity_number,
                "otlp_scope": scope_name,
                "exception_type": exception_type,
                
                # CloudHub-specific fields
                "cloudhub_app": cloudhub_app,
                "cloudhub_org": cloudhub_org,
                "cloudhub_region": cloudhub_region,
                
                # Store ALL custom attributes from OTLP (prefixed to avoid conflicts)
                "custom_attributes": telemetry_log.attributes
            }
            
            logger.info(f"[OTLP Parser] Incident metadata includes {len(all_attrs)} custom attributes")
            
            # Create incident
            incident = IncidentCreate(
                app_name=telemetry_log.app_name,
                environment=telemetry_log.environment,
                error_title=error_title,
                error_description=error_description,
                stack_trace=stack_trace or self._extract_stack_from_body(telemetry_log.message),
                raw_log=raw_log,
                severity=severity,
                metadata=metadata
            )
            
            logger.debug(f"Created incident from OTLP: {error_title[:50]}...")
            return incident
            
        except Exception as e:
            logger.error(f"Error parsing log record: {str(e)}", exc_info=True)
            return None
    
    def _parse_telemetry_log_record(
        self,
        log_record: Dict[str, Any],
        resource_attrs: Dict[str, Any],
        scope_name: str
    ) -> Optional[TelemetryLogCreate]:
        """Parse individual OTLP log record into telemetry log model."""
        try:
            log_attrs = self._extract_attributes(log_record.get("attributes", []))
            all_attrs = {**resource_attrs, **log_attrs}

            severity_number = log_record.get("severityNumber", 0)
            severity_text = log_record.get("severityText", "")
            severity = self._map_severity(severity_number, severity_text)

            body = log_record.get("body", {})
            message = self._extract_body_value(body)
            error_message = all_attrs.get("exception.message", message)
            error_type = all_attrs.get("exception.type")

            cloudhub_app = all_attrs.get(
                "cloudhub.application.name",
                all_attrs.get(
                    "application.name",
                    all_attrs.get("app.name", all_attrs.get("mule.application.name", "")),
                ),
            )
            environment = (
                all_attrs.get("deployment.environment")
                or all_attrs.get("environment")
                or all_attrs.get("env")
                or all_attrs.get("cloudhub.environment")
                or all_attrs.get("mule.environment")
                or "unknown"
            )
            app_name = cloudhub_app or all_attrs.get("service.name", "unknown-service")
            deployment_type = (
                all_attrs.get("mule.deployment.type")
                or all_attrs.get("deployment.type")
                or all_attrs.get("cloud.platform")
                or all_attrs.get("cloud.provider")
            )

            trace_id = log_record.get("traceId", "")
            span_id = log_record.get("spanId", "")
            if trace_id and isinstance(trace_id, str):
                trace_id = self._format_trace_id(trace_id)

            time_unix_nano = log_record.get("timeUnixNano", 0)
            observed_time_unix_nano = log_record.get("observedTimeUnixNano", 0)

            timestamp = self._parse_unix_nano_timestamp(time_unix_nano)
            observed_timestamp = self._parse_unix_nano_timestamp(observed_time_unix_nano)

            return TelemetryLogCreate(
                app_name=app_name,
                environment=environment,
                message=message,
                severity=severity,
                timestamp=timestamp,
                observed_timestamp=observed_timestamp,
                deployment_type=deployment_type,
                severity_number=severity_number or None,
                error_type=error_type,
                error_message=error_message,
                trace_id=trace_id or None,
                span_id=span_id or None,
                flow_name=all_attrs.get("mule.flow.name") or all_attrs.get("flow.name"),
                logger_name=all_attrs.get("logger.name"),
                service_name=all_attrs.get("service.name"),
                source_scope=scope_name,
                raw_payload=json.dumps(
                    {
                        "otlp_version": "1.0",
                        "message": message,
                        "severity": severity,
                        "severity_number": severity_number,
                        "attributes": all_attrs,
                        "scope": scope_name,
                        "timestamp_unix_nano": time_unix_nano,
                        "observed_timestamp_unix_nano": observed_time_unix_nano,
                        "trace_id": trace_id,
                        "span_id": span_id,
                    },
                    indent=2,
                ),
                attributes=all_attrs,
            )
        except Exception as e:
            logger.error(f"Error parsing telemetry log record: {str(e)}", exc_info=True)
            return None

    def _extract_attributes(self, attributes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Extract attributes from OTLP attribute array.
        
        OTLP attributes format:
        [{"key": "service.name", "value": {"stringValue": "my-service"}}, ...]
        
        Args:
            attributes: List of OTLP attribute objects
            
        Returns:
            Dictionary of key-value pairs
        """
        result = {}
        
        for attr in attributes:
            key = attr.get("key", "")
            value_obj = attr.get("value", {})
            
            # Extract value based on type
            if "stringValue" in value_obj:
                result[key] = value_obj["stringValue"]
            elif "intValue" in value_obj:
                result[key] = int(value_obj["intValue"])
            elif "doubleValue" in value_obj:
                result[key] = float(value_obj["doubleValue"])
            elif "boolValue" in value_obj:
                result[key] = bool(value_obj["boolValue"])
            elif "arrayValue" in value_obj:
                result[key] = self._extract_array_value(value_obj["arrayValue"])
            elif "kvlistValue" in value_obj:
                result[key] = self._extract_attributes(value_obj["kvlistValue"].get("values", []))
            else:
                result[key] = str(value_obj)
        
        return result
    
    def _extract_array_value(self, array_obj: Dict[str, Any]) -> List[Any]:
        """Extract array value from OTLP array object."""
        values = array_obj.get("values", [])
        result = []
        
        for value_obj in values:
            if "stringValue" in value_obj:
                result.append(value_obj["stringValue"])
            elif "intValue" in value_obj:
                result.append(int(value_obj["intValue"]))
            elif "doubleValue" in value_obj:
                result.append(float(value_obj["doubleValue"]))
            elif "boolValue" in value_obj:
                result.append(bool(value_obj["boolValue"]))
            else:
                result.append(str(value_obj))
        
        return result
    
    def _extract_body_value(self, body: Any) -> str:
        """Extract string value from OTLP body."""
        if isinstance(body, dict):
            if "stringValue" in body:
                return body["stringValue"]
            elif "intValue" in body:
                return str(body["intValue"])
            elif "doubleValue" in body:
                return str(body["doubleValue"])
            elif "boolValue" in body:
                return str(body["boolValue"])
            else:
                return json.dumps(body)
        return str(body)
    
    def _map_severity(self, severity_number: int, severity_text: str = "") -> str:
        """
        Map OTLP severity to application severity level.
        
        Args:
            severity_number: OTLP severity number (0-24)
            severity_text: Optional severity text
            
        Returns:
            Severity string: CRITICAL, HIGH, MEDIUM, LOW
        """
        # Prioritize severity_number over severity_text for accurate mapping
        # Use number-based mapping first (most reliable)
        if severity_number >= 21:  # FATAL/FATAL2/FATAL3/FATAL4
            return "CRITICAL"
        elif severity_number >= 17:  # ERROR/ERROR2/ERROR3/ERROR4
            return "HIGH"
        elif severity_number >= 13:  # WARN/WARN2/WARN3/WARN4
            return "MEDIUM"
        elif severity_number >= 9:  # INFO and above
            return "LOW"
        
        # Fall back to text-based mapping only if number is not set
        if severity_text and severity_number == 0:
            text_upper = severity_text.upper()
            if "FATAL" in text_upper or "CRITICAL" in text_upper:
                return "CRITICAL"
            elif "ERROR" in text_upper or "HIGH" in text_upper:
                return "HIGH"
            elif "WARN" in text_upper or "MEDIUM" in text_upper:
                return "MEDIUM"
            else:
                return "LOW"
        
        return "LOW"
    
    def _format_trace_id(self, trace_id: str) -> str:
        """Format trace ID for display."""
        # If already hex string, return as-is
        if len(trace_id) == 32:
            return trace_id
        # Otherwise try to decode base64
        try:
            import base64
            decoded = base64.b64decode(trace_id)
            return decoded.hex()
        except:
            return trace_id
    
    def _build_error_description(
        self,
        exception_message: str,
        attributes: Dict[str, Any],
        cloudhub_app: str,
        cloudhub_org: str,
        cloudhub_region: str
    ) -> str:
        """Build comprehensive error description."""
        parts = [exception_message]
        
        if cloudhub_app:
            parts.append(f"\nCloudHub Application: {cloudhub_app}")
        if cloudhub_org:
            parts.append(f"Organization: {cloudhub_org}")
        if cloudhub_region:
            parts.append(f"Region: {cloudhub_region}")
        
        # Add relevant HTTP attributes if present
        if "http.status_code" in attributes:
            parts.append(f"\nHTTP Status: {attributes['http.status_code']}")
        if "http.method" in attributes:
            parts.append(f"HTTP Method: {attributes['http.method']}")
        if "http.url" in attributes:
            parts.append(f"URL: {attributes['http.url']}")
        
        return "\n".join(parts)
    
    def _parse_unix_nano_timestamp(self, value: Any) -> datetime:
        """Convert OTLP unix nano timestamps to datetime."""
        try:
            if not value:
                return datetime.utcnow()
            return datetime.fromtimestamp(int(value) / 1_000_000_000)
        except Exception:
            return datetime.utcnow()

    def _extract_stack_from_body(self, message: str) -> str:
        """Try to extract stack trace from message body."""
        # Look for common stack trace patterns
        if "at " in message and ("java" in message.lower() or "mule" in message.lower()):
            # Java/MuleSoft stack trace
            lines = message.split("\n")
            stack_lines = [line for line in lines if line.strip().startswith("at ")]
            if stack_lines:
                return "\n".join(stack_lines[:20])  # First 20 lines
        
        return ""
