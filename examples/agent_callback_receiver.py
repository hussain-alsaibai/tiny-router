"""Hardened agent callback receiver recipe.

A copyable reference service that wires together several zero-dep tiny-* repos
to deliver the boring-but-essential properties an asynchronous agent workflow
expects from a webhook endpoint:

  - shared-token / HMAC auth (middleware)
  - payload schema validation (tiny-validator)
  - delivery-ID idempotency with TTL (fast-cache)
  - structured JSON logs with request correlation (tiny-log)
  - cheap in-memory rate limiting per source (tiny-rate)
  - explicit /health, /ready, and /status endpoints

The point of the file is to be readable in one screen, vendor-able into a
bounty repro / internal automation, and easy to delete when the workflow
changes. Nothing here requires a database, a broker, or a framework.

Run it with:

    python examples/agent_callback_receiver.py
    # then POST to http://127.0.0.1:8088/callbacks/<provider>

This file is intentionally self-contained: it vendors the parts of tiny-*
it needs by re-implementing the small set of behaviours in-place so the
example runs even if those libraries aren't installed. Drop the inline
helpers and import the libraries once you adopt them.

Tested with Python 3.9+ and only the standard library.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


# ---------- Configuration ----------

CALLBACK_TOKEN = os.environ.get("CALLBACK_TOKEN", "local-dev-token")
CALLBACK_HMAC_SECRET = os.environ.get("CALLBACK_HMAC_SECRET", "")  # optional
CALLBACK_RATE_PER_MIN = int(os.environ.get("CALLBACK_RATE_PER_MIN", "120"))
DEDUPE_TTL_SECONDS = int(os.environ.get("CALLBACK_DEDUPE_TTL", "900"))  # 15m
HOST = os.environ.get("CALLBACK_HOST", "127.0.0.1")
PORT = int(os.environ.get("CALLBACK_PORT", "8088"))


# ---------- Minimal vendored helpers ----------
#
# Each helper below is the subset of a tiny-* library needed for this recipe.
# Replace with `import tiny_validator as tv`, `import fast_cache as fc`,
# `import tiny_log as tl`, `import tiny_rate as tr` once those are installed.
# The behaviour matches the upstream libraries 1:1 for the patterns exercised
# here.


def _log(level: str, msg: str, **fields: Any) -> None:
    """Structured JSON log line. Mirrors tiny-log's JSON formatter."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "level": level,
        "msg": msg,
        **fields,
    }
    print(json.dumps(record, default=str), flush=True)


def _validate_callback_payload(payload: Any) -> tuple[bool, list[str]]:
    """Tiny-validator-style check. Returns (ok, errors)."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return False, ["payload must be a JSON object"]
    event = payload.get("event")
    if not isinstance(event, str) or not event:
        errors.append("event: required non-empty string")
    job_id = payload.get("job_id")
    if job_id is not None and not isinstance(job_id, str):
        errors.append("job_id: must be string when present")
    status_value = payload.get("status")
    if status_value is not None and status_value not in {"ok", "error", "running"}:
        errors.append("status: must be one of ok|error|running when present")
    return not errors, errors


def _check_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time HMAC-SHA256 verification. Mirrors tiny-secret."""
    if not secret:
        return True  # signature check disabled
    if not header or not header.startswith("sha256="):
        return False
    provided = header.split("=", 1)[1]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


