"""Example usage of tiny_router."""

from __future__ import annotations

from tiny_router import Request, Response, Router, serve

app = Router()


# Logging middleware
@app.use
def log_requests(req: Request, nxt):  # type: ignore[no-untyped-def]
    print(f"[tiny-router] {req.method} {req.path}")
    return nxt(req)


# Auth middleware example
@app.use
def auth(req: Request, nxt):  # type: ignore[no-untyped-def]
    # Public paths skip auth
    if req.path.startswith("/public"):
        return nxt(req)
    # Demo: any non-empty token works
    if not req.headers.get("authorization"):
        return Response({"error": "missing token"}, status=401)
    return nxt(req)


@app.get("/")
def home(req: Request) -> dict:
    return {"message": "Welcome to tiny-router"}


@app.get("/users/{id}")
def get_user(req: Request, id: str) -> dict:  # noqa: A002
    return {"id": int(id), "name": f"User {id}"}


@app.post("/users")
def create_user(req: Request) -> tuple:
    data = req.json or {}
    return {"created": True, "name": data.get("name", "anonymous")}, 201


@app.get("/orgs/{org}/repos/{repo}")
def get_repo(req: Request, org: str, repo: str) -> dict:
    return {"org": org, "repo": repo}


@app.get("/search")
def search(req: Request) -> dict:
    return {"q": req.query.get("q", [""])[0]}


@app.on_error(404)
def not_found(req: Request, exc: Exception) -> Response:  # noqa: ARG001
    return Response({"error": "missing", "path": req.path}, status=404)


if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=8000)
