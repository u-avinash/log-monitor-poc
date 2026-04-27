# Tools Directory

This directory contains utility scripts for managing, testing, and debugging the log monitoring system.

## Directory Structure

### `checks/`
Verification scripts for specific features and integrations.

- `check_github_files.py` - Verify GitHub file fetching
- `check_github_integration.py` - Test GitHub integration
- `check_incident_details.py` - Inspect incident details
- `check_incident_status.py` - Check incident status
- `check_metadata.py` - Verify metadata fields
- `check_pdf.py` - Test PDF generation
- `check_severity.py` - Check severity assessment
- `check_workflow_completion.py` - Verify workflow completion
- `check_workflow_errors.py` - Check for workflow errors
- `check_workflow_status.py` - Check workflow status
- `check_workflow_steps.py` - Verify workflow steps

### `debug/`
Diagnostic and inspection scripts for troubleshooting.

- `debug_code_fetch.py` - Debug code fetching from GitHub
- `debug_github.py` - Debug GitHub API calls
- `debug_incidents.py` - Inspect incident data
- `debug_rca_github.py` - Debug RCA GitHub integration
- `debug_workflow.py` - Debug workflow execution
- `view_rca.py` - View RCA content from database
- `wait_and_check.py` - Monitoring and waiting script

### `tests/`
Integration and workflow tests.

- `test_alphanumeric_ids.py` - Test alphanumeric ID generation
- `test_code_fetch_workflow.py` - Test code fetch workflow
- `test_complete_workflow.py` - Test complete end-to-end workflow
- `test_db.py` - Test database operations
- `test_full_workflow.py` - Full workflow integration test
- `test_integrations.py` - Test external integrations
- `test_repo_extraction.py` - Test repository extraction logic

### `operations/`
Workflow management and fix utilities.

- `complete_approved_workflow.py` - Complete workflow for approved incidents (generates patch & PR)
- `fix_all_incidents_for_approval.py` - Batch fix utility for incidents
- `fix_approval_status.py` - Fix approval status issues
- `fix_status.py` - General status fix utility
- `force_pending_approval.py` - Force approval state for testing

## Usage

Run scripts from the project root:

```bash
# Check a specific feature
python tools/checks/check_github_integration.py

# Debug an issue
python tools/debug/debug_workflow.py

# Run a test
python tools/tests/test_complete_workflow.py

# Complete workflow for approved incidents
python tools/operations/complete_approved_workflow.py
```

## Notes

- Most scripts expect to be run from the project root directory
- Scripts may require environment variables to be configured (see main README.md)
- Some scripts interact directly with the database and should be used with caution
- Check script headers for specific usage instructions and requirements
