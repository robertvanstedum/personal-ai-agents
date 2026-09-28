"""Master Craftsman on the Shop floor: the backend switch (off, stub, openclaw,
grok) and its connector contract. See backend.py."""
from .backend import (MASTER_CRAFTSMAN_STUB, SWITCH_VAR, CachedHealth, Health, MasterCraftsmanBackend,
                      NotAnAnswer, OffBackend, TurnRequest, UnavailableBackend, TurnResult, backend_from_env, keep_reply,
                      reply_author, switch_value, view)

__all__ = ["MASTER_CRAFTSMAN_STUB", "SWITCH_VAR", "CachedHealth", "Health", "MasterCraftsmanBackend", "NotAnAnswer",
           "OffBackend", "TurnRequest", "UnavailableBackend", "TurnResult", "backend_from_env", "keep_reply", "reply_author",
           "switch_value", "view"]
