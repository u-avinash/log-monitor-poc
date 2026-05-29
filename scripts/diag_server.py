"""Diagnostic script to identify the dashboard 500 error."""
import sys
import traceback
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from storage.database import get_db
    from storage.incident_repository import IncidentRepository
    from fastapi.templating import Jinja2Templates

    db = next(get_db())
    repo = IncidentRepository(db)
    incidents = repo.get_all(limit=1000)
    print(f"Incidents fetched: {len(incidents)}")

    # Test _compute_stats
    from ui.server import _compute_stats
    stats = _compute_stats(incidents)
    print(f"Stats computed: {stats}")

    # Test template rendering
    _HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")
    templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))

    # Check if static files exist
    static_dir = os.path.join(_HERE, "static")
    print(f"Static dir exists: {os.path.exists(static_dir)}")
    print(f"Static files: {os.listdir(static_dir) if os.path.exists(static_dir) else 'N/A'}")

    # Check templates dir
    tmpl_dir = os.path.join(_HERE, "templates")
    print(f"Templates dir exists: {os.path.exists(tmpl_dir)}")
    print(f"Template files: {os.listdir(tmpl_dir) if os.path.exists(tmpl_dir) else 'N/A'}")

    # Try rendering directly
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(tmpl_dir))
    tmpl = env.get_template("dashboard.html")
    recent = incidents[:10]
    result = tmpl.render(
        request=None,
        stats=stats,
        recent_incidents=recent,
        api_online=False,
        page="dashboard",
    )
    print(f"Template rendered OK, length: {len(result)}")

except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
