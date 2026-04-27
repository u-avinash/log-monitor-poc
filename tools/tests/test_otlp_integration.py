"""
Test script for OTLP (OpenTelemetry Protocol) integration.

This script demonstrates how to send OTLP-formatted telemetry (logs, traces, metrics)
to the standard v1 endpoints.
"""
import requests
import json
import time
from datetime import datetime


def create_otlp_logs_payload():
    """
    Create a sample OTLP logs payload following OpenTelemetry specification.
    """
    time_unix_nano = int(time.time() * 1_000_000_000)
    
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "payment-api"}},
                        {"key": "deployment.environment", "value": {"stringValue": "production"}},
                        {"key": "cloudhub.application.name", "value": {"stringValue": "payment-processing-app"}},
                        {"key": "cloudhub.org.id", "value": {"stringValue": "org-12345"}},
                        {"key": "cloudhub.region", "value": {"stringValue": "us-east-1"}},
                        {"key": "host.name", "value": {"stringValue": "worker-node-01"}}
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "mule.runtime", "version": "4.4.0"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(time_unix_nano),
                                "observedTimeUnixNano": str(time_unix_nano),
                                "severityNumber": 17,  # ERROR
                                "severityText": "ERROR",
                                "body": {"stringValue": "Payment processing failed: Database connection timeout"},
                                "attributes": [
                                    {"key": "exception.type", "value": {"stringValue": "java.sql.SQLException"}},
                                    {"key": "exception.message", "value": {"stringValue": "Connection timeout after 30 seconds"}},
                                    {"key": "exception.stacktrace", "value": {
                                        "stringValue": """java.sql.SQLException: Connection timeout after 30 seconds
    at com.mulesoft.modules.db.internal.DbConnection.getConnection(DbConnection.java:142)
    at com.mulesoft.modules.db.internal.operations.DbOperation.execute(DbOperation.java:89)"""
                                    }},
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.url", "value": {"stringValue": "/api/v1/payments/process"}},
                                    {"key": "http.status_code", "value": {"intValue": "500"}},
                                    {"key": "transaction.id", "value": {"stringValue": "txn-789012"}}
                                ],
                                "traceId": "5b8efff798038103d269b633813fc60c",
                                "spanId": "eee19b7ec3c1b174",
                                "flags": 1
                            }
                        ]
                    }
                ]
            }
        ]
    }
    return payload


def create_otlp_traces_payload():
    """
    Create a sample OTLP traces payload following OpenTelemetry specification.
    """
    time_unix_nano = int(time.time() * 1_000_000_000)
    
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "payment-api"}},
                        {"key": "deployment.environment", "value": {"stringValue": "production"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "payment-tracer", "version": "1.0.0"},
                        "spans": [
                            {
                                "traceId": "5b8efff798038103d269b633813fc60c",
                                "spanId": "eee19b7ec3c1b174",
                                "parentSpanId": "",
                                "name": "POST /api/v1/payments/process",
                                "kind": 2,  # SPAN_KIND_SERVER
                                "startTimeUnixNano": str(time_unix_nano - 100_000_000),
                                "endTimeUnixNano": str(time_unix_nano),
                                "attributes": [
                                    {"key": "http.method", "value": {"stringValue": "POST"}},
                                    {"key": "http.url", "value": {"stringValue": "/api/v1/payments/process"}},
                                    {"key": "http.status_code", "value": {"intValue": "500"}}
                                ],
                                "status": {
                                    "code": 2,  # STATUS_CODE_ERROR
                                    "message": "Internal server error"
                                }
                            },
                            {
                                "traceId": "5b8efff798038103d269b633813fc60c",
                                "spanId": "fff29b7ec3c1b285",
                                "parentSpanId": "eee19b7ec3c1b174",
                                "name": "db.query",
                                "kind": 3,  # SPAN_KIND_CLIENT
                                "startTimeUnixNano": str(time_unix_nano - 80_000_000),
                                "endTimeUnixNano": str(time_unix_nano - 50_000_000),
                                "attributes": [
                                    {"key": "db.system", "value": {"stringValue": "postgresql"}},
                                    {"key": "db.statement", "value": {"stringValue": "SELECT * FROM payments WHERE id = ?"}}
                                ],
                                "status": {
                                    "code": 1  # STATUS_CODE_OK
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    return payload


def create_otlp_metrics_payload():
    """
    Create a sample OTLP metrics payload following OpenTelemetry specification.
    """
    time_unix_nano = int(time.time() * 1_000_000_000)
    
    payload = {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "payment-api"}},
                        {"key": "deployment.environment", "value": {"stringValue": "production"}}
                    ]
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": "payment-metrics", "version": "1.0.0"},
                        "metrics": [
                            {
                                "name": "http.server.request.duration",
                                "description": "HTTP request duration",
                                "unit": "ms",
                                "histogram": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": str(time_unix_nano),
                                            "count": "100",
                                            "sum": 15000.0,
                                            "bucketCounts": ["10", "25", "35", "20", "10"],
                                            "explicitBounds": [50, 100, 200, 500, 1000],
                                            "attributes": [
                                                {"key": "http.method", "value": {"stringValue": "POST"}},
                                                {"key": "http.route", "value": {"stringValue": "/api/v1/payments/process"}}
                                            ]
                                        }
                                    ]
                                }
                            },
                            {
                                "name": "jvm.memory.heap.used",
                                "description": "JVM heap memory used",
                                "unit": "bytes",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": str(time_unix_nano),
                                            "asInt": "1610612736",
                                            "attributes": []
                                        }
                                    ]
                                }
                            },
                            {
                                "name": "http.server.requests",
                                "description": "Total HTTP requests",
                                "unit": "1",
                                "sum": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": str(time_unix_nano),
                                            "asInt": "1523",
                                            "attributes": [
                                                {"key": "http.status_code", "value": {"stringValue": "200"}}
                                            ]
                                        }
                                    ],
                                    "aggregationTemporality": 2,  # CUMULATIVE
                                    "isMonotonic": True
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    return payload


