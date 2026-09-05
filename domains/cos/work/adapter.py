"""The product-free in-process entry point.

This is the thinnest thing that can carry a version-1 request envelope to the
service and a version-1 response envelope back. It names no product, no
runtime, no channel and no model, and it appears in no canonical record:
deleting this file changes no stored byte, and every record written through
it stays readable without it.

That is the whole point of the seam. A later gate wires a real caller to the
service; a local script can drive all eight effects through this one today.
Neither becomes part of what the record says happened.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from .envelope import build_request
from .service import WorkService


@runtime_checkable
class WorkAdapter(Protocol):
    """Anything that can carry one envelope in and one envelope back."""

    def invoke(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Carry one version-1 request envelope to its response."""
        ...


class InProcessWorkAdapter:
    """One process, one service, no transport in between."""

    def __init__(self, service: WorkService) -> None:
        self._service = service

    def invoke(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Carry one version-1 request envelope to its response."""
        return self._service.invoke(request)

    def call(
        self,
        effect: str,
        params: Mapping[str, Any],
        *,
        operation_id: str | None = None,
        grant_ref: str | None = None,
    ) -> dict[str, Any]:
        """Build the envelope and invoke it, for a caller that has neither."""
        return self.invoke(
            build_request(effect, params, operation_id=operation_id, grant_ref=grant_ref)
        )


__all__ = ["InProcessWorkAdapter", "WorkAdapter"]
