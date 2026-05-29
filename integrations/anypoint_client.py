"""
Anypoint Platform API Client
Provides integration with MuleSoft Anypoint Runtime Manager, API Manager,
and Exchange APIs following MuleSoft best practices.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

ANYPOINT_BASE_URL = "https://anypoint.mulesoft.com"
CLOUDHUB_BASE_URL = "https://anypoint.mulesoft.com/cloudhub/api/v2"
ARM_BASE_URL = "https://anypoint.mulesoft.com/hybrid/api/v1"
API_MANAGER_BASE = "https://anypoint.mulesoft.com/apimanager/api/v1"
EXCHANGE_BASE = "https://anypoint.mulesoft.com/exchange/api/v2"


class AnypointClient:
    """
    Client for MuleSoft Anypoint Platform APIs.
    Supports Connected Apps (OAuth2 client_credentials) and Bearer token auth.
    Ref: https://docs.mulesoft.com/access-management/connected-apps-developers
    """

    def __init__(
        self,
        org_id: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        bearer_token: Optional[str] = None,
        timeout: int = 15,
    ):
        self.org_id = org_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._static_token = bearer_token
        self.timeout = timeout
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self.session = requests.Session()

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        """Obtain access token via Connected App OAuth2 client_credentials flow."""
        if self._static_token:
            return self._static_token

        if self._access_token and self._token_expires_at and datetime.utcnow() < self._token_expires_at:
            return self._access_token

        if not self.client_id or not self.client_secret:
            raise ValueError("Either bearer_token or (client_id + client_secret) must be provided")

        url = f"{ANYPOINT_BASE_URL}/accounts/api/v2/oauth2/token"
        resp = self.session.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = str(data["access_token"])
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in - 60)
        return self._access_token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _get(self, url: str, params: Optional[Dict] = None) -> Any:
        try:
            resp = self.session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("Anypoint API GET %s failed: %s", url, e)
            raise

    # ── Environments ──────────────────────────────────────────────────────────

    def list_environments(self) -> List[Dict]:
        """
        List all environments for the organization.
        GET /accounts/api/organizations/{orgId}/environments
        """
        url = f"{ANYPOINT_BASE_URL}/accounts/api/organizations/{self.org_id}/environments"
        data = self._get(url)
        return data.get("data", [])

    def get_environment(self, env_id: str) -> Optional[Dict]:
        """Get a specific environment by ID."""
        envs = self.list_environments()
        return next((e for e in envs if e.get("id") == env_id), None)

    # ── CloudHub Applications ─────────────────────────────────────────────────

    def list_cloudhub_applications(self, env_id: str) -> List[Dict]:
        """
        List all CloudHub 1.0 applications in an environment.
        GET /cloudhub/api/v2/applications
        Ref: https://docs.mulesoft.com/runtime-manager/cloudhub-api
        """
        url = f"{CLOUDHUB_BASE_URL}/applications"
        headers = {
            **self._headers(),
            "X-ANYPNT-ENV-ID": env_id,
            "X-ANYPNT-ORG-ID": self.org_id,
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("CloudHub apps fetch failed: %s", e)
            return []

    def get_cloudhub_application(self, env_id: str, app_name: str) -> Optional[Dict]:
        """
        Get details of a specific CloudHub application.
        GET /cloudhub/api/v2/applications/{domain}
        """
        url = f"{CLOUDHUB_BASE_URL}/applications/{app_name}"
        headers = {
            **self._headers(),
            "X-ANYPNT-ENV-ID": env_id,
            "X-ANYPNT-ORG-ID": self.org_id,
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("CloudHub app %s fetch failed: %s", app_name, e)
            return None

    def get_cloudhub_app_logs(self, env_id: str, app_name: str, limit: int = 100) -> List[Dict]:
        """
        Retrieve log messages for a CloudHub application.
        GET /cloudhub/api/v2/applications/{domain}/logs
        """
        url = f"{CLOUDHUB_BASE_URL}/applications/{app_name}/logs"
        headers = {
            **self._headers(),
            "X-ANYPNT-ENV-ID": env_id,
            "X-ANYPNT-ORG-ID": self.org_id,
        }
        try:
            resp = self.session.post(
                url,
                headers=headers,
                json={"startTime": 0, "endTime": 0, "limit": limit},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("CloudHub logs fetch failed: %s", e)
            return []

    # ── Runtime Fabric / Hybrid Servers ──────────────────────────────────────

    def list_hybrid_applications(self, env_id: str) -> List[Dict]:
        """
        List applications deployed via ARM (hybrid/RTF).
        GET /hybrid/api/v1/applications
        """
        url = f"{ARM_BASE_URL}/applications"
        headers = {
            **self._headers(),
            "X-ANYPNT-ENV-ID": env_id,
            "X-ANYPNT-ORG-ID": self.org_id,
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except requests.exceptions.RequestException as e:
            logger.error("ARM apps fetch failed: %s", e)
            return []

    def list_servers(self, env_id: str) -> List[Dict]:
        """
        List all registered servers / server groups.
        GET /hybrid/api/v1/servers
        """
        url = f"{ARM_BASE_URL}/servers"
        headers = {
            **self._headers(),
            "X-ANYPNT-ENV-ID": env_id,
            "X-ANYPNT-ORG-ID": self.org_id,
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except requests.exceptions.RequestException as e:
            logger.error("ARM servers fetch failed: %s", e)
            return []

    # ── API Manager ───────────────────────────────────────────────────────────

    def list_managed_apis(self, env_id: str) -> List[Dict]:
        """
        List APIs managed in API Manager for an environment.
        GET /apimanager/api/v1/organizations/{orgId}/environments/{envId}/apis
        """
        url = f"{API_MANAGER_BASE}/organizations/{self.org_id}/environments/{env_id}/apis"
        try:
            data = self._get(url)
            return data.get("assets", [])
        except Exception as e:
            logger.error("API Manager list failed: %s", e)
            return []

    def get_api_policies(self, env_id: str, api_id: str) -> List[Dict]:
        """List policies applied to an API in API Manager."""
        url = f"{API_MANAGER_BASE}/organizations/{self.org_id}/environments/{env_id}/apis/{api_id}/policies"
        try:
            data = self._get(url)
            return data.get("policies", [])
        except Exception as e:
            logger.error("API policies fetch failed: %s", e)
            return []

    # ── Exchange ──────────────────────────────────────────────────────────────

    def list_exchange_assets(self, search: str = "", asset_type: str = "") -> List[Dict]:
        """
        Search Anypoint Exchange assets.
        GET /exchange/api/v2/assets
        """
        params = {"organizationId": self.org_id}
        if search:
            params["search"] = search
        if asset_type:
            params["type"] = asset_type
        try:
            return self._get(f"{EXCHANGE_BASE}/assets", params=params)
        except Exception as e:
            logger.error("Exchange assets fetch failed: %s", e)
            return []

    # ── Aggregated Health Summary ─────────────────────────────────────────────

    def get_environment_health_summary(self, env_id: str, deployment_type: str = "cloudhub") -> Dict:
        """
        Aggregate application health across an environment.
        Returns counts by status and a list of app health records.
        """
        if deployment_type == "cloudhub":
            apps = self.list_cloudhub_applications(env_id)
        else:
            apps = self.list_hybrid_applications(env_id)

        total = len(apps)
        running = sum(1 for a in apps if _app_status(a) == "STARTED")
        stopped = sum(1 for a in apps if _app_status(a) == "STOPPED")
        failed = sum(1 for a in apps if _app_status(a) in ("DEPLOY_FAILED", "FAILED", "UNDEPLOYING"))

        return {
            "total": total,
            "running": running,
            "stopped": stopped,
            "failed": failed,
            "apps": [_normalize_app(a) for a in apps],
        }


def _app_status(app: Dict) -> str:
    """Normalize status field across CloudHub and ARM responses."""
    return (
        app.get("status")
        or app.get("artifact", {}).get("status")
        or "UNKNOWN"
    ).upper()


def _normalize_app(app: Dict) -> Dict:
    """Normalize app record to a standard shape for the UI."""
    status = _app_status(app)
    status_map = {
        "STARTED": "running",
        "STOPPED": "stopped",
        "DEPLOY_FAILED": "failed",
        "FAILED": "failed",
        "UNDEPLOYING": "degraded",
        "DEPLOYING": "deploying",
        "PARTIALLY_STARTED": "degraded",
    }
    ui_status = status_map.get(status, "unknown")

    # CloudHub shape
    domain = app.get("domain", "")
    app_name = app.get("name") or domain

    # Worker info (CloudHub)
    workers = {}
    if "workers" in app:
        w = app["workers"]
        workers = {
            "type": w.get("type", {}).get("name", ""),
            "amount": w.get("amount", 1),
        }

    return {
        "name": app_name,
        "domain": domain,
        "status": ui_status,
        "raw_status": status,
        "runtime": app.get("muleVersion", {}).get("version", "") if isinstance(app.get("muleVersion"), dict) else app.get("muleVersion", ""),
        "region": app.get("region", ""),
        "last_updated": app.get("lastUpdateTime", ""),
        "workers": workers,
        "url": f"https://{domain}.cloudhub.io" if domain else "",
        "deployment_type": "cloudhub" if domain else "hybrid",
    }


def build_client_from_project(project: Dict) -> Optional[AnypointClient]:
    """
    Build an AnypointClient from a project config dict.
    Looks for integrations.anypoint config block.
    """
    anypoint_cfg = project.get("integrations", {}).get("anypoint", {})
    if not anypoint_cfg.get("enabled"):
        return None

    org_id = anypoint_cfg.get("org_id", "")
    if not org_id:
        return None

    return AnypointClient(
        org_id=org_id,
        client_id=anypoint_cfg.get("client_id") or None,
        client_secret=anypoint_cfg.get("client_secret") or None,
        bearer_token=anypoint_cfg.get("bearer_token") or None,
    )