def test_v1_logs(api_url="http://localhost:8000"):
    """Test OTLP v1 logs endpoint."""
    print("=" * 80)
    print("Testing /v1/logs Endpoint")
    print("=" * 80)
    
    payload = create_otlp_logs_payload()
    print(f"\n1. Sending logs to {api_url}/v1/logs")
    
    try:
        response = requests.post(
            f"{api_url}/v1/logs",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        print(f"   ✓ Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n2. Results:")
            print(f"   - Status: {result['status']}")
            print(f"   - Message: {result['message']}")
            
            if 'stats' in result:
                print(f"\n   Statistics:")
                for key, value in result['stats'].items():
                    print(f"   - {key}: {value}")
            
            if 'created_incidents' in result and result['created_incidents']:
                print(f"\n   Created Incidents:")
                for incident in result['created_incidents']:
                    print(f"   - ID: {incident['incident_id']} | Severity: {incident['severity']}")
            
            print("\n   ✓ /v1/logs test passed!")
            return True
        else:
            print(f"\n   ✗ Error: {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"   ✗ Error: Could not connect to {api_url}")
        print(f"   Make sure the ingestion API is running: python ingestion/api.py")
        return False
    except Exception as e:
        print(f"   ✗ Error: {str(e)}")
        return False


def test_v1_traces(api_url="http://localhost:8000"):
    """Test OTLP v1 traces endpoint."""
    print("\n" + "=" * 80)
    print("Testing /v1/traces Endpoint")
    print("=" * 80)
    
    payload = create_otlp_traces_payload()
    print(f"\n1. Sending traces to {api_url}/v1/traces")
    
    try:
        response = requests.post(
            f"{api_url}/v1/traces",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        print(f"   ✓ Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n2. Results:")
            print(f"   - Status: {result['status']}")
            print(f"   - Message: {result['message']}")
            
            if 'stats' in result:
                print(f"\n   Statistics:")
                for key, value in result['stats'].items():
                    print(f"   - {key}: {value}")
            
            print("\n   ✓ /v1/traces test passed!")
            return True
        else:
            print(f"\n   ✗ Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"   ✗ Error: {str(e)}")
        return False


def test_v1_metrics(api_url="http://localhost:8000"):
    """Test OTLP v1 metrics endpoint."""
    print("\n" + "=" * 80)
    print("Testing /v1/metrics Endpoint")
    print("=" * 80)
    
    payload = create_otlp_metrics_payload()
    print(f"\n1. Sending metrics to {api_url}/v1/metrics")
    
    try:
        response = requests.post(
            f"{api_url}/v1/metrics",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        print(f"   ✓ Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n2. Results:")
            print(f"   - Status: {result['status']}")
            print(f"   - Message: {result['message']}")
            
            if 'stats' in result:
                print(f"\n   Statistics:")
                for key, value in result['stats'].items():
                    if key == 'by_type':
                        print(f"   - {key}:")
                        for metric_type, count in value.items():
                            print(f"     * {metric_type}: {count}")
                    else:
                        print(f"   - {key}: {value}")
            
            print("\n   ✓ /v1/metrics test passed!")
            return True
        else:
            print(f"\n   ✗ Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"   ✗ Error: {str(e)}")
        return False


def test_legacy_endpoint(api_url="http://localhost:8000"):
    """Test legacy /ingest/otlp endpoint for backward compatibility."""
    print("\n" + "=" * 80)
    print("Testing Legacy /ingest/otlp Endpoint")
    print("=" * 80)
    
    payload = create_otlp_logs_payload()
    print(f"\n1. Sending logs to {api_url}/ingest/otlp")
    
    try:
        response = requests.post(
            f"{api_url}/ingest/otlp",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        print(f"   ✓ Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"   ✓ Legacy endpoint still works: {result['message']}")
            return True
        else:
            print(f"   ✗ Error: {response.text}")
            return False
            
    except Exception as e:
        print(f"   ✗ Error: {str(e)}")
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test OTLP v1 endpoints")
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Base URL of the ingestion API (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--endpoint",
        choices=["logs", "traces", "metrics", "legacy", "all"],
        default="all",
        help="Which endpoint to test (default: all)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 80)
    print("OTLP v1 Endpoints Integration Test")
    print("=" * 80)
    print(f"API URL: {args.api_url}")
    print(f"Testing: {args.endpoint}")
    print("=" * 80)
    
    results = {}
    
    # Run tests based on selection
    if args.endpoint in ["logs", "all"]:
        results["logs"] = test_v1_logs(args.api_url)
    
    if args.endpoint in ["traces", "all"]:
        results["traces"] = test_v1_traces(args.api_url)
    
    if args.endpoint in ["metrics", "all"]:
        results["metrics"] = test_v1_metrics(args.api_url)
    
    if args.endpoint in ["legacy", "all"]:
        results["legacy"] = test_legacy_endpoint(args.api_url)
    
    # Summary
    print("\n" + "=" * 80)
    print("Test Summary")
    print("=" * 80)
    
    passed = sum(1 for result in results.values() if result)
    total = len(results)
    
    for endpoint, result in results.items():
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"  {endpoint.upper():<15} {status}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    print("=" * 80 + "\n")
    
    exit(0 if passed == total else 1)
