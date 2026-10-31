"""Shared visual assets for the CoS and MC pages."""
from pathlib import Path
from flask import send_from_directory

def install(app):
    @app.get("/founders-ui/<path:filename>")
    def founders_ui_asset(filename):
        return send_from_directory(Path(__file__).parent / "static", filename)
