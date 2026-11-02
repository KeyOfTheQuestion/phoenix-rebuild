# Phoenix contract 001: a durable webhook inbox

Version: 1.0.0. This document is the complete implementation input.

Build a useful HTTP service that receives webhooks, preserves their original
UTF-8 bodies, and lets an administrator inspect them. Use only the Python 3.9+
standard library. Deliver one self-contained `server.py`. No other source files,
templates, package installation, network services, or third-party libraries.

## Startup and storage contract

Run with `python3 server.py`. Bind to `127.0.0.1` on the integer `PORT`
environment variable (default 8787). `PHOENIX_DB_PATH` is the SQLite path
(default `phoenix.sqlite`). `PHOENIX_ADMIN_TOKEN` is required; fail at startup
if it is missing or empty. Do not log request bodies, tokens, or incoming paths.
Support simultaneous requests, with a separate SQLite connection per request
and a reasonable busy timeout. Commit successful writes before responding.

Open existing data without dropping, clearing, or replacing it. The exact v1
schema below is a compatibility contract, including column names and types:

```sql
CREATE TABLE IF NOT EXISTS inboxes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  token_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  inbox_id TEXT NOT NULL REFERENCES inboxes(id),
  external_id TEXT,
  received_at TEXT NOT NULL,
  headers_json TEXT NOT NULL,
  body TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  UNIQUE(inbox_id, external_id)
);
```

Use `PRAGMA foreign_keys=ON`. IDs are unpredictable, opaque strings. Creation
times are UTC ISO 8601 strings. Inbox tokens contain at least 32 random bytes;
store only their SHA-256 hex digest, never the plaintext token.

## Response conventions

All responses are UTF-8 JSON, including errors, with a JSON content type.
Errors contain an `error` string. Unknown resources return 404. Reject unsupported
HTTP methods with JSON 405. Never expose stack traces. Reject bodies over 65,536
bytes with 413, malformed/missing Content-Length with 400, negative lengths with
400, and any Transfer-Encoding with 400. A missing Content-Length is permitted
on bodyless POSTs as a zero-byte body. Close the connection after rejecting a
body. Reject invalid UTF-8 with 400. Do not send permissive CORS headers.

Every `/api/` route requires exactly `Authorization: Bearer <admin-token>`.
Use a constant-time comparison. Missing or incorrect credentials return 401
before querying resources. Health and token-protected hook reception are public.

## Endpoints

### GET /health
200: `{"status":"ok","schema_version":1}`.

### POST /api/inboxes
Accept a JSON object with a `name` string, trimmed length 1..80. Reject other
JSON types and invalid names with 400. Return 201 with the newly created inbox:
`{"id":"...","name":"...","created_at":"...","token":"..."}`.
This is the only response that contains the plaintext inbox token.

### GET /api/inboxes
200: `{"inboxes":[{"id":"...","name":"...","created_at":"..."}]}`.
Order by `created_at DESC, id DESC`. Never return token hashes or tokens.

### POST /hooks/{inbox_id}/{token}
Check that the inbox exists and the token hash matches. Both a bad token and an
unknown inbox return 404. Accept any valid UTF-8 body (including empty text and
non-JSON). Capture only these request headers, with lowercase names:
`content-type`, `x-webhook-id`, `x-event-type`. Ignore all other headers,
especially cookies and authorization. Do not persist the credential-bearing URL.

Return 201: `{"id":"<event-id>","duplicate":false}`.

`X-Webhook-Id`, when nonempty, is an idempotency key scoped to the inbox. A
repeated key and identical body return 200 with the original event id and
`"duplicate":true`. A repeated key with a different body returns 409. This must
also hold when multiple identical requests arrive concurrently, and after restart.
Requests without a key are separate events even when their bodies are identical.

### GET /api/inboxes/{inbox_id}/events?limit=20
200: `{"events":[{"id":"...","inbox_id":"...","external_id":null,
"received_at":"...","body_sha256":"..."}]}`. `external_id` is a string
when a nonempty idempotency key was supplied, otherwise JSON null. Order by
`received_at DESC, id DESC`. A missing inbox is 404. The optional `limit` must
occur at most once and be a decimal integer between 1 and 100 inclusive;
otherwise return 400. Default: 20. Lists intentionally omit body and headers.

### GET /api/events/{event_id}
200: the six event summary fields above, plus `"body":"<exact original text>"`
and `"headers":{...}`. Return 404 for a missing event.

## Deliberate v1 boundaries

One administrator; no multi-tenant authorization claim. No outbound webhook
delivery or replay, account signup, deletion, or production deployment. This is
a local development inbox and reconstruction subject. Public demonstrations use
synthetic webhook bodies. A future contract may add delivery and migrations.
