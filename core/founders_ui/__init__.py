"""Shared visual assets for the CoS and MC pages."""
import hashlib
from pathlib import Path
from flask import send_from_directory

def install(app):
    # Stable URLs for unchanged bytes; a new revision whenever either UI asset changes.
    root = Path(__file__).resolve().parents[2]
    assets = [Path(__file__).parent / 'static/founders.css', root / 'domains/cos/static/cos-history-ui.js']
    app.jinja_env.globals['founders_ui_version'] = hashlib.sha256(b''.join(p.read_bytes() for p in assets)).hexdigest()[:16]
    @app.get("/founders-ui/<path:filename>")
    def founders_ui_asset(filename):
        return send_from_directory(Path(__file__).parent / "static", filename)
