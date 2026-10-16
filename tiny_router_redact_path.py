"""tiny_router v0.7.3 sidecar: route-path redaction middleware.

Why a sidecar?
---------------
This module is imported on demand. Existing tiny_router users who never call
`import tiny_router_redact_path` see no behaviour change. The middleware is
also a pure pass-through if `tiny_secret_mask` is not installed (or is older
than v0.4.0).

Public API
----------
- `redact_path_middleware()` - returns a `Middleware` that calls
  `tiny_secret_mask.mask_route_path(req.path)` and replaces the path in-place
  before the next handler runs. Pair it with `request_id` so the log line and
  the redacted path share the same correlation ID.
- `RedactionStats` - lightweight counter object (`paths_seen`, `redactions`,
  `last_redacted_path`) useful for `/metrics` endpoints.
- `redactor_with_stats()` - returns `(middleware, stats)`. The middleware
  mutates the stats object as requests flow through.

Quick start
------------
>>> from tiny_router import Router, serve
>>> from tiny_router_redact_path import redact_path_middleware
>>> app = Router()
>>> app.add_middleware(redact_path_middleware())
>>> @app.get("/u/{id}")
... def user(req, id): return {"id": id}
>>> # Inbound /u/550e8400-e29b-41d4-a716-446655440000 becomes /u/[REDACTED:uuid].
"""

from __future__ import annotations

from typing import Any, Callable, Optional


__version__ = "0.7.3"


def _resolve_mask():
    """Return tiny_secret_mask.mask_route_path or None if unavailable."""
    try:
        from tiny_secret_mask import mask_route_path
        return mask_route_path
    except ImportError:
        return None
    except Exception:
        return None


_MASK = _resolve_mask()


class RedactionStats:
    """Lightweight counter object exposed for /metrics integration.

    >>> s = RedactionStats()
    >>> s.observe("/u/550e8400-e29b-41d4-a716-446655440000", "/u/[REDACTED:uuid]")
    >>> s.redactions
    1
    """

    __slots__ = ("paths_seen", "redactions", "last_redacted_path")

    def __init__(self) -> None:
        self.paths_seen: int = 0
        self.redactions: int = 0
        self.last_redacted_path: Optional[str] = None

    def observe(self, original: str, redacted: str) -> None:
        self.paths_seen += 1
        if redacted != original:
            self.redactions += 1
        self.last_redacted_path = redacted

    def as_dict(self) -> dict:
        return {
            "paths_seen": self.paths_seen,
            "redactions": self.redactions,
            "last_redacted_path": self.last_redacted_path,
        }


def redact_path_middleware(*, stats: Optional["RedactionStats"] = None) -> Callable[[Any, Callable[[Any], Any]], Any]:
    """Return a tiny_router middleware that redacts credentials in req.path.

    The middleware is a no-op if `tiny_secret_mask` is not importable. It
    mutates `req.path` in-place so the rest of the chain (and any log line
    that captures `req.path`) sees the redacted form. Idempotent: a path
    that is already redacted is unchanged on a second pass.

    `stats` is an optional `RedactionStats` counter object.
    """
    s = stats
    mask = _MASK

    def _mw(req: Any, nxt: Callable[[Any], Any]) -> Any:
        if mask is None:
            return nxt(req)
        original = getattr(req, "path", None) or ""
        if not original:
            return nxt(req)
        redacted = mask(original)
        if redacted != original:
            try:
                req.path = redacted
            except Exception:
                pass
        if s is not None:
            s.observe(original, redacted)
        return nxt(req)

    _mw._is_path_redactor = True  # type: ignore[attr-defined]
    return _mw


def redactor_with_stats() -> tuple:
    """Convenience: return (middleware, stats) pair.

    >>> mw, stats = redactor_with_stats()
    >>> # attach mw to a Router; later read stats.as_dict() for /metrics
    """
    s = RedactionStats()
    return redact_path_middleware(stats=s), s


__all__ = [
    "redact_path_middleware",
    "redactor_with_stats",
    "RedactionStats",
    "__version__",
]
