"""Unit tests for project resolution during workflow startup."""

from agents.workflow import _resolve_project_id_for_incident


def test_resolve_project_id_prefers_repo_mapping_even_when_environment_differs(monkeypatch) -> None:
    projects = [
        {
            "id": "PRJ-04A0A733",
            "name": "Fallback Project",
            "repo_url": "",
            "app_names": [],
            "environment": "production",
        },
        {
            "id": "PRJ-ORDER",
            "name": "Order Processing",
            "repo_url": "",
            "app_names": [],
            "environment": "sandbox",
        },
    ]

    configs = {
        "PRJ-04A0A733": {"llm": {"provider": "openai"}, "repo_mappings": {}},
        "PRJ-ORDER": {
            "llm": {},
            "repo_mappings": {
                "order-processing-service": {
                    "repo": "avinash-ai-langchain/order-processing-service",
                    "branch": "main",
                }
            },
        },
    }

    monkeypatch.setattr("storage.auth_store.list_projects", lambda: projects)
    monkeypatch.setattr("storage.auth_store.get_project_config", lambda project_id: configs[project_id])

    resolved = _resolve_project_id_for_incident(None, "order-processing-service", "production")

    assert resolved == "PRJ-ORDER"


def test_resolve_project_id_uses_environment_alias_when_app_matches(monkeypatch) -> None:
    projects = [
        {
            "id": "PRJ-ORDER",
            "name": "Order Processing",
            "repo_url": "https://github.com/avinash-ai-langchain/order-processing-service.git",
            "app_names": [],
            "environment": "production",
        }
    ]

    configs = {
        "PRJ-ORDER": {"llm": {}, "repo_mappings": {}},
    }

    monkeypatch.setattr("storage.auth_store.list_projects", lambda: projects)
    monkeypatch.setattr("storage.auth_store.get_project_config", lambda project_id: configs[project_id])

    resolved = _resolve_project_id_for_incident(None, "order-processing-service", "prod")

    assert resolved == "PRJ-ORDER"


def test_resolve_project_id_falls_back_only_when_no_app_match_exists(monkeypatch) -> None:
    projects = [
        {
            "id": "PRJ-04A0A733",
            "name": "Fallback Project",
            "repo_url": "",
            "app_names": [],
            "environment": "production",
        },
        {
            "id": "PRJ-OTHER",
            "name": "Inventory",
            "repo_url": "https://github.com/acme/inventory-service.git",
            "app_names": ["inventory-service"],
            "environment": "production",
        },
    ]

    configs = {
        "PRJ-04A0A733": {"llm": {"provider": "openai"}, "repo_mappings": {}},
        "PRJ-OTHER": {"llm": {}, "repo_mappings": {}},
    }

    monkeypatch.setattr("storage.auth_store.list_projects", lambda: projects)
    monkeypatch.setattr("storage.auth_store.get_project_config", lambda project_id: configs[project_id])

    resolved = _resolve_project_id_for_incident(None, "order-processing-service", "production")

    assert resolved == "PRJ-04A0A733"
