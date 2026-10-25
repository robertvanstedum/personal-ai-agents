"""Authenticated UI adapter; no model identity or client-supplied authority."""
import hashlib
import hmac

from flask import current_app
from . import cfg, feature_environ
from .api import _author, _floor, _principal
from .board_actions import BoardActions, BoardContext


def enabled():
    return feature_environ().get("MINIMOI_BOARD_ACTIONS") == "1"


def write(key, changes, *, target=None, version=None):
    env = feature_environ().get("MINIMOI_BOARD_ENV")
    owner = _principal()
    secret = current_app.secret_key
    if isinstance(secret, str):
        secret = secret.encode()
    if not secret:
        raise ValueError("board configuration unavailable")
    cursor_key = hmac.new(secret, b"minimoi-board-cursors-v1", hashlib.sha256).digest()
    def item_allowed(principal, item):
        result = cfg()["services"].queue.get_item(item)
        return principal == owner and result.ok and result.data is not None
    store = _floor()
    service = BoardActions(store, owner=owner, environment=env, cursor_key=cursor_key, item_allowed=item_allowed)
    ctx = BoardContext(owner, env, store.floor, owner, "ui:" + hashlib.sha256(key.encode()).hexdigest(),
                       frozenset({"guild.board.note.create", "guild.board.note.update"}), owner_session=True)
    if target is not None:
        # Photos keep their existing UI behavior; the first domain slice is text notes.
        row = store._run(lambda q: store._postit_by_id(q, target), write=False)
        if row and row["kind"] == "photo":
            field, value = next(iter(changes.items()))
            method = {"text": store.set_text, "label": store.set_label, "item_ref": store.set_link, "done": store.set_done}[field]
            return method(target, value, _author(), expect_version=version, idempotency_key=key)
    return service.ui_write(ctx, key, changes, target=target, version=version, author=_author())
