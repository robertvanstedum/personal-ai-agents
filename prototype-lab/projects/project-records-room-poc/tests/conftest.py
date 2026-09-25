"""Make the standalone PoC importable regardless of pytest collection order."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def remove_v5_shape(db):
    """Reconstruct an old-schema test fixture, never use on real records."""
    db.execute("DROP TRIGGER session_parent_required")
    db.execute("DROP TRIGGER session_parent_immutable")
    db.execute("DROP INDEX session_parent")
    db.execute("ALTER TABLE rooms DROP COLUMN parent_room_id")
    db.execute("DROP TABLE persistent_rooms")
