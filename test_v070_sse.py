"""v0.7.0 - Tests for tiny_router_sse (SSE sidecar)."""
from __future__ import annotations

import io
import unittest

from tiny_router import Router, openapi_spec
from tiny_router_sse import (
    SSE_KEEPALIVE_INTERVAL,
    SSEEvent,
    SSEResponse,
    add_streaming_route,
    format_sse_event,
    install_streaming_decorator,
    is_streaming_route,
    mark_streaming,
    openapi_spec_streaming_overlay,
    sse_keepalive,
    sse_response,
)


class TestSSEEventFormat(unittest.TestCase):
    def test_basic(self):
        e = SSEEvent(data="hello", event="greeting", id="42")
        wire = e.format()
        self.assertIn("event: greeting", wire)
        self.assertIn("id: 42", wire)
        self.assertIn("data: hello", wire)
        self.assertTrue(wire.endswith(chr(10) + chr(10)))

    def test_data_only(self):
        e = SSEEvent(data="raw")
        self.assertEqual(e.format(), "data: raw" + chr(10) + chr(10))

    def test_multiline_data(self):
        e = SSEEvent(data="line1" + chr(10) + "line2")
        wire = e.format()
        self.assertIn("data: line1", wire)
        self.assertIn("data: line2", wire)

    def test_crlf_data(self):
        e = SSEEvent(data="a" + chr(13) + chr(10) + "b")
        wire = e.format()
        self.assertIn("data: a", wire)
        self.assertIn("data: b", wire)

    def test_retry(self):
        e = SSEEvent(data="x", retry=3000)
        self.assertIn("retry: 3000", e.format())

    def test_comment(self):
        e = SSEEvent(data="x", comment="hello world")
        self.assertIn(": hello world", e.format())

    def test_json_data(self):
        e = SSEEvent(data={"i": 1, "ok": True})
        wire = e.format()
        self.assertIn('data: {"i":1,"ok":true}', wire)

    def test_list_data(self):
        e = SSEEvent(data=[1, 2, 3])
        self.assertIn("data: [1,2,3]", e.format())

    def test_none_data_emits_data_colon(self):
        e = SSEEvent(data=None)
        self.assertIn("data: ", e.format())

    def test_field_order(self):
        e = SSEEvent(data="x", event="evt", id="99")
        wire = e.format()
        # Our format() emits in this order: comment, event, id, retry, data
        pos_evt = wire.index("event: evt")
        pos_id = wire.index("id: 99")
        pos_data = wire.index("data: x")
        self.assertLess(pos_evt, pos_id)
        self.assertLess(pos_id, pos_data)

    def test_format_sse_event_returns_bytes(self):
        e = SSEEvent(data="x")
        b = format_sse_event(e)
        self.assertIsInstance(b, bytes)
        self.assertEqual(b, e.format().encode("utf-8"))


class TestSSEResponse(unittest.TestCase):
    def test_headers(self):
        resp = sse_response(iter([]))
        self.assertEqual(
            resp.headers["content-type"], "text/event-stream; charset=utf-8"
        )
        self.assertEqual(resp.headers["cache-control"], "no-store")
        self.assertEqual(resp.headers["x-accel-buffering"], "no")
        self.assertEqual(resp.headers["connection"], "keep-alive")

    def test_iter_sse_events(self):
        def gen():
            yield SSEEvent(data={"i": 0}, event="tick")
            yield SSEEvent(data={"i": 1}, event="tick")
        resp = sse_response(gen())
        body = b"".join(iter(resp))
        self.assertIn(b"event: tick", body)
        self.assertIn(b'data: {"i":0}', body)
        self.assertIn(b'data: {"i":1}', body)

    def test_iter_strings(self):
        def gen():
            yield "data: hello" + chr(10)
            yield "data: world" + chr(10)
        resp = sse_response(gen())
        body = b"".join(iter(resp))
        self.assertIn(b"data: hello", body)
        self.assertIn(b"data: world", body)

    def test_iter_arbitrary_json(self):
        def gen():
            yield {"a": 1}
            yield [1, 2, 3]
        resp = sse_response(gen())
        body = b"".join(iter(resp))
        self.assertIn(b'data: {"a":1}', body)
        self.assertIn(b"data: [1,2,3]", body)

    def test_custom_headers_preserved(self):
        resp = sse_response(iter([]), headers={"X-Trace": "abc"})
        self.assertEqual(resp.headers["X-Trace"], "abc")
        # SSE headers are still set even if caller passed their own.
        self.assertIn("content-type", resp.headers)

    def test_keepalive_factory(self):
        e = sse_keepalive()
        self.assertIn(": keepalive", e.format())
        self.assertEqual(SSE_KEEPALIVE_INTERVAL, 15.0)

    def test_custom_keepalive(self):
        e = sse_keepalive(interval=5.0)
        self.assertIn(": keepalive", e.format())