class _TTLDedupe:
    """TTL-keyed dedupe set. Mirrors fast-cache TTL semantics."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def seen(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            self._prune(now)
            if key in self._seen:
                return True
            self._seen[key] = now + self.ttl
            return False

    def _prune(self, now: float) -> None:
        expired = [k for k, exp in self._seen.items() if exp <= now]
        for k in expired:
            self._seen.pop(k, None)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"live_keys": len(self._seen), "ttl_seconds": self.ttl}


class _SlidingRateLimiter:
    """Tiny sliding-window rate limiter per source. Mirrors tiny-rate."""

    def __init__(self, max_per_minute: int) -> None:
        self.max = max_per_minute
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, source: str) -> tuple[bool, int]:
        """Return (allowed, remaining)."""
        now = time.time()
        with self._lock:
            window = self._hits[source]
            cutoff = now - 60.0
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self.max:
                return False, 0
            window.append(now)
            return True, self.max - len(window)


# ---------- App state ----------

DEDUPE = _TTLDedupe(DEDUPE_SECONDS_DEFAULT := 900)
RATE = _SlidingRateLimiter(CALLBACK_RATE_PER_MIN)
STARTED_AT = time.time()
COUNTS: dict[str, int] = defaultdict(int)
COUNTS_LOCK = threading.Lock()


def _record(event: str) -> None:
    with COUNTS_LOCK:
        COUNTS[event] += 1


# ---------- Router ----------

class _App:
    """Tiny route table. Mirrors the tiny-router surface used by this recipe."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Callable[..., Any]]] = []
        self.middlewares: list[Callable[..., Any]] = []

    def add(self, method: str, path: str, handler: Callable[..., Any]) -> None:
        self.routes.append((method, path, handler))

    def use(self, mw: Callable[..., Any]) -> None:
        self.middlewares.append(mw)

    def dispatch(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        request_id = headers.get("x-request-id") or str(uuid.uuid4())
        ctx = {"request_id": request_id, "started_at": time.time(), "path": path, "method": method}
        _log("info", "request.start", method=method, path=path, request_id=request_id)

        # Match by exact path; pattern routes (e.g. /callbacks/{provider}) handled by handler.
        handler = next((h for m, p, h in self.routes if m == method and p == path), None)
        pattern_handler = None
        pattern_path = None
        params: dict[str, str] = {}
        if handler is None:
            for m, p, h in self.routes:
                if m != method:
                    continue
                if "{" not in p:
                    continue
                regex = _compile_path(p)
                match = regex.match(path)
                if match:
                    pattern_handler = h
                    pattern_path = p
                    params = match.groupdict()
                    break

        chosen = handler or pattern_handler
        chosen_path = path if handler else pattern_path
        if chosen is None:
            _record("not_found")
            _log("warning", "request.not_found", request_id=request_id, path=path)
            return 404, {"content-type": "application/json", "x-request-id": request_id}, _json(
                {"error": "not_found", "path": path}
            )

        ctx["params"] = params
        ctx["headers"] = headers
        ctx["body"] = body

        # Run middleware chain.
        def call(req: dict[str, Any]) -> Any:
            return chosen(req, **req["params"])

        chain = call
        for mw in reversed(self.middlewares):
            chain = (lambda nxt, mw=mw: lambda req: mw(req, nxt))(chain)

        try:
            result = chain(ctx)
        except _HTTPError as exc:
            _record("error")
            _log("error", "request.error", request_id=request_id, error=str(exc))
            return exc.status, {"content-type": "application/json", "x-request-id": request_id}, _json(
                {"error": exc.message, "request_id": request_id}
            )
        except Exception as exc:  # noqa: BLE001
            _record("error")
            _log("error", "request.unhandled", request_id=request_id, error=str(exc))
            return 500, {"content-type": "application/json", "x-request-id": request_id}, _json(
                {"error": "internal", "request_id": request_id}
            )

        status, payload = _normalize_result(result, request_id)
        duration_ms = int((time.time() - ctx["started_at"]) * 1000)
        _record(f"status_{status}")
        _log("info", "request.end", request_id=request_id, route=chosen_path, status=status, duration_ms=duration_ms)
        return status, {"content-type": "application/json", "x-request-id": request_id}, payload


class _HTTPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _compile_path(template: str) -> "re.Pattern[str]":
    import re

    # Replace each "{name}" segment with a named capture group.
    pattern = re.sub(r"\{([^{}]+)\}", lambda m: f"(?P<{m.group(1)}>[^/]+)", template)
    return re.compile("^" + pattern + "$")


def _normalize_result(result: Any, request_id: str) -> tuple[int, bytes]:
    status = 200
    payload: Any
    if isinstance(result, tuple):
        if len(result) == 1:
            payload = result[0]
        elif len(result) == 2:
            payload, status = result
        else:
            payload, status, _ = result
    else:
        payload = result
    if isinstance(payload, (bytes, bytearray)):
        return status, bytes(payload)
    if isinstance(payload, str):
        return status, payload.encode()
    return status, json.dumps(payload, default=str).encode()


def _json(obj: Any) -> bytes:
    return json.dumps(obj, default=str).encode()


# ---------- Middleware ----------


def require_token(req: dict[str, Any], nxt: Callable[[dict[str, Any]], Any]) -> Any:
    """Reject requests missing or mismatching the shared token.

    Health endpoints are exempt so probes can hit /health without a token.
    """
    path = req.get("path", "")
    if path in {"/health", "/ready"}:
        return nxt(req)
    token = req["headers"].get("x-callback-token")
    if not token or not hmac.compare_digest(token, CALLBACK_TOKEN):
        raise _HTTPError(401, "unauthorized")
    return nxt(req)


def verify_signature(req: dict[str, Any], nxt: Callable[[dict[str, Any]], Any]) -> Any:
    """Verify HMAC signature when CALLBACK_HMAC_SECRET is set."""
    if not CALLBACK_HMAC_SECRET:
        return nxt(req)
    sig = req["headers"].get("x-signature")
    if not _check_signature(CALLBACK_HMAC_SECRET, req["body"], sig):
        raise _HTTPError(401, "bad_signature")
    return nxt(req)


# ---------- Handlers ----------


def health(_req: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "uptime_seconds": int(time.time() - STARTED_AT)}


def ready(_req: dict[str, Any]) -> dict[str, Any]:
    return {"ready": True, "dedupe_keys": DEDUPE.stats()["live_keys"]}


def status(_req: dict[str, Any]) -> dict[str, Any]:
    return {
        "counts": dict(COUNTS),
        "dedupe": DEDUPE.stats(),
        "rate_limit_per_min": CALLBACK_RATE_PER_MIN,
        "uptime_seconds": int(time.time() - STARTED_AT),
    }


def callback(req: dict[str, Any], provider: str) -> tuple[dict[str, Any], int]:
    """Process a callback. Validates, dedupes, rate-limits, then logs."""
    headers = req["headers"]
    source = headers.get("x-source-ip") or headers.get("x-forwarded-for", "unknown")
    allowed, remaining = RATE.allow(source)
    if not allowed:
        _record("rate_limited")
        raise _HTTPError(429, "rate_limited")

    delivery_id = headers.get("x-delivery-id") or hashlib.sha256(req["body"]).hexdigest()
    if DEDUPE.seen(delivery_id):
        _record("duplicate")
        _log("info", "callback.duplicate", provider=provider, delivery_id=delivery_id)
        return {"ok": True, "duplicate": True, "provider": provider, "delivery_id": delivery_id}, 200

    try:
        payload = json.loads(req["body"].decode() or "{}")
    except json.JSONDecodeError as exc:
        raise _HTTPError(400, f"invalid_json: {exc.msg}") from exc

    ok, errors = _validate_callback_payload(payload)
    if not ok:
        _record("invalid_payload")
        raise _HTTPError(422, "; ".join(errors))

    _record("accepted")
    _log(
        "info",
        "callback.accepted",
        provider=provider,
        delivery_id=delivery_id,
        event=payload.get("event"),
        job_id=payload.get("job_id"),
        rate_remaining=remaining,
    )
    return (
        {
            "ok": True,
            "accepted": True,
            "provider": provider,
            "delivery_id": delivery_id,
            "rate_remaining": remaining,
        },
        202,
    )


# ---------- Server wiring ----------


APP = _App()
APP.use(verify_signature)
APP.use(require_token)
APP.add("GET", "/health", health)
APP.add("GET", "/ready", ready)
APP.add("GET", "/status", status)
APP.add("POST", "/callbacks/{provider}", callback)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Suppress default access log; tiny-log already wrote structured records.
        return

    def _dispatch(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        # Inject the path into ctx for middleware that needs it.
        path = self.path.split("?", 1)[0]
        status, response_headers, payload = APP.dispatch(method, path, body, headers)
        self.send_response(status)
        for name, value in response_headers.items():
            self.send_header(name, value)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), _Handler)
    _log("info", "server.start", host=HOST, port=PORT, rate_per_min=CALLBACK_RATE_PER_MIN, dedupe_ttl=DEDUPE_TTL_SECONDS)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("info", "server.stop")
        server.server_close()


if __name__ == "__main__":
    main()