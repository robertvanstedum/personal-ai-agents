"""Shared presentation components and assets for CoS and Guild."""
import hashlib
from pathlib import Path
from flask import send_from_directory
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

def install(app):
    # Namespaced templates are available to both Flask apps without replacing
    # their own loaders or leaking shared names into unrelated domains.
    shared = Path(__file__).parent
    app.jinja_loader = ChoiceLoader([
        *([app.jinja_loader] if app.jinja_loader is not None else []),
        PrefixLoader({'founders_ui': FileSystemLoader(shared / 'templates')}),
    ])
    # Stable URLs for unchanged bytes; a new revision whenever either UI asset changes.
    root = Path(__file__).resolve().parents[2]
    assets = sorted(p for p in (Path(__file__).parent / 'static').rglob('*') if p.is_file()) + [root / 'domains/cos/static/cos-history-ui.js', root / 'domains/cos/static/cos-in-flight.js', root / 'domains/cos/static/cos-work-cards.js', root / 'domains/cos/static/calm-confer.css', root / 'domains/cos/static/cos-task-proposals.js', root / 'domains/cos/static/cos.css', root / 'domains/cos/static/calm-confer.js']
    app.jinja_env.globals['founders_ui_version'] = hashlib.sha256(b''.join(p.read_bytes() for p in assets)).hexdigest()[:16]
    @app.get("/founders-ui/<path:filename>")
    def founders_ui_asset(filename):
        return send_from_directory(Path(__file__).parent / "static", filename)
