"""tiny_router_sse - Server-Sent Events support for tiny-router v0.7.0.

Sidecar module that adds SSE-aware OpenAPI mode + a structured event type
on top of the core tiny_router package. Keep the v0.6.0 single-file
experience intact; this sidecar is only imported if SSE support is
needed.

Exports:
    SSEEvent, sse_response, format_sse_event, SSE_KEEPALIVE_INTERVAL,
    sse_keepalive, is_streaming_route, mark_streaming, add_streaming_route

Usage::

    from tiny_router import Router, openapi_spec
    from tiny_router_sse import sse_response, SSEEvent

    app = Router()

    @app.get("/events")
    def events(req):
        def gen():
            for i in range(3):
                yield SSEEvent(data=f"hello {i}", event="greeting", id=str(i))
        return sse_response(gen())

    spec = openapi_spec(app, streaming=True)
    # spec["paths"]["/events"]["get"]["responses"]["200"]["content"][
    #     "text/event-stream"]["schema"]["type"] == "string"
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

try:
    from .tiny_router import (
        Router,
        StreamingResponse,
        Response,
        Request,
        HTTPError,
    )
except ImportError:  # pragma: no cover - flat-layout fallback
    from tiny_router import (
        Router,
        StreamingResponse,
        Response,
        Request,
        HTTPError,
    )


__version__ = "0.7.0"


# Default keepalive interval in seconds. Per the SSE spec, the server
# should send a comment line (starting with ``:``) every ~30s to keep
# proxies from closing the connection. Override per-route if needed.
SSE_KEEPALIVE_INTERVAL: float = 15.0


@dataclass(frozen=True)
class SSEEvent:
    """A single Server-Sent Event.

    Mirrors the SSE spec (https://html.spec.whatwg.org/multipage/server-sent-events.html):
    - ``data`` is the payload (string or JSON-serialisable). Newlines in
      ``data`` are escaped per spec (each line becomes its own ``data:``
      field).
    - ``event`` is the event name (optional). Defaults to ``"message"``
      on the wire if not set.
    - ``id`` is the event ID (optional). Surfaces in the client's
      ``EventSource.lastEventId``.
    - ``retry`` is the client reconnect time in ms (optional).
    - ``comment`` is an optional comment line (emitted as ``: comment``).
    """

    data: Any = ""
    event: str | None = None
    id: str | None = None
    retry: int | None = None
    comment: str | None = None

    def format(self) -> str:
        """Format the event as a wire-protocol SSE message.

        Always terminates with ``\n\n`` (the SSE message boundary).
        Lines that contain newlines are split: each becomes its own
        ``data:`` field, which is the spec-mandated escape for multiline
        data.
        """
        parts: list[str] = []
        if self.comment:
            for line in self._split_lines(self.comment):
                parts.append(f": {line}")

        if self.event is not None:
            parts.append(f"event: {self.event}")
        if self.id is not None:
            parts.append(f"id: {self.id}")
        if self.retry is not None:
            parts.append(f"retry: {int(self.retry)}")

        # data: payload (one line per data line)
        if isinstance(self.data, (dict, list, tuple)):
            payload = json.dumps(self.data, separators=(",", ":"), default=str)
        elif self.data is None:
            payload = ""
        else:
            payload = str(self.data)
        if payload == "":
            # An event with empty data still needs the data: line so the
            # client sees a heartbeat / dispatch signal.
            parts.append("data: ")
        else:
            for line in self._split_lines(payload):
                parts.append(f"data: {line}")

        NL = chr(10) + chr(10); L = chr(10)
        return L.join(parts) + NL

    @staticmethod
    def _split_lines(text: str) -> list[str]:
        # SSE splits on \r\n, \r, or \n (per spec).
        out: list[str] = []
        cur = ""
        i = 0
        while i < len(text):
            c = text[i]
            if c == "\r" and i + 1 < len(text) and text[i + 1] == "\n":
                out.append(cur)
                cur = ""
                i += 2
            elif c in ("\r", "\n"):
                out.append(cur)
                cur = ""
                i += 1
            else:
                cur += c
                i += 1
        out.append(cur)
        return out


def format_sse_event(event: SSEEvent) -> bytes:
    """Format an SSEEvent as UTF-8 bytes (ready to write to a socket)."""
    return event.format().encode("utf-8")


def sse_keepalive(interval: float = SSE_KEEPALIVE_INTERVAL) -> SSEEvent:
    """Return a keepalive SSEEvent (a single comment line).

    Useful for periodic injection into an event stream to keep proxies
    from closing the connection. Use the result with ``sse_response``::

        def gen():
            while True:
                yield sse_keepalive()
                time.sleep(15)
    """
    return SSEEvent(comment="keepalive")


class SSEResponse(StreamingResponse):
    """A StreamingResponse pre-configured for ``text/event-stream``.

    Yield ``SSEEvent`` instances (or any object with a ``.format()``
    method that returns a string); each is serialised to the wire
    format. Strings are passed through (after a trailing newline is
    appended if missing). Other objects are ``json.dumps``'d into a
    single ``data:`` line.

    The ``Cache-Control: no-store`` header is set automatically so
    intermediaries don't buffer the stream.
    """

    def __init__(
        self,
        generator: Iterable[SSEEvent | str | bytes | Any],
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
        keepalive_interval: float | None = None,
    ) -> None:
        super().__init__(generator, status=status, headers=headers)
        # Force the SSE content type and disable caching.
        self.headers["content-type"] = "text/event-stream; charset=utf-8"
        self.headers["cache-control"] = "no-store"
        self.headers["x-accel-buffering"] = "no"  # nginx: don't buffer
        self.headers.setdefault("connection", "keep-alive")
        self._keepalive_interval = keepalive_interval

    def __iter__(self) -> Iterator[bytes]:  # type: ignore[override]
        for chunk in self._generator:
            if isinstance(chunk, SSEEvent):
                yield format_sse_event(chunk)
            elif isinstance(chunk, str):
                # Raw string: ensure trailing newline so the next event
                # starts on a fresh line.
                if not chunk.endswith(chr(10)):
                    chunk = chunk + chr(10)
                yield chunk.encode("utf-8")
            elif isinstance(chunk, bytes):
                yield chunk
            else:
                # Anything else: JSON-serialise into a data: field.
                payload = json.dumps(chunk, default=str, separators=(",", ":"))
                NL = chr(10) + chr(10)
                yield (("data: " + payload + NL).encode("utf-8"))


def sse_response(
    generator: Iterable[SSEEvent | str | bytes | Any],
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> SSEResponse:
    """Build a Server-Sent Events response from a generator.

    Equivalent to ``SSEResponse(generator, ...)`` but reads more clearly
    at the call site. Sets the standard SSE headers (content-type,
    cache-control, x-accel-buffering) automatically.

    Example::

        @app.get("/events")
        def events(req):
            def gen():
                for i in range(3):
                    yield SSEEvent(data={"i": i}, event="tick")
            return sse_response(gen())
    """
    return SSEResponse(generator, status=status, headers=headers)


# ---------- Streaming route annotation ----------


# Per-router registry of routes that return a streaming response. The
# router keeps a set of (method, path) tuples; routes that are present
# in this set get a ``text/event-stream`` content type in the OpenAPI
# spec when ``openapi_spec(router, streaming=True)`` is called.

def _streaming_key(method: str, path: str) -> tuple[str, str]:
    return (method.upper(), path)


def mark_streaming(router: Router, method: str, path: str) -> None:
    """Mark a route on a router as streaming.

    Used by the OpenAPI exporter (``streaming=True`` mode) to attach a
    ``text/event-stream`` content type to the route. Most users will
    not call this directly - use ``@app.get(path, streaming=True)``
    (or the equivalent decorator argument) instead.
    """
    if not hasattr(router, "_streaming_routes"):
        router._streaming_routes = set()  # type: ignore[attr-defined]
    router._streaming_routes.add(_streaming_key(method, path))  # type: ignore[attr-defined]


def is_streaming_route(router: Router, method: str, path: str) -> bool:
    """Return True if the given route is marked as streaming.

    The path should be the public OpenAPI form (``/users/{id}``), not
    the internal regex.
    """
    routes = getattr(router, "_streaming_routes", None)
    if not routes:
        return False
    return _streaming_key(method, path) in routes


def add_streaming_route(router: Router, method: str, pattern: str) -> None:
    """Register ``pattern`` as streaming. Internal helper used by the
    decorator wrapper installed by ``install_streaming_decorator``.
    """
    mark_streaming(router, method, pattern)


def install_streaming_decorator(router: Router) -> None:
    """Install a ``streaming=True`` kwarg on the router's verb-specific
    decorators (``@app.get``, ``@app.post``, ...).

    After calling this, ``@app.get(path, streaming=True)`` will register
    the route AND mark it as streaming for OpenAPI export. The
    implementation monkey-patches each verb method once.

    Idempotent: calling twice is a no-op.

    Example::

        app = Router()
        install_streaming_decorator(app)

        @app.get("/events", streaming=True)
        def events(req):
            ...
    """
    if getattr(router, "_streaming_decorator_installed", False):
        return
    for verb in ("get", "post", "put", "patch", "delete", "head", "options"):
        original = getattr(router, verb)

        def make(method_name, original_fn):
            def wrapper(pattern, tags=None, *, streaming=False):
                decorator = original_fn(pattern, tags=tags)

                def wrap(fn):
                    if streaming:
                        add_streaming_route(router, method_name.upper(), pattern)
                    return decorator(fn)

                return wrap
            return wrapper

        setattr(router, verb, make(verb, original))
    router._streaming_decorator_installed = True  # type: ignore[attr-defined]


# ---------- OpenAPI streaming export ----------


def _content_type_for_streaming() -> dict:
    """The OpenAPI 3.1 content-type entry for text/event-stream."""
    return {
        "text/event-stream": {
            "schema": {
                # The wire format is text, but the payload inside each
                # event is JSON. We declare ``type: string`` so the spec
                # is valid; consumers that want JSON-schema validation
                # should add a per-event schema separately.
                "type": "string",
                "description": (
                    "Server-Sent Events stream. Each event has the shape "
                    "``{data, event?, id?, retry?, comment?}``; see the "
                    "SSEEvent dataclass in tiny_router_sse."
                ),
            }
        }
    }


def openapi_spec_streaming_overlay(
    spec: dict, router: Router
) -> dict:
    """Add ``text/event-stream`` content to any route marked streaming.

    Mutates and returns ``spec``. Existing JSON content types are
    preserved; the SSE entry is added alongside (clients pick the one
    they support via the ``Accept`` header).
    """
    routes = getattr(router, "_streaming_routes", None)
    if not routes:
        return spec

    paths = spec.get("paths", {})
    for method, pattern in routes:
        # The pattern stored in the router is the public form, so we
        # can look it up directly.
        path_item = paths.get(pattern)
        if not path_item:
            continue
        op = path_item.get(method.lower())
        if not op:
            continue
        responses = op.setdefault("responses", {})
        resp_200 = responses.setdefault("200", {"description": "OK"})
        content = resp_200.setdefault("content", {})
        content.update(_content_type_for_streaming())
    return spec


__all__ = [
    "SSEEvent",
    "sse_response",
    "SSEResponse",
    "format_sse_event",
    "sse_keepalive",
    "SSE_KEEPALIVE_INTERVAL",
    "mark_streaming",
    "is_streaming_route",
    "add_streaming_route",
    "install_streaming_decorator",
    "openapi_spec_streaming_overlay",
    "__version__",
]
