"""Photos as stored data: a domain's /static/photos/ reads the stored folder first, then what ships in the image.

A photo a page shows ships in the image and stays in git. An optional photo lives in the stored-data folder, one
per environment, kept and backed up like the domain's other files. An empty or absent folder changes nothing: every
page serves exactly what the image carries.

The folder is ``$MINIMOI_PHOTOS_DIR/<domain>`` when that is set, otherwise ``domains/<domain>/data/photos``, which on
Dev and production is already the host's ``data/<domain>/photos`` through the domain's existing mount.

A stored file with a shipped photo's name replaces it on the page; any other stored file is served under its own name
for a page that names it. Bytes go out untouched.
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, send_from_directory
from werkzeug.utils import safe_join

PHOTOS_ENV = "MINIMOI_PHOTOS_DIR"
PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"})


def stored_photos_dir(domain: str, default_parent: Path) -> Path:
    """This environment's folder for a domain's stored photos. It need not exist."""
    root = os.environ.get(PHOTOS_ENV, "").strip()
    return (Path(root) / domain if root else Path(default_parent) / "photos").resolve()


def stored_photo(domain: str, default_parent: Path, filename: str) -> Path | None:
    """The stored file for this name, or None. Only images, and never outside the folder."""
    if Path(filename).suffix.lower() not in PHOTO_SUFFIXES:
        return None
    folder = stored_photos_dir(domain, default_parent)
    joined = safe_join(str(folder), filename)
    return Path(joined) if joined and os.path.isfile(joined) else None


def install_photo_route(app: Flask, domain: str, default_parent: Path, shipped: Path) -> None:
    """Serve /static/photos/<name> from the stored folder, falling back to the shipped folder. The rule is more
    specific than Flask's /static/<path>, and caching matches it (send_from_directory reads the same app setting)."""

    def stored_or_shipped_photo(filename: str):
        path = stored_photo(domain, default_parent, filename)
        if path is not None:
            return send_from_directory(stored_photos_dir(domain, default_parent), filename)
        return send_from_directory(shipped, filename)

    app.add_url_rule("/static/photos/<path:filename>", "stored_or_shipped_photo", stored_or_shipped_photo)
