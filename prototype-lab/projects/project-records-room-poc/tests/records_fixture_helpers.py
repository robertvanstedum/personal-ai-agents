"""Disposable historical schema fixture helpers."""
def remove_v5_shape(db):
    """Reconstruct an old-schema test fixture, never use on real records."""
    db.execute("DROP TRIGGER session_parent_required")
    db.execute("DROP TRIGGER session_parent_immutable")
    db.execute("DROP INDEX session_parent")
    db.execute("ALTER TABLE rooms DROP COLUMN parent_room_id")
    db.execute("DROP TABLE persistent_rooms")
