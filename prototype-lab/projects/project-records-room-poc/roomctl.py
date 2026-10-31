"""Compatibility entrypoint; maintained implementation: domains.records.roomctl."""
from pathlib import Path as _Path
import sys as _sys
_root = next(parent for parent in _Path(__file__).resolve().parents
             if (parent / "domains" / "records").is_dir())
if str(_root) not in _sys.path:
    _sys.path.insert(0, str(_root))
from domains.records._compat import export as _export
_export(globals(), "domains.records.roomctl")
