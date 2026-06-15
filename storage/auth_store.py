"""
auth_store.py — JSON-file based store for users, projects, teams, and role-based access.

Roles:
  admin       — Platform administrator; not tagged to any project by default.
  team_admin  — Manages a project's team, integrations, and feature access.
  user        — Regular team member; access scoped to assigned projects/features.

Default admin credentials:  username=admin  password=ChangeMe123!
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from typing import Optional

from storage.database import ProjectIntegrationConfig, get_session as get_db_session, init_database
from utils.secret_crypto import decrypt_secret, encrypt_secret

# ── Storage path ────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_HERE, "..", "data")
_AUTH_FILE = os.path.join(_DATA_DIR, "auth_data.json")

# Fields within each section that must be stored encrypted
_SECRET_FIELDS = {
    "llm": {"api_key"},
    "jira": {"api_token"},
    "github": {"token"},
    "anypoint": {"client_secret"},
    "slack": {"webhook_url"},
    "teams": {"webhook_url"},
    "runtime": set(),
    "repo_mappings": set(),  # no secrets — plain JSON mapping
}

# All sections persisted in the DB config table
_DB_CONFIG_SECTIONS = ("llm", "jira", "github", "anypoint", "slack", "teams", "runtime", "repo_mappings")

# ── Default admin credentials ───────────────────────────────────────────────
DEFAULT_ADMIN_USERNAME = os.getenv("PRISM_DEFAULT_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("PRISM_DEFAULT_ADMIN_PASSWORD", "ChangeMe123!")
DEFAULT_ADMIN_EMAIL = os.getenv("PRISM_DEFAULT_ADMIN_EMAIL", "admin@prism.local")

# ── All available feature keys ───────────────────────────────────────────────
ALL_FEATURES = [
    "dashboard",
    "incidents",
    "observability",
    "log_viewer",
    "traces",
    "metrics",
    "app_health",
    "api_analytics",
    "audit",
    "settings",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _now() -> str:
    return datetime.utcnow().isoformat()


def _new_id(prefix: str = "") -> str:
    return prefix + str(uuid.uuid4())[:8].upper()


def _sanitize_section_name(section: str) -> str:
    return "teams" if section in {"teams", "teams_notif"} else section


def _default_project_config(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "llm": {},
        "jira": {},
        "github": {},
        "anypoint": {},
        "slack": {},
        "teams": {},
        "teams_notif": {},
        "runtime": {},
        "repo_mappings": {},
    }


def _encrypt_section(section: str, values: dict) -> dict:
    secret_fields = _SECRET_FIELDS.get(section, set())
    encrypted: dict = {}
    for key, value in (values or {}).items():
        if key in secret_fields and value not in (None, ""):
            encrypted[key] = encrypt_secret(str(value))
        else:
            encrypted[key] = value
    return encrypted


def _decrypt_section(section: str, values: dict | None) -> dict:
    secret_fields = _SECRET_FIELDS.get(section, set())
    decrypted: dict = {}
    for key, value in (values or {}).items():
        if key in secret_fields and value not in (None, ""):
            decrypted[key] = decrypt_secret(value)
        else:
            decrypted[key] = value
    return decrypted


def _mask_section_for_ui(section: str, values: dict | None) -> dict:
    masked = dict(values or {})
    for key in _SECRET_FIELDS.get(section, set()):
        if masked.get(key):
            masked[key] = ""
            masked[key + "_configured"] = True
        else:
            masked[key + "_configured"] = False
    return masked


def _load_db_project_config(project_id: str, mask_secrets: bool = False) -> Optional[dict]:
    with get_db_session() as session:
        row = session.query(ProjectIntegrationConfig).filter_by(project_id=project_id).first()
        if not row:
            return None

        cfg = _default_project_config(project_id)
        for section in _DB_CONFIG_SECTIONS:
            section_data = _decrypt_section(section, getattr(row, section) or {})
            cfg[section] = _mask_section_for_ui(section, section_data) if mask_secrets else section_data
        cfg["teams_notif"] = dict(cfg["teams"])
        return cfg


def _save_db_project_config(project_id: str, section: str, values: dict) -> dict:
    normalized_section = _sanitize_section_name(section)
    if normalized_section not in _DB_CONFIG_SECTIONS:
        raise ValueError(f"Unsupported project config section: {section}")

    with get_db_session() as session:
        row = session.query(ProjectIntegrationConfig).filter_by(project_id=project_id).first()
        if not row:
            row = ProjectIntegrationConfig(project_id=project_id)
            session.add(row)
            session.flush()

        current_values = _decrypt_section(normalized_section, getattr(row, normalized_section) or {})
        merged_values = dict(current_values)

        for key, value in (values or {}).items():
            if key in _SECRET_FIELDS.get(normalized_section, set()) and value in (None, ""):
                continue
            merged_values[key] = value

        setattr(row, normalized_section, _encrypt_section(normalized_section, merged_values))
        session.flush()

    cfg = _load_db_project_config(project_id, mask_secrets=False)
    return cfg or _default_project_config(project_id)


def _migrate_legacy_project_configs(data: dict) -> bool:
    changed = False
    legacy_configs = data.get("project_configs", [])
    if not legacy_configs:
        return False

    for cfg in legacy_configs:
        project_id = cfg.get("project_id")
        if not project_id:
            continue
        for section in _DB_CONFIG_SECTIONS:
            section_values = cfg.get(section) or cfg.get("teams_notif" if section == "teams" else section) or {}
            if section_values:
                _save_db_project_config(project_id, section, section_values)
                changed = True

    if changed:
        data["project_configs"] = []
        _save(data)
    return changed


# ── File I/O ─────────────────────────────────────────────────────────────────

def _load() -> dict:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(_AUTH_FILE):
        return _seed_defaults()
    with open(_AUTH_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    _migrate_legacy_project_configs(data)
    return data


def _save(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _seed_defaults() -> dict:
    """Create the initial data file with a default admin user."""
    data: dict = {
        "users": [],
        "projects": [],
        "teams": [],
        "memberships": [],
        "project_assignments": [],
        "project_configs": [],
    }
    admin_id = "USR-ADMIN"
    data["users"].append(
        {
            "id": admin_id,
            "username": DEFAULT_ADMIN_USERNAME,
            "email": DEFAULT_ADMIN_EMAIL,
            "password_hash": _hash_password(DEFAULT_ADMIN_PASSWORD),
            "role": "admin",
            "features": ALL_FEATURES,
            "created_at": _now(),
            "is_active": True,
        }
    )
    _save(data)
    return data


def _ensure_keys(data: dict) -> None:
    """Back-fill missing top-level keys for older data files."""
    for key in ("project_assignments", "project_configs"):
        data.setdefault(key, [])
    for u in data.get("users", []):
        if "features" not in u:
            if u.get("role") == "admin":
                u["features"] = ALL_FEATURES
            elif u.get("role") == "team_admin":
                u["features"] = ALL_FEATURES
            else:
                u["features"] = ["dashboard", "incidents"]


# ════════════════════════════════════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════════════════════════════════════

def authenticate(username: str, password: str) -> Optional[dict]:
    """Return user dict on success, None on failure."""
    data = _load()
    _ensure_keys(data)
    pw_hash = _hash_password(password)

    matches = [
        u for u in data["users"]
        if u["username"] == username and u["password_hash"] == pw_hash and u.get("is_active", True)
    ]
    if not matches:
        return None

    assigned_user_ids = {a.get("user_id") for a in data.get("project_assignments", [])}

    def _sort_key(user: dict) -> tuple:
        return (
            1 if user.get("id") in assigned_user_ids else 0,
            1 if user.get("role") in {"team_admin", "admin"} else 0,
            user.get("created_at", ""),
        )

    matches.sort(key=_sort_key, reverse=True)
    return matches[0]


def get_user_by_id(user_id: str) -> Optional[dict]:
    data = _load()
    return next((u for u in data["users"] if u["id"] == user_id), None)


def get_user_by_username(username: str) -> Optional[dict]:
    data = _load()
    return next((u for u in data["users"] if u["username"] == username), None)


# ════════════════════════════════════════════════════════════════════════════
# USERS (admin management)
# ════════════════════════════════════════════════════════════════════════════

def list_users() -> list:
    data = _load()
    _ensure_keys(data)
    return data.get("users", [])


def create_user(payload: dict) -> dict:
    data = _load()
    _ensure_keys(data)

    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip()

    existing = next(
        (
            u for u in data["users"]
            if u.get("username", "").lower() == username.lower()
            or (email and u.get("email", "").lower() == email.lower())
        ),
        None,
    )
    if existing:
        return existing

    role = payload.get("role", "user")
    if role == "admin":
        default_features = ALL_FEATURES
    elif role == "team_admin":
        default_features = ALL_FEATURES
    else:
        default_features = payload.get("features", ["dashboard", "incidents"])

    user = {
        "id": "USR-" + _new_id(),
        "username": username,
        "email": email,
        "password_hash": _hash_password(payload.get("password", "changeme")),
        "role": role,
        "features": default_features,
        "created_at": _now(),
        "is_active": True,
    }
    data["users"].append(user)
    _save(data)
    return user


def update_user(user_id: str, payload: dict) -> Optional[dict]:
    data = _load()
    _ensure_keys(data)
    for u in data["users"]:
        if u["id"] == user_id:
            if "password" in payload:
                u["password_hash"] = _hash_password(payload.pop("password"))
            for k, v in payload.items():
                if k not in ("id", "password_hash"):
                    u[k] = v
            _save(data)
            return u
    return None


def delete_user(user_id: str) -> bool:
    data = _load()
    _ensure_keys(data)
    before = len(data["users"])
    data["users"] = [u for u in data["users"] if u["id"] != user_id]
    data["memberships"] = [m for m in data["memberships"] if m["user_id"] != user_id]
    data["project_assignments"] = [
        a for a in data["project_assignments"] if a["user_id"] != user_id
    ]
    _save(data)
    return len(data["users"]) < before


# ════════════════════════════════════════════════════════════════════════════
# FEATURE ACCESS PER USER (managed by Team Admin)
# ════════════════════════════════════════════════════════════════════════════

def set_user_features(user_id: str, features: list) -> Optional[dict]:
    """Team Admin sets which features a user can access."""
    return update_user(user_id, {"features": features})


def get_user_features(user_id: str) -> list:
    user = get_user_by_id(user_id)
    if not user:
        return []
    return user.get("features", ["dashboard", "incidents"])


# ════════════════════════════════════════════════════════════════════════════
# PROJECTS
# ════════════════════════════════════════════════════════════════════════════

def list_projects() -> list:
    data = _load()
    return data.get("projects", [])


def create_project(payload: dict) -> dict:
    data = _load()
    _ensure_keys(data)
    project = {
        "id": "PRJ-" + _new_id(),
        "name": payload["name"],
        "description": payload.get("description", ""),
        "repo_url": payload.get("repo_url", ""),
        "app_names": payload.get("app_names", []),
        "stack": payload.get("stack", ""),
        "environment": payload.get("environment", "production"),
        "owner_id": "USR-ADMIN",
        "team_admin_id": payload.get("team_admin_id", ""),
        "team_admin_email": payload.get("team_admin_email", ""),
        "created_at": _now(),
        "is_active": True,
    }
    data["projects"].append(project)
    _save(data)
    get_project_config(project["id"])
    return project


def get_project(project_id: str) -> Optional[dict]:
    data = _load()
    return next((p for p in data["projects"] if p["id"] == project_id), None)


def update_project(project_id: str, payload: dict) -> Optional[dict]:
    data = _load()
    for p in data["projects"]:
        if p["id"] == project_id:
            for k, v in payload.items():
                if k != "id":
                    p[k] = v
            _save(data)
            return p
    return None


def delete_project(project_id: str) -> bool:
    data = _load()
    before = len(data["projects"])
    data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
    data["project_assignments"] = [a for a in data.get("project_assignments", []) if a["project_id"] != project_id]
    _save(data)

    with get_db_session() as session:
        session.query(ProjectIntegrationConfig).filter_by(project_id=project_id).delete()

    return len(data["projects"]) < before


# ════════════════════════════════════════════════════════════════════════════
# PROJECT ↔ USER ASSIGNMENTS
# ════════════════════════════════════════════════════════════════════════════

def assign_user_to_project(user_id: str, project_id: str) -> dict:
    data = _load()
    _ensure_keys(data)
    existing = next(
        (a for a in data["project_assignments"] if a["user_id"] == user_id and a["project_id"] == project_id),
        None,
    )
    if existing:
        return existing
    assignment = {"id": "ASN-" + _new_id(), "user_id": user_id, "project_id": project_id, "assigned_at": _now()}
    data["project_assignments"].append(assignment)
    _save(data)
    return assignment


def remove_user_from_project(user_id: str, project_id: str) -> bool:
    data = _load()
    _ensure_keys(data)
    before = len(data["project_assignments"])
    data["project_assignments"] = [
        a for a in data["project_assignments"]
        if not (a["user_id"] == user_id and a["project_id"] == project_id)
    ]
    _save(data)
    return len(data["project_assignments"]) < before


def get_user_projects(user_id: str) -> list:
    """Return list of projects assigned to a user."""
    data = _load()
    _ensure_keys(data)
    user = next((u for u in data["users"] if u["id"] == user_id), None)
    if not user:
        return []
    if user.get("role") == "admin":
        return data.get("projects", [])
    project_ids = {a["project_id"] for a in data["project_assignments"] if a["user_id"] == user_id}
    for p in data.get("projects", []):
        if p.get("team_admin_id") == user_id:
            project_ids.add(p["id"])
    return [p for p in data.get("projects", []) if p["id"] in project_ids]


def get_project_users(project_id: str) -> list:
    """Return list of users assigned to a project."""
    data = _load()
    _ensure_keys(data)
    user_ids = {a["user_id"] for a in data["project_assignments"] if a["project_id"] == project_id}
    return [u for u in data["users"] if u["id"] in user_ids]


# ════════════════════════════════════════════════════════════════════════════
# PROJECT CONFIGS  (per-project integration settings)
# ════════════════════════════════════════════════════════════════════════════

def get_project_config(project_id: str, mask_secrets: bool = False) -> Optional[dict]:
    data = _load()
    _ensure_keys(data)
    cfg = _load_db_project_config(project_id, mask_secrets=mask_secrets)
    if cfg is None:
        cfg = _default_project_config(project_id)
        for section in _DB_CONFIG_SECTIONS:
            _save_db_project_config(project_id, section, {})
        cfg = _load_db_project_config(project_id, mask_secrets=mask_secrets)
    return cfg


def update_project_config(project_id: str, section: str, values: dict) -> Optional[dict]:
    data = _load()
    _ensure_keys(data)
    normalized_section = _sanitize_section_name(section)
    sanitized_values = {
        k: v for k, v in (values or {}).items()
        if k not in {"project_id", "section"} and v is not None
    }
    cfg = _save_db_project_config(project_id, normalized_section, sanitized_values)
    if normalized_section == "teams":
        cfg["teams_notif"] = dict(cfg["teams"])
    return cfg


def clear_project_config(project_id: str, section: str) -> Optional[dict]:
    normalized_section = _sanitize_section_name(section)
    if normalized_section not in _DB_CONFIG_SECTIONS:
        raise ValueError(f"Unsupported project config section: {section}")

    with get_db_session() as session:
        row = session.query(ProjectIntegrationConfig).filter_by(project_id=project_id).first()
        if not row:
            row = ProjectIntegrationConfig(project_id=project_id)
            session.add(row)
            session.flush()
        setattr(row, normalized_section, {})
        session.flush()

    return _load_db_project_config(project_id, mask_secrets=False) or _default_project_config(project_id)


def get_app_repo_mapping(project_id: str) -> dict:
    """
    Return the app→repo mapping dict for a project.
    Structure: { "app-name": {"repo": "org/repo", "branch": "main", "description": ""}, ... }
    Returns an empty dict if none configured.
    """
    cfg = get_project_config(project_id)
    if not cfg:
        return {}
    return dict(cfg.get("repo_mappings") or {})


def set_app_repo_mapping(project_id: str, mappings: dict) -> dict:
    """
    Overwrite the full app→repo mapping for a project.
    mappings: { "app-name": {"repo": "org/repo", "branch": "main", "description": ""}, ... }
    """
    return update_project_config(project_id, "repo_mappings", mappings) or {}


# ════════════════════════════════════════════════════════════════════════════
# TEAMS
# ════════════════════════════════════════════════════════════════════════════

def list_teams(project_id: Optional[str] = None) -> list:
    data = _load()
    teams = data.get("teams", [])
    if project_id:
        teams = [t for t in teams if t.get("project_id") == project_id]
    return teams


def create_team(payload: dict) -> dict:
    data = _load()
    _ensure_keys(data)
    team = {
        "id": "TM-" + _new_id(),
        "name": payload["name"],
        "description": payload.get("description", ""),
        "project_id": payload.get("project_id", ""),
        "created_at": _now(),
    }
    data["teams"].append(team)
    _save(data)
    return team


# ════════════════════════════════════════════════════════════════════════════
# SESSIONS  (simple in-memory via JSON)
# ════════════════════════════════════════════════════════════════════════════

_SESSIONS_FILE = os.path.join(_DATA_DIR, "sessions.json")


def _load_sessions() -> dict:
    if not os.path.exists(_SESSIONS_FILE):
        return {}
    with open(_SESSIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_sessions(sessions: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2)


def create_session(user: dict) -> str:
    token = str(uuid.uuid4())
    sessions = _load_sessions()
    sessions[token] = {
        "user_id": user["id"],
        "username": user["username"],
        "role": user.get("role", "user"),
        "email": user.get("email", ""),
        "features": user.get("features", ["dashboard", "incidents"]),
        "created_at": _now(),
    }
    _save_sessions(sessions)
    return token


def get_session(token: str) -> Optional[dict]:
    sessions = _load_sessions()
    return sessions.get(token)


def delete_session(token: str) -> None:
    sessions = _load_sessions()
    sessions.pop(token, None)
    _save_sessions(sessions)


# ════════════════════════════════════════════════════════════════════════════
# FIRST-RUN CHECK
# ════════════════════════════════════════════════════════════════════════════

def is_first_run() -> bool:
    """True if no auth data file exists yet (fresh install)."""
    return not os.path.exists(_AUTH_FILE)


# ════════════════════════════════════════════════════════════════════════════
# COMPAT ALIASES  (legacy names used by server.py)
# ════════════════════════════════════════════════════════════════════════════

def authenticate_user(username: str, password: str) -> Optional[dict]:
    return authenticate(username, password)