class TestStreamingAnnotation(unittest.TestCase):
    def test_mark_and_check(self):
        app = Router()
        self.assertFalse(is_streaming_route(app, "GET", "/x"))
        mark_streaming(app, "GET", "/x")
        self.assertTrue(is_streaming_route(app, "GET", "/x"))

    def test_add_streaming_route(self):
        app = Router()
        add_streaming_route(app, "POST", "/upload")
        self.assertTrue(is_streaming_route(app, "POST", "/upload"))

    def test_install_decorator_marks(self):
        app = Router()
        install_streaming_decorator(app)
        @app.get("/events", streaming=True)
        def events(req):
            return {}
        self.assertTrue(is_streaming_route(app, "GET", "/events"))

    def test_install_decorator_idempotent(self):
        app = Router()
        install_streaming_decorator(app)
        install_streaming_decorator(app)  # should be no-op
        @app.get("/e", streaming=True)
        def e(req):
            return {}
        self.assertTrue(is_streaming_route(app, "GET", "/e"))

    def test_install_decorator_non_streaming(self):
        app = Router()
        install_streaming_decorator(app)
        @app.get("/normal")
        def normal(req):
            return {}
        self.assertFalse(is_streaming_route(app, "GET", "/normal"))

    def test_all_verbs_supported(self):
        for verb in ("get", "post", "put", "patch", "delete", "head", "options"):
            app = Router()
            install_streaming_decorator(app)
            decorator = getattr(app, verb)
            decorator("/x", streaming=True)(lambda req: {})
            self.assertTrue(
                is_streaming_route(app, verb.upper(), "/x"),
                f"{verb} did not mark streaming",
            )


class TestOpenAPIStreamingOverlay(unittest.TestCase):
    def _build_app(self):
        app = Router()
        install_streaming_decorator(app)
        @app.get("/events", streaming=True)
        def events(req):
            return {}
        @app.get("/normal")
        def normal(req):
            return {}
        @app.get("/users/{id}")
        def user(req, id):
            return {}
        return app

    def test_streaming_route_gets_text_event_stream(self):
        app = self._build_app()
        spec = openapi_spec(app, title="S")
        spec = openapi_spec_streaming_overlay(spec, app)
        content = spec["paths"]["/events"]["get"]["responses"]["200"]["content"]
        self.assertIn("text/event-stream", content)
        self.assertEqual(
            content["text/event-stream"]["schema"]["type"], "string"
        )

    def test_non_streaming_route_unchanged(self):
        app = self._build_app()
        spec = openapi_spec(app, title="S")
        spec = openapi_spec_streaming_overlay(spec, app)
        # The /normal route has no "content" key in 200 (openapi_spec
        # default emits only {"description": "OK"}).
        resp = spec["paths"]["/normal"]["get"]["responses"]["200"]
        self.assertNotIn("content", resp)

    def test_no_streaming_routes_no_op(self):
        app = Router()
        @app.get("/x")
        def x(req):
            return {}
        spec = openapi_spec(app, title="S")
        before = spec["paths"]["/x"]["get"]["responses"]["200"]
        spec2 = openapi_spec_streaming_overlay(spec, app)
        after = spec2["paths"]["/x"]["get"]["responses"]["200"]
        self.assertEqual(before, after)

    def test_path_param_route_works(self):
        app = Router()
        install_streaming_decorator(app)
        @app.get("/users/{id}/events", streaming=True)
        def user_events(req, id):
            return {}
        spec = openapi_spec(app, title="S")
        spec = openapi_spec_streaming_overlay(spec, app)
        # The public path in spec is the same as the registered pattern
        self.assertIn("text/event-stream",
                     spec["paths"]["/users/{id}/events"]["get"]["responses"]["200"]["content"])


class TestIntegration(unittest.TestCase):
    def test_full_app_lifecycle(self):
        """End-to-end: build a Router with a streaming endpoint and a
        normal endpoint, generate the OpenAPI spec, and confirm the
        content types are correct."""
        app = Router()
        install_streaming_decorator(app)

        @app.get("/events", streaming=True, tags=["stream"])
        def events(req):
            def gen():
                yield SSEEvent(data="a")
                yield SSEEvent(data="b")
            return sse_response(gen())

        @app.get("/health")
        def health(req):
            return {"ok": True}

        spec = openapi_spec(app, title="T", version="0.1.0")
        spec = openapi_spec_streaming_overlay(spec, app)

        # Streaming route has the SSE content type
        sse_content = spec["paths"]["/events"]["get"]["responses"]["200"]["content"]
        self.assertIn("text/event-stream", sse_content)

        # Non-streaming route has no content type
        self.assertNotIn(
            "content",
            spec["paths"]["/health"]["get"]["responses"]["200"],
        )

        # Tags are preserved
        self.assertEqual(spec["paths"]["/events"]["get"]["tags"], ["stream"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
