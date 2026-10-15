"""tiny_router v0.7.4 header-redaction middleware sidecar.

This module is imported by tiny_router at runtime (lazily). It adds:

- redact_headers_middleware(*, stats=None) - a tiny-router middleware that
  mutates req.headers in place by calling tiny_secret_mask.mask_headers().
  No-op if tiny_secret_mask is not installed.
- redactor_with_stats() - convenience that returns (middleware, stats).
  Useful for wiring to /metrics.
- HeaderStats - counter object with headers_seen, redactions,
  last_redacted_header, as_dict(). Same shape as tiny_headers_redact for
  cross-compat.

Backward compatibility: zero new dependencies, zero new required imports.
A user on tiny-router v0.7.3 (or earlier) without tiny_secret_mask sees
no behaviour change; the middleware is a passthrough.

v0.7.4 completes the credential-leak loop in tiny-router:

  v0.7.3 (path): mask_req.path via mask_route_path()
  v0.7.4 (header): mask_req.headers via mask_headers()

Together, every primitive that crosses a network boundary in tiny-router
is now expected to apply both redaction layers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


__version__ = "0.7.4"


class HeaderStats:
    """Counter object for header-redaction observability.

    Fields:
    - headers_seen: int - number of requests whose headers were inspected
    - redactions: int - number of headers actually replaced
    - last_redacted_header: Optional[str] - name of the most-recently-redacted header

    The shape is identical to tiny_headers_redact.HeaderStats for
    cross-compat. as_dict() returns a JSON-serialisable dict for direct
    emission to /metrics endpoints.
    """

    __slots__ = ("headers_seen", "redactions", "last_redacted_header")

    def __init__(self, headers_seen: int = 0, redactions: int = 0, last_redacted_header: Optional[str] = None):
        self.headers_seen = headers_seen
        self.redactions = redactions
        self.last_redacted_header = last_redacted_header

    def as_dict(self) -> Dict[str, Any]:
        return {
            "headers_seen": self.headers_seen,
            "redactions": self.redactions,
            "last_redacted_header": self.last_redacted_header,
        }


def _build_redactor():
    """Lazy-build the mask_headers function from tiny_secret_mask.

    Returns (mask_fn, available_bool). When unavailable, mask_fn is a
    passthrough that returns the input dict unchanged.
    """
    try:
        from tiny_secret_mask import mask_headers as _mh
        return _mh, True
    except ImportError:
        def _passthrough(headers, **kw):
            return dict(headers) if headers else {}
        return _passthrough, False


_MASK_HEADERS, MASK_AVAILABLE = _build_redactor()


def redact_headers_middleware(*, stats: Optional[HeaderStats] = None):
    """Return a tiny-router middleware that redacts req.headers in place.

    The middleware reads req.headers (a dict-like), produces a NEW dict
    via tiny_secret_mask.mask_headers(), and assigns it back to req.headers.
    No-op if tiny_secret_mask is not installed: req.headers is left
    unchanged.

    The middleware is order-sensitive: register it AFTER any middleware
    that injects headers (e.g. JWT middleware adding X-User-Id) so that
    injected headers are also redacted. Register it BEFORE any middleware
    that persists headers (logging, audit, trace export) so that persisted
    headers are redacted.

    >>> from tiny_router import Router
    >>> app = Router()
    >>> app.add(redact_headers_middleware())
    """
    def middleware(req, call_next):
        if hasattr(req, "headers") and req.headers is not None:
            redacted = _MASK_HEADERS(req.headers)
            if stats is not None:
                stats.headers_seen += 1
                # Count how many headers were actually replaced
                changed = sum(
                    1 for k in redacted if req.headers.get(k) != redacted.get(k)
                )
                stats.redactions += changed
                if changed > 0:
                    # Track the last header name that was redacted
                    for k in redacted:
                        if req.headers.get(k) != redacted.get(k):
                            stats.last_redacted_header = k
                            break
            req.headers = redacted
        return call_next(req)
    return middleware


def redactor_with_stats() -> Tuple[Any, HeaderStats]:
    """Convenience that returns (middleware, stats).

    Useful for wiring the redaction rate (redactions / headers_seen) to
    a /metrics endpoint.
    """
    stats = HeaderStats()
    return redact_headers_middleware(stats=stats), stats


__all__ = [
    "HeaderStats",
    "redact_headers_middleware",
    "redactor_with_stats",
]
