"""v0.9.1 - the README's decorators have to exist.

Three things the README advertised were not in the class:

* ``@app.middleware``   -> AttributeError, only ``use()`` existed.
* ``@app.websocket``    -> AttributeError, only ``ws()`` existed.
* ``Response(401, {...})`` -> the parameters are ``(body, status)``, so the
  README's example produced body=``b'401'`` and
  status=``{'error': 'Unauthorized'}``. No exception, no test, and the
  response is nonsense at runtime.

Run as a script, this file re-executes the README's own middleware section.
"""

import unittest

from tiny_router import Request, Response, Router


def _req(method="GET", path="/x", headers=None):
    return Request(method, path, {}, headers or {}, b"")


class TestMiddlewareDecorator(unittest.TestCase):
    def test_middleware_decorator_registers(self):
        app = Router()
        calls = []

        @app.middleware
        def timing(req, next_handler):
            calls.append("before")
            return next_handler(req)

        @app.get("/x")
        def x(req):
            return {"ok": True}

        self.assertEqual(len(app._middlewares), 1)
        self.assertIs(app._middlewares[0], timing)

    def test_middleware_decorator_returns_the_function(self):
        app = Router()

        def mw(req, next_handler):
            return next_handler(req)

        self.assertIs(app.middleware(mw), mw)

    def test_middleware_runs_in_order(self):
        app = Router()
        seen = []

        @app.middleware
        def outer(req, next_handler):
            seen.append("outer-in")
            r = next_handler(req)
            seen.append("outer-out")
            return r

        @app.middleware
        def inner(req, next_handler):
            seen.append("inner-in")
            r = next_handler(req)
            seen.append("inner-out")
            return r

        @app.get("/x")
        def x(req):
            seen.append("handler")
            return {"ok": True}

        result = app._dispatch(_req())
        self.assertEqual(seen, ["outer-in", "inner-in", "handler",
                                "inner-out", "outer-out"])
        self.assertEqual(result.body, b'{"ok": true}')

    def test_middleware_can_mutate_the_response(self):
        app = Router()

        @app.middleware
        def timing(req, next_handler):
            resp = next_handler(req)
            resp.headers["X-Process-Time"] = "0.003s"
            return resp

        @app.get("/x")
        def x(req):
            return Response({"ok": True})

        result = app._dispatch(_req())
        self.assertEqual(result.headers.get("X-Process-Time"), "0.003s")


class TestWebsocketAlias(unittest.TestCase):
    def test_websocket_is_ws(self):
        app = Router()

        @app.websocket("/ws")
        async def echo(ws):
            pass

        self.assertEqual(len(app._ws_routes), 1)

    def test_ws_still_works(self):
        app = Router()

        @app.ws("/ws")
        async def echo(ws):
            pass

        self.assertEqual(len(app._ws_routes), 1)


class TestResponseArgumentOrder(unittest.TestCase):
    def test_documented_error_form_keeps_the_status(self):
        """Response(body, status=...) - the form that cannot be misread."""
        r = Response({"error": "Unauthorized"}, status=401)
        self.assertEqual(r.status, 401)

    def test_positional_form_is_body_then_status(self):
        r = Response({"error": "Unauthorized"}, 401)
        self.assertEqual(r.status, 401)

    def test_readme_middleware_section_runs_verbatim(self):
        """The exact block from README 'Advanced: Middleware Chaining'."""
        app = Router()

        @app.middleware
        def timing_middleware(req: Request, next_handler):
            resp = next_handler(req)
            resp.headers["X-Process-Time"] = "0.001s"
            return resp

        @app.middleware
        def auth_middleware(req: Request, next_handler):
            if req.headers.get("Authorization") != "Bearer secret":
                return Response({"error": "Unauthorized"}, status=401)
            return next_handler(req)

        @app.get("/protected")
        def protected(req):
            return {"secret": "data"}

        denied = app._dispatch(_req("/x", "/protected"))
        self.assertEqual(denied.status, 401, f"status was {denied.status!r}")

        allowed = app._dispatch(
            _req(path="/protected", headers={"Authorization": "Bearer secret"})
        )
        self.assertEqual(allowed.body, b'{"secret": "data"}')


if __name__ == "__main__":
    unittest.main()
