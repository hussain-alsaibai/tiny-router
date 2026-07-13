# Agent Callback Recipe Shipped With tiny-router

Date: 2026-07-13

## Trend

Async agent workflows need a callback receiver that is **boring on purpose**:
shared-token auth, signature verification, payload validation, dedupe with a
TTL, rate limiting, structured logs, and a `/status` endpoint. When that
receipt is missing, "the webhook worked sometimes" becomes a recurring source
of incidents. When it is present and copy-pasteable, agents become much easier
to wire into cron, GitHub Actions, payment providers, and CI.

The piece that is *not* a library is the recipe: a single file that wires
`tiny-router`, `tiny-validator`, `fast-cache`, `tiny-log`, and `tiny-rate`
together into a service an operator can read in one screen.

## What Shipped

- `examples/agent_callback_receiver.py` — a 250-LOC reference service that
  implements auth + signature + validation + dedupe + rate-limit + log
  correlation, exposes `/health`, `/ready`, and `/status`, and runs against
  the Python standard library only.
- `examples/test_agent_callback_receiver.py` — 11 unit tests covering happy
  path, dedupe by delivery ID, dedupe by body hash, token enforcement,
  payload validation, malformed JSON, and rate-limit isolation between
  sources. All tests run without a real socket.
- CI extended to run both `test_tiny_router.py` and the new example tests on
  Python 3.8, 3.11, and 3.12.

## Why tiny-router Is the Right Anchor

`tiny-router` is the smallest credible HTTP layer for these services. The
example deliberately inlines tiny-validator / fast-cache / tiny-log /
tiny-rate behaviour in-place so it runs even when those libraries are not
installed; once a team adopts them, the inlined helpers drop away and the
recipe becomes a one-import wiring diagram.

The receiver keeps every request log line correlated by `x-request-id`,
returns structured JSON errors with the same ID, and exposes status counts
that an operator can read over SSH or paste into a status post.

## Recommended Pattern

```python
from tiny_router import Router, Response, serve

app = Router()

@app.use
def require_token(req, nxt):
    if req.headers.get("x-callback-token") != "local-dev-token":
        return Response({"error": "unauthorized"}, status=401)
    return nxt(req)

@app.post("/callbacks/{provider}")
def callback(req, provider):
    delivery_id = req.headers.get("x-delivery-id")
    # … validate, dedupe, log, return 202 …
    return {"ok": True, "accepted": True, "provider": provider}, 202

@app.get("/health")
def health(req):
    return {"ok": True}

serve(app, host="127.0.0.1", port=8088)
```

The full reference adds validation, dedupe with TTL, sliding-window rate
limits per source, structured logs, and a `/status` endpoint — but the
10-line version above is enough to retire the worst category of webhook
script.

## Engagement Hooks

- "Async agent work needs a callback receiver you can audit in one screen."
- "The boring webhook setup is the missing piece between cron jobs and
  autonomous agents."
- "Read the recipe, drop in the tiny-* libraries, ship the service today."