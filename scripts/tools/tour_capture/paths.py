"""Private capture artifacts and credentials live outside application checkouts."""
from __future__ import annotations

import os
from pathlib import Path
import stat


def default_output_root() -> Path:
    configured = os.environ.get("MINIMOI_CAPTURE_OUTPUT_ROOT")
    return (Path(configured).expanduser() if configured else
            Path.home() / "minimoi-runtime" / "operators" / "tour-capture" / "runs")


def auth_root() -> Path:
    configured = os.environ.get("MINIMOI_CAPTURE_AUTH_ROOT")
    return (Path(configured).expanduser() if configured else
            Path.home() / "minimoi-runtime" / "credentials" / "tour-capture")


def ensure_private_directory(path: Path) -> Path:
    """No implicit permission changes or symlink traversal; create private parents.

    Trusted ancestors may be root-owned, owner-owned, or root's sticky tmp.
    The selected directory itself must be owned by this user and exactly0700.
    Existing permissions/data are never silently repaired or imported.
    """
    path = Path(path).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("capture private directory must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            st = current.lstat()
        mode = stat.S_IMODE(st.st_mode)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (0, os.getuid()):
            raise RuntimeError("capture private directory ownership unavailable")
        sticky_tmp = st.st_uid == 0 and bool(st.st_mode & stat.S_ISVTX)
        if mode & 0o022 and not sticky_tmp:
            raise RuntimeError("capture private directory parent is writable")
        if current == path and (st.st_uid != os.getuid() or mode != 0o700):
            raise RuntimeError("capture private directory must be owner-only0700")
    return path


def validate_private_file(path: Path) -> None:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
        raise RuntimeError("capture authentication state must be private regular0600")
