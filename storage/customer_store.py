"""Simple JSON-file-backed customer store for Prism multi-tenant admin."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "customers.json",
)


def _now() -> str:
    return datetime.utcnow().isoformat()


def _load() -> Dict[str, Any]:
    if not os.path.exists(_STORE_PATH):
        return {"customers": []}
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"customers": []}


def _save(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Customer CRUD ────────────────────────────────────────────────────────────

def list_customers() -> List[Dict]:
    return _load()["customers"]


def get_customer(customer_id: str) -> Optional[Dict]:
    return next((c for c in _load()["customers"] if c["id"] == customer_id), None)


def create_customer(payload: Dict[str, Any]) -> Dict:
    data = _load()
    cid = "cust_" + uuid.uuid4().hex[:8]
    slug = payload.get("name", "").lower().replace(" ", "-").replace("_", "-")
    # ensure unique slug
    existing_slugs = {c["slug"] for c in data["customers"]}
    base = slug
    i = 2
    while slug in existing_slugs:
        slug = f"{base}-{i}"
        i += 1

    customer: Dict[str, Any] = {
        "id": cid,
        "slug": slug,
        "name": payload.get("name", ""),
        "description": payload.get("description", ""),
        "industry": payload.get("industry", ""),
        "website": payload.get("website", ""),
        "logo_url": payload.get("logo_url", ""),
        "contact_name": payload.get("contact_name", ""),
        "contact_email": payload.get("contact_email", ""),
        "contact_phone": payload.get("contact_phone", ""),
        "plan": payload.get("plan", "starter"),
        "status": "active",
        "created_at": _now(),
        "updated_at": _now(),
        "environments": [],
        "team_members": [],
        "integrations": _default_integrations(),
        "tags": payload.get("tags", []),
        "notes": payload.get("notes", ""),
        "timezone": payload.get("timezone", "UTC"),
        "incident_retention_days": int(payload.get("incident_retention_days", 90)),
        "max_incidents_per_day": int(payload.get("max_incidents_per_day", 500)),
        "alert_email": payload.get("alert_email", payload.get("contact_email", "")),
    }

    # Auto-add owner as first team member
    if payload.get("contact_email"):
        customer["team_members"].append({
            "id": "user_" + uuid.uuid4().hex[:6],
            "name": payload.get("contact_name", ""),
            "email": payload["contact_email"],
            "role": "owner",
            "access_level": "admin",
            "status": "active",
            "invited_at": _now(),
        })

    data["customers"].append(customer)
    _save(data)
    return customer


def update_customer(customer_id: str, updates: Dict[str, Any]) -> Optional[Dict]:
    data = _load()
    for i, c in enumerate(data["customers"]):
        if c["id"] == customer_id:
            updates["updated_at"] = _now()
            data["customers"][i].update(updates)
            _save(data)
            return data["customers"][i]
    return None


def delete_customer(customer_id: str) -> bool:
    data = _load()
    before = len(data["customers"])
    data["customers"] = [c for c in data["customers"] if c["id"] != customer_id]
    if len(data["customers"]) < before:
        _save(data)
        return True
    return False


# ── Environment CRUD ─────────────────────────────────────────────────────────

def add_environment(customer_id: str, env: Dict[str, Any]) -> Optional[Dict]:
    data = _load()
    for i, c in enumerate(data["customers"]):
        if c["id"] == customer_id:
            eid = "env_" + uuid.uuid4().hex[:6]
            new_env = {
                "id": eid,
                "name": env.get("name", ""),
                "type": env.get("type", "staging"),
                "endpoint": env.get("endpoint", ""),
                "region": env.get("region", ""),
                "status": "active",
                "created_at": _now(),
            }
            data["customers"][i].setdefault("environments", []).append(new_env)
            data["customers"][i]["updated_at"] = _now()
            _save(data)
            return new_env
    return None


def remove_environment(customer_id: str, env_id: str) -> bool:
    data = _load()
    for i, c in enumerate(data["customers"]):
        if c["id"] == customer_id:
            before = len(c.get("environments", []))
            data["customers"][i]["environments"] = [
                e for e in c.get("environments", []) if e["id"] != env_id
            ]
            if len(data["customers"][i]["environments"]) < before:
                data["customers"][i]["updated_at"] = _now()
                _save(data)
                return True
    return False


# ── Team member CRUD ─────────────────────────────────────────────────────────

def add_team_member(customer_id: str, member: Dict[str, Any]) -> Optional[Dict]:
    data = _load()
    for i, c in enumerate(data["customers"]):
        if c["id"] == customer_id:
            uid = "user_" + uuid.uuid4().hex[:6]
            new_member = {
                "id": uid,
                "name": member.get("name", ""),
                "email": member.get("email", ""),
                "role": member.get("role", "viewer"),
                "access_level": member.get("access_level", "read"),
                "status": "invited",
                "invited_at": _now(),
            }
            data["customers"][i].setdefault("team_members", []).append(new_member)
            data["customers"][i]["updated_at"] = _now()
            _save(data)
            return new_member
    return None


def remove_team_member(customer_id: str, member_id: str) -> bool:
    data = _load()
    for i, c in enumerate(data["customers"]):
        if c["id"] == customer_id:
            before = len(c.get("team_members", []))
            data["customers"][i]["team_members"] = [
                m for m in c.get("team_members", []) if m["id"] != member_id
            ]
            if len(data["customers"][i]["team_members"]) < before:
                data["customers"][i]["updated_at"] = _now()
                _save(data)
                return True
    return False


def update_team_member(customer_id: str, member_id: str, updates: Dict) -> Optional[Dict]:
    data = _load()
    for ci, c in enumerate(data["customers"]):
        if c["id"] == customer_id:
            for mi, m in enumerate(c.get("team_members", [])):
                if m["id"] == member_id:
                    data["customers"][ci]["team_members"][mi].update(updates)
                    data["customers"][ci]["updated_at"] = _now()
                    _save(data)
                    return data["customers"][ci]["team_members"][mi]
    return None


# ── Integration CRUD ──────────────────────────────────────────────────────────

def update_integration(customer_id: str, integration_name: str, config: Dict) -> Optional[Dict]:
    data = _load()
    for i, c in enumerate(data["customers"]):
        if c["id"] == customer_id:
            data["customers"][i].setdefault("integrations", _default_integrations())
            data["customers"][i]["integrations"].setdefault(integration_name, {})
            data["customers"][i]["integrations"][integration_name].update(config)
            data["customers"][i]["updated_at"] = _now()
            _save(data)
            return data["customers"][i]["integrations"][integration_name]
    return None


def _default_integrations() -> Dict:
    return {
        "jira": {
            "enabled": False,
            "url": "",
            "username": "",
            "api_token": "",
            "project_key": "",
            "issue_type": "Bug",
            "verified": False,
            "last_verified": None,
            "error": None,
        },
        "github": {
            "enabled": False,
            "org": "",
            "repo": "",
            "token": "",
            "default_branch": "main",
            "verified": False,
            "last_verified": None,
            "error": None,
        },
        "slack": {
            "enabled": False,
            "webhook_url": "",
            "channel": "#incidents",
            "bot_token": "",
            "notify_on": ["critical", "high"],
            "verified": False,
            "last_verified": None,
            "error": None,
        },
        "llm": {
            "enabled": False,
            "provider": "openai",
            "api_key": "",
            "model": "gpt-4o",
            "base_url": "",
            "max_tokens": 4096,
            "temperature": 0.2,
            "verified": False,
            "last_verified": None,
            "error": None,
        },
        "pagerduty": {
            "enabled": False,
            "api_key": "",
            "service_id": "",
            "escalation_policy_id": "",
            "verified": False,
            "last_verified": None,
            "error": None,
        },
        "datadog": {
            "enabled": False,
            "api_key": "",
            "app_key": "",
            "site": "datadoghq.com",
            "verified": False,
            "last_verified": None,
            "error": None,
        },
    }
