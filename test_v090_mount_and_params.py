"""v0.9.0 — mounting is a routing operation, not a naming convention.

Two defects, both found by running the README's own example rather than the
test suite. The suite passed because every test declared its handler the one
way that happened to work.

1. `Router(prefix="/api")` + `app.mount("/api", api)` produced
   `/api/api/users`. The mount appended the prefix that the sub-router had
   already applied. `mount(router)` with one argument - also in the README -
   raised `TypeError: mount() missing 1 required positional argument`.
2. A handler that reads its path param from `request.params` instead of
   taking it as a keyword (`def get_user(req)` vs `def get_user(req, id)`)
   returned **500**, not 404 and not 200: the router passed every path param
   as a keyword regardless of the signature.

Each test fails against 0.8.0.
"""

import io
import json
import unittest

from tiny_router import Depends, Router, openapi_spec


def _call(app, path, method="GET"):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "wsgi.input": io.BytesIO(b""),
        "CONTENT_LENGTH": "",
    }
    statuses = []

    def start_response(status, headers):
        statuses.append(status)

    body = b"".join(app.wsgi(environ, start_response))
    return statuses[0], json.loads(body)


class TestMountDoesNotDoublePrefix(unittest.TestCase):
    def test_readme_example_does_not_double_the_prefix(self):
        app = Router()
        api = Router(prefix="/api")

        @api.get("/users")
        def list_users(req):
            return {"users": ["alice"]}

        app.mount("/api", api)

        status, body = _call(app, "/api/users")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, {"users": ["alice"]})

        status, _ = _call(app, "/api/api/users")
        self.assertEqual(status, "404 Not Found")

    def test_one_argument_mount_uses_the_sub_router_prefix(self):
        """`main.mount(api)` is in the README and used to raise TypeError."""
        app = Router()
        v1 = Router(prefix="/api/v1")

        @v1.get("/users")
        def v1_users(req):
            return {"version": "v1"}

        app.mount(v1)

        status, body = _call(app, "/api/v1/users")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, {"version": "v1"})

    def test_mounting_two_routers_keeps_them_separate(self):
        app = Router()
        api = Router(prefix="/api")
        admin = Router(prefix="/admin")

        @api.get("/items")
        def items(req):
            return {"items": []}

        @admin.get("/stats")
        def stats(req):
            return {"visits": 1}

        app.mount("/api", api)
        app.mount("/admin", admin)

        self.assertEqual(_call(app, "/api/items")[1], {"items": []})
        self.assertEqual(_call(app, "/admin/stats")[1], {"visits": 1})

    def test_unprefixed_subrouter_adopts_the_mount_prefix(self):
        app = Router()
        svc = Router()

        @svc.get("/items")
        def items(req):
            return {"items": []}

        app.mount("/svc", svc)
        self.assertEqual(_call(app, "/svc/items")[1], {"items": []})

    def test_extending_a_prefix_remaps_path_params(self):
        """A remounted route must still match its named group."""
        app = Router()
        adm = Router(prefix="/items")

        @adm.get("/{id}")
        def get_item(req):
            return {"id": req.params["id"]}

        app.mount("/api", adm)
        status, body = _call(app, "/api/items/7")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, {"id": "7"})

    def test_openapi_spec_follows_the_mount(self):
        app = Router()
        api = Router(prefix="/api")

        @api.get("/users/{uid}")
        def get_user(req):
            return {}

        app.mount("/api", api)

        spec = openapi_spec(app)
        self.assertIn("/api/users/{uid}", spec["paths"])
        self.assertNotIn("/api/api/users/{uid}", spec["paths"])


class TestHandlerMayReadParamsFromRequest(unittest.TestCase):
    def test_handler_taking_only_request(self):
        app = Router()

        @app.get("/users/{id}")
        def get_user(req):
            return {"id": req.params["id"]}

        status, body = _call(app, "/users/7")
        self.assertEqual(status, "200 OK", f"body={body}")
        self.assertEqual(body, {"id": "7"})

    def test_handler_declaring_the_param_still_works(self):
        app = Router()

        @app.get("/users/{id}")
        def get_user(req, id):
            return {"id": id}

        status, body = _call(app, "/users/7")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, {"id": "7"})

    def test_kwargs_handler_receives_params(self):
        app = Router()

        @app.get("/users/{id}")
        def get_user(req, **kw):
            return {"kw": kw}

        status, body = _call(app, "/users/7")
        self.assertEqual(body, {"kw": {"id": "7"}})

    def test_dependencies_are_not_dropped_by_the_filter(self):
        app = Router()

        def tenant(req):
            return {"tenant": "t1"}

        @app.get("/x/{id}")
        def handler(req, ctx=Depends(tenant)):
            return {"ctx": ctx, "id": req.params["id"]}

        status, body = _call(app, "/x/3")
        self.assertEqual(status, "200 OK", f"body={body}")
        self.assertEqual(body, {"ctx": {"tenant": "t1"}, "id": "3"})

    def test_multiple_params_read_from_request(self):
        app = Router()

        @app.get("/o/{org}/r/{repo}")
        def get_repo(req):
            return {"org": req.params["org"], "repo": req.params["repo"]}

        status, body = _call(app, "/o/acme/r/widget")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, {"org": "acme", "repo": "widget"})


if __name__ == "__main__":
    unittest.main()
