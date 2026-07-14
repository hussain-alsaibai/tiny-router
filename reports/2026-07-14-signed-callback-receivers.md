# Signed Callback Receivers With tiny-router: July 2026 Field Note

Autonomous developer tools increasingly depend on callbacks: GitHub webhooks, CI notifications, payment events, browser automation completions, and internal job updates. The callback receiver is often tiny, local, and boring until it becomes the path where untrusted input enters an agent workflow.

The emerging developer need is a receiver that is small enough to embed but still has the safety basics: signature checks, replay protection, stable JSON errors, and health endpoints.

## Trend Signals

- **More tools call agents back.** Long-running jobs and hosted automation now report completion through webhooks instead of polling.
- **Local callback servers are common.** Developers run receivers inside containers, tunnels, and cron-adjacent services where a full web stack is unnecessary.
- **Replay is the quiet failure mode.** A valid old delivery can trigger duplicate work unless the receiver tracks timestamps and delivery IDs.
- **Status endpoints reduce blind spots.** Operators need `/health`, `/ready`, and `/status` before debugging tunnels, DNS, or provider retries.
- **Structured errors matter.** Webhook providers retry differently based on status codes, so receivers need predictable 401, 409, 422, and 500 responses.

## What Developers Need

1. HMAC verification over the exact raw request body.
2. Timestamp tolerance to reject old signed payloads.
3. TTL-based delivery ID dedupe.
4. Schema validation before dispatch.
5. Lightweight status endpoints that expose readiness without leaking secrets.

## Fit For `tiny-router`

`tiny-router` gives enough HTTP structure for this job without forcing an ASGI stack into a small agent service. Middleware can bind request IDs, route handlers can stay explicit, and JSON responses are first-class.

Recommended near-term additions:

- Promote `examples/agent_callback_receiver.py` as the canonical webhook starter.
- Add a compact HMAC middleware recipe.
- Document status-code choices for rejected callbacks.
- Pair examples with `tiny-validator`, `fast-cache`, `tiny-log`, and `tiny-idempotency`.

## Example Shape

```python
@app.post("/callbacks/job")
def job_callback(req):
    verify_signature(req.body, req.headers)
    delivery_id = req.headers.get("x-delivery-id")
    if seen_deliveries.get(delivery_id):
        return {"ok": True, "duplicate": True}, 409

    payload = callback_schema(req.json)
    seen_deliveries.set(delivery_id, True, ttl=900)
    dispatch_job_update(payload)
    return {"ok": True}
```

## OpenClaw Workflow Relevance

OpenClaw already relies on webhook-style handoffs for automation and status reporting. A tiny signed receiver pattern makes those handoffs easier to audit, safer to expose through tunnels, and simpler to reuse in bounty repro services.
