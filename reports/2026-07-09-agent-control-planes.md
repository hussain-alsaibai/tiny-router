# Agent Control Planes With tiny-router

Date: 2026-07-09

## Trend

Developer tools are moving from one-off scripts toward small local control
planes: webhook receivers, health endpoints, action runners, and status pages
that agents can call during long workflows. Most of these services do not need
an ASGI stack, background worker framework, or application server dependency
tree. They need predictable routing, simple JSON, and a shape that is easy to
paste into a bounty repro or operations repo.

## Why tiny-router Fits

`tiny-router` gives agents and developers a single-file HTTP surface for:

- Receiving GitHub, Telegram, Stripe, or internal automation callbacks.
- Wrapping local scripts as HTTP actions without adding Flask or FastAPI.
- Exposing `/health`, `/status`, and `/metrics` style probes for cron jobs.
- Building reproducible exploit or regression harnesses with minimal noise.

The main appeal is auditability. A reviewer can read the router, middleware,
and handler path in minutes, which is useful when a proof of concept or internal
agent service needs trust more than framework reach.

## Recommended Pattern

```python
from tiny_router import Router, Response, serve

app = Router()

@app.use
def require_agent_token(req, nxt):
    token = req.headers.get("x-agent-token")
    if token != "local-dev-token":
        return Response({"error": "unauthorized"}, status=401)
    return nxt(req)

@app.get("/health")
def health(req):
    return {"ok": True, "service": "workflow-control"}

@app.post("/run/{task}")
def run_task(req, task):
    payload = req.json
    return {
        "task": task,
        "accepted": True,
        "dry_run": bool(payload.get("dry_run", True)),
    }, 202

if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=8080)
```

## Product Opportunities

- Add a first-party `examples/agent_control_plane.py` that combines auth,
  health checks, JSON responses, and error handlers.
- Publish a short benchmark against Flask cold-start and simple route latency.
- Add copy that positions `tiny-router` as "the router for local agent tools",
  not only a lightweight Flask alternative.

## Engagement Hooks

- "Build a local agent webhook in one file."
- "A readable HTTP control plane for scripts, crons, and bounties."
- "FastAPI is excellent for APIs. This is for the 40-line service you actually
  want to audit."
