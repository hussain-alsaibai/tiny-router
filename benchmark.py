"""Quick micro-benchmark for tiny_router."""

from __future__ import annotations

import time

from tiny_router import Router


def make_app() -> Router:
    app = Router()

    @app.get("/")
    def home(req):  # type: ignore[no-untyped-def]
        return {"hello": "world"}

    @app.get("/users/{id}")
    def user(req, id):  # noqa: A002  # type: ignore[no-untyped-def]
        return {"id": int(id)}

    return app


def main() -> None:
    app = make_app()
    iters = 20_000
    start = time.perf_counter()
    for i in range(iters):
        req_method = "GET"
        req_path = "/" if i % 2 == 0 else f"/users/{i}"
        # We can't easily call wsgi inline without a fake environ; use a stub.
        from io import BytesIO

        environ = {
            "REQUEST_METHOD": req_method,
            "PATH_INFO": req_path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": "0",
            "wsgi.input": BytesIO(b""),
            "wsgi.errors": BytesIO(),
            "wsgi.url_scheme": "http",
            "SERVER_NAME": "x",
            "SERVER_PORT": "80",
        }
        captured: list = []

        def start_response(status, headers):  # type: ignore[no-untyped-def]
            captured.append((status, headers))

        list(app.wsgi(environ, start_response))
    elapsed = time.perf_counter() - start
    print(f"{iters} requests in {elapsed:.3f}s → {iters / elapsed:,.0f} req/s")


if __name__ == "__main__":
    main()
