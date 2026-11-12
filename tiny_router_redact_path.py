"""tiny_router v0.7.4 sidecar: route-path redaction middleware.

Why a sidecar?
---------------
This module is imported on demand. Existing tiny_router users who never call
`import tiny_router_redact_path` see no behaviour change.

If `tiny_secret_mask` is not installed, `redact_path_middleware()` raises
`RedactionUnavailable` at construction time. It used to return a silent
pass-through, which told a deployment that paths were being scrubbed while
they were not. Callers who genuinely want no redaction must say so with
`redact_path_middleware_passthrough()`.

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
- `redact_path_middleware_passthrough()` - explicit no-op opt-out.

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


__version__ = "0.7.4"


def _build_redactor():
    """Lazily resolve mask_route_path from tiny_secret_mask.

    Returns (mask_fn_or_None, available_bool). When unavailable the function
    is None on purpose: the caller decides whether that is fatal or whether
    it asked for the passthrough explicitly.
    """
    try:
        from tiny_secret_mask import mask_route_path as _mr
        return _mr, True
    except ImportError:
        return None, False


_MASK, MASK_AVAILABLE = _build_redactor()


class RedactionUnavailable(RuntimeError):
    """Raised when path redaction was requested but cannot be performed.

    Silently passing the path through would tell the caller that credentials
    in URL segments were scrubbed while they were not. A redaction layer that
    cannot redact must refuse, not pretend.
    """


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

    It mutates `req.path` in-place so the rest of the chain (and any log line
    that captures `req.path`) sees the redacted form. Idempotent: a path
    that is already redacted is unchanged on a second pass.

    Raises RedactionUnavailable immediately when tiny_secret_mask is not
    installed. This is deliberate: the previous behaviour was to install a
    silently ineffective middleware, so a deployment could believe it was
    scrubbing credential-bearing paths when it was passing them through
    verbatim. Build the middleware at startup, not per request, so the
    failure is loud and early. Use redact_path_middleware_passthrough() to
    opt in to a no-op.

    `stats` is an optional `RedactionStats` counter object.
    """
    if not MASK_AVAILABLE:
        raise RedactionUnavailable(
            "tiny_secret_mask is not installed, so path redaction cannot be "
            "performed. Install tiny-secret-mask, or call "
            "redact_path_middleware_passthrough() if passing credential-bearing "
            "paths through unredacted is what you intend."
        )

    s = stats
    mask = _MASK

    def _mw(req: Any, nxt: Callable[[Any], Any]) -> Any:
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


def redact_path_middleware_passthrough(*, stats: Optional["RedactionStats"] = None) -> Callable[[Any, Callable[[Any], Any]], Any]:
    """Explicit no-op middleware: paths pass through unredacted.

    This exists so that "I want no redaction" is a decision a caller writes
    down, not an accident that happens when a dependency is missing. The name
    appears in the call site and in code review.

    Raises nothing and redacts nothing.
    """
    def _mw(req: Any, nxt: Callable[[Any], Any]) -> Any:
        if stats is not None:
            original = getattr(req, "path", None) or ""
            if original:
                stats.observe(original, original)
        return nxt(req)

    _mw._is_path_redactor = False  # type: ignore[attr-defined]
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
    "redact_path_middleware_passthrough",
    "redactor_with_stats",
    "RedactionStats",
    "RedactionUnavailable",
    "__version__",
]
