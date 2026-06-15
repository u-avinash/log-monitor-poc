"""Unit tests for GitHub repository resolution hardening."""

from integrations.github_client import GitHubClient


def _make_client(org: str = "avinash-ai-langchain", mappings: dict | None = None) -> GitHubClient:
    client = GitHubClient.__new__(GitHubClient)
    client.project_id = "PRJ-TEST"
    client.org = org
    client.default_branch = "main"
    client.token = "test-token"
    client.client = None

    mapping_data = mappings or {}
    client._get_repo_mappings = lambda: mapping_data  # type: ignore[method-assign]
    return client


def test_clean_repo_full_name_removes_github_prefixes() -> None:
    client = _make_client()

    assert client._clean_repo_full_name("https://github.com/acme/orders.git") == "acme/orders"
    assert client._clean_repo_full_name("git@github.com:acme/orders.git") == "acme/orders"
    assert client._clean_repo_full_name("acme/orders/") == "acme/orders"


def test_is_full_repo_name_rejects_org_only_values() -> None:
    client = _make_client()

    assert client._is_full_repo_name("acme/orders") is True
    assert client._is_full_repo_name("avinash-ai-langchain") is False
    assert client._is_full_repo_name("") is False
    assert client._is_full_repo_name(None) is False


def test_extract_repo_from_log_ignores_org_only_mapping_and_uses_log_url() -> None:
    client = _make_client(
        mappings={
            "order-processing-service": {
                "repo": "avinash-ai-langchain",
                "branch": "main",
            }
        }
    )

    repo = client.extract_repo_from_log(
        "Build failed in https://github.com/avinash-ai-langchain/order-processing-service/actions/runs/123",
        "order-processing-service",
    )

    assert repo == "avinash-ai-langchain/order-processing-service"


def test_extract_repo_from_log_uses_case_insensitive_valid_mapping() -> None:
    client = _make_client(
        mappings={
            "Order-Processing-Service": {
                "repo": "https://github.com/avinash-ai-langchain/order-processing-service.git",
                "branch": "main",
            }
        }
    )

    repo = client.extract_repo_from_log("Error in order-processing-service", "order-processing-service")

    assert repo == "avinash-ai-langchain/order-processing-service"


def test_extract_repo_from_log_derives_repo_from_org_only_mapping() -> None:
    client = _make_client(
        mappings={
            "order-processing-service": {
                "repo": "avinash-ai-langchain",
                "branch": "main",
            }
        }
    )

    repo = client.extract_repo_from_log("Error in order-processing-service", "order-processing-service")

    assert repo == "avinash-ai-langchain/order-processing-service"
