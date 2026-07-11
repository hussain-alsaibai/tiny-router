# Agent Webhook Hardening With tiny-router

Date: 2026-07-11

## Trend

Agent platforms are adding more webhooks for long-running tasks, PR checks,
browser sessions, and payment or bounty events. The risk is that many local
webhook receivers start as quick scripts and never get the minimum operational
shape: authentication, idempotency, health checks, structured errors, and a
clear dry-run path.

Developers need tiny receivers that are easy to inspect and easy to throw away.
For bounty work, the receiver often needs to live inside a reproduction repo
without asking reviewers to install a framework.

## Why tiny-router Fits

`tiny-router` is a good base for hardened local webhook receivers because the
entire request path can stay visible in one file:

- Middleware can enforce a shared token, local-only IP policy, or signature
  check before handlers run.
- Path parameters make provider-specific routes readable without decorator
  magic.
- Error handlers can return consistent JSON for rejected callbacks.
- The stdlib server is enough for local tunnels, CI repros, and agent control
  surfaces.

The important product message is not "replace Flask". It is "make the unsafe
40-line webhook receiver reviewable".

## Recommended Pattern

```python
from tiny_router import Router, Response, serve

app = Router()
seen = set()

@app.use
def require_token(req, nxt):
    if req.headers.get("x-agent-token") != "dev-token":
        return Response({"error": "unauthorized"}, status=401)
    return nxt(req)

@app.post("/webhooks/{source}")
def webhook(req, source):
    delivery_id = req.headers.get("x-delivery-id")
    if delivery_id in seen:
        return {"ok": True, "duplicate": True}
    seen.add(delivery_id)
    return {"ok": True, "source": source}, 202

@app.get("/health")
def health(req):
    return {"ok": True}

if __name__ == "__main__":
    serve(app, host="127.0.0.1", port=8080)
```

## Product Opportunities

- Add an example that pairs `tiny-router`, `tiny-validator`, and
  `tiny-idempotency` for signed webhook ingestion.
- Document a "local tunnel checklist" for agent callbacks: token, duplicate
  detection, `/health`, and JSON error shape.
- Add a copy block aimed at bounty repro authors: "ship the vulnerable flow and
  the callback receiver in one readable file."

## Engagement Hooks

- "The webhook receiver should be easier to audit than the event it handles."
- "A local agent callback server without a framework dependency tree."
- "Harden the 40-line webhook script before it becomes production."

