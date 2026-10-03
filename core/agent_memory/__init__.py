"""MiniMoi's own copy of agents' memory files (Spec 160 / agent-memory v0.4 §3).

Once a day the copier reads the *named* memory files an agent keeps, scrubs
them, and publishes a ``current/`` set plus dated snapshots of what changed.
Everything here is plain Python and plain files: no Docker SDK, no network, no
database. A failed copy never breaks the app; it records one fixed code
(``errors.py``) and shows on the Agents light (``status.py``).
"""
