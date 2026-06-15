# Tools Directory

This directory contains utility scripts for managing, testing, and debugging the Prism incident management platform.

## Directory Structure

### `checks/`
Verification scripts for specific features and integrations.

- `check_github_files.py` — Verify GitHub file fetching for a given repo and path
- `check_github_integration.py` — Test GitHub API connectivity and token permissions
- `check_incident_details.py` — Inspect all fields of a specific incident record
- `check_incident_status.py` — Check the current status of a specific incident
- `check_incidents_status.py` — Bulk-check status of multiple or all incidents
- `check_metadata.py` — Verify OTLP metadata and custom attributes on an incident
- `check_patch_quality.py` — Inspect and validate a generated patch file
- `check_pdf.py` — Verify PDF report generation for an incident
- `check_severity.py` — Check severity assessment logic for sample inputs
- `check_workflow_completion.py` — Verify that a workflow reached a completed terminal state
- `check_workflow_errors.py` — Inspect workflow errors and failure reasons
- `check_workflow_status.py` — Check the current workflow node and progress percentage
- `check_workflow_steps.py` — List all completed workflow steps for an incident

### `debug/`
Diagnostic and inspection scripts for troubleshooting.

- `check_severity_in_db.py` — Inspect raw severity values stored in the database
- `debug_code_fetch.py` — Debug GitHub code fetching for a specific repo + file
- `debug_github.py` — Debug GitHub API calls (auth, rate limits, repo access)
- `debug_incidents.py` — Inspect incident data including raw log and metadata fields
- `debug_rca_github.py` — Debug RCA GitHub metadata extraction from log payloads
- `debug_workflow.py` — Step-through debug of workflow execution and state transitions
- `view_rca.py` — View generated RCA content for an incident from the database
- `wait_and_check.py` — Poll an incident until it reaches a target status or times out

### `tests/`
Integration and workflow tests.

- `test_alphanumeric_ids.py` — Test 4-character alphanumeric incident ID generation
- `test_code_fetch_workflow.py` — End-to-end test of GitHub code fetching within a workflow
- `test_complete_workflow.py` — Full end-to-end workflow test from ingestion to finalization
- `test_db.py` — Test database operations (create, read, update, delete on incident records)
- `test_full_workflow.py` — Full workflow integration test including post-approval steps
- `test_github_metadata_extraction.py` — Test extraction of GitHub metadata from OTLP log payloads
- `test_github_repo_resolution.py` — Test project-to-repo resolution logic for various app names
- `test_integrations.py` — Test external integration clients (LLM, Jira, GitHub connectivity)
- `test_metadata_extraction.py` — Test OTLP attribute and custom metadata extraction
- `test_order_processing_service.py` — Test incident workflow against order processing service logs
- `test_otlp_integration.py` — End-to-end test of OTLP log ingestion via `/v1/logs`
- `test_patch_format_validation.py` — Validate generated `.patch` file format and applicability
- `test_project_resolution.py` — Test `_resolve_project_id_for_incident()` matching logic
- `test_reflector_persists_errors.py` — Test that the reflector node persists errors to the database
- `test_repo_extraction.py` — Test repository extraction from log content and metadata

### `operations/`
Workflow management and fix utilities.

- `complete_approved_workflow.py` — Trigger the post-approval workflow for already-approved incidents (patch → notifications → Jira → PR → finalize)
- `fix_all_incidents_for_approval.py` — Batch utility to advance multiple incidents to PENDING_APPROVAL state
- `fix_approval_status.py` — Correct stale or inconsistent approval status values in the database
- `fix_status.py` — General-purpose status correction utility for stuck incidents
- `force_pending_approval.py` — Force an incident into PENDING_APPROVAL state for testing the approval UI

## Usage

All scripts must be run from the **project root** directory so that imports resolve correctly:

```bash
# Verify GitHub integration for a project
python tools/checks/check_github_integration.py

# Inspect a specific incident
python tools/checks/check_incident_details.py <INCIDENT_ID>

# Debug why workflow stalled
python tools/debug/debug_workflow.py <INCIDENT_ID>

# View the generated RCA
python tools/debug/view_rca.py <INCIDENT_ID>

# Poll until an incident completes
python tools/debug/wait_and_check.py <INCIDENT_ID>

# Run a complete end-to-end workflow test
python tools/tests/test_complete_workflow.py

# Test OTLP ingestion
python tools/tests/test_otlp_integration.py

# Complete post-approval steps for an approved incident
python tools/operations/complete_approved_workflow.py <INCIDENT_ID>

# Force an incident to PENDING_APPROVAL for UI testing
python tools/operations/force_pending_approval.py <INCIDENT_ID>
```

## Notes

- All scripts expect to be run from the **project root** (i.e., `python tools/...` not `cd tools && python ...`)
- Scripts that write to the database should be used with caution in production environments
- Integration test scripts (`tools/tests/`) require a running ingestion API (`:8000`) and configured project integrations
- Check individual script headers for specific arguments and usage details
- Use `tools/debug/wait_and_check.py` to monitor long-running workflows without blocking your terminal
