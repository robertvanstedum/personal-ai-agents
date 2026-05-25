# Pattern: 202 Async + Webhook Callback

## When to use
Any operation that takes more than ~2 seconds in a downstream system.
Line/service provisioning, account migrations, bulk operations.
Never block a partner on a synchronous call to a slow backend.

## The flow

```
Partner                  API Gateway           Platform Backend       Partner Webhook
  |                           |                       |                      |
  |-- POST /services -------->|                       |                      |
  |   Idempotency-Key: uuid   |                       |                      |
  |                           |-- validate + queue -->|                      |
  |<-- 202 Accepted ----------|                       |                      |
  |    correlationId          |                       |                      |
  |    statusUrl              |                       |                      |
  |                           |                  [processing]                |
  |                           |                       |                      |
  |   (optional polling)      |                       |                      |
  |-- GET /status/{id} ------>|                       |                      |
  |<-- 200 IN_PROGRESS -------|                       |                      |
  |                           |                       |                      |
  |                           |             [provisioning complete]          |
  |                           |                       |-- POST callback ---->|
  |                           |                       |   event: PROVISIONED |
  |                           |                       |   correlationId      |
  |                           |                       |<-- 200 ACK ----------|
```

## Key design decisions (TPM owns these)

**What events to surface externally**
Not every internal event should become a webhook. You decide what
partners can observe. Start minimal — add events as partners request them.

**Retry policy**
Standard: 3 attempts, exponential backoff (30s, 60s, 120s).
After 3 failures, mark webhook as SUSPENDED, notify partner.

**Payload signing**
HMAC-SHA256 signature in X-Signature header.
Partners verify on receipt to confirm payload authenticity.
You define the signing spec and document it for partners.

**Polling as fallback**
Always provide GET /status/{correlationId} alongside webhooks.
Some partners can't receive inbound HTTPS (firewalls, legacy infra).
Same backend serves both — no duplication.

**Idempotency window**
Cache idempotency keys for 24 hours minimum.
Partner retries within that window get the cached 202 response,
not a duplicate provisioning operation.

## What to say in an interview

"Provisioning takes time in BSS/OSS — you can't block the partner on
a synchronous call. The pattern is: accept the request immediately
with 202 and a correlationId, process async, then fire a webhook when
done. I design the webhook contract as a product artifact — what events
fire, the payload schema, the retry policy, and how partners register
their endpoint. I also always include a polling fallback for partners
who can't receive inbound webhooks."
