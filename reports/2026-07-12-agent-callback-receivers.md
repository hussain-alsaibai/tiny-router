# Agent Callback Receivers With tiny-router

Date: 2026-07-12

## Trend

Agent platforms increasingly split work across asynchronous boundaries: browser
sessions, background code agents, CI jobs, payment events, task queues, and
human approval callbacks. Developers need a local receiver that can accept a
callback, verify it, dedupe it, and expose health without becoming a full web
application.

The common failure mode is a quick webhook script that grows just enough risk
to matter: no auth, no idempotency, no payload contract, and no status endpoint.

## Why tiny-router Fits

`tiny-router` is a good foundation for callback receivers because the routing,
middleware, and response path stay small enough to audit:

- Middleware can enforce a shared token or local tunnel secret.
- Routes can separate `/callbacks/{provider}` from `/health` and `/status`.
- Handlers can return structured JSON without framework setup.
- The whole receiver can live in a bounty repro or internal automation repo.

The most useful message is practical: make async agent callbacks boring,
visible, and easy to delete when the workflow changes.

## Recommended Pattern

```python
from tiny_router import Router, Response, serve

app = Router()
seen = set()

@app.use
def require_callback_token(req, nxt):
    if req.headers.get("x-callback-token") != "local-dev-token":
        return Response({"error": "unauthorized"}, status=401)
    return nxt(req)

@app.post("/callbacks/{provider}")
def callback(req, provider):
    delivery_id = req.headers.get("x-delivery-id")
    if delivery_id in seen:
        return {"ok": True, "duplicate": True, "provider": provider}
    seen.add(delivery_id)
    return {"ok": True, "accepted": True, "provider": provider}, 202

@app.get("/health")
def health(req):
    return {"ok": True}

if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=8080)
```

## Product Opportunities

- Add an `examples/agent_callback_receiver.py` example.
- Pair the example with `tiny-validator` for payload validation and
  `fast-cache` for TTL-based idempotency.
- Document a local tunnel checklist: token, delivery ID, `/health`, structured
  errors, and dry-run mode.

## Engagement Hooks

- "Async agent work needs a callback receiver you can audit in one screen."
- "Webhook glue should not become your next framework migration."
- "Receive the event, validate it, dedupe it, and move on."
