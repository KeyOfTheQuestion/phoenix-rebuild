# Phoenix contract 002: explicit webhook retention and v1 migration

Version: 2.0.0 (reference/acceptance pilot; not yet an AI-ledger contract). This document is the complete implementation input.

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
200: `{"status":"ok","schema_version":2}`.

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

## Atomic schema migration

Keep the v1 `inboxes` and `events` schemas above unchanged. On startup, migrate
fresh or valid v1 databases with `PRAGMA user_version` 0 or 1 to version 2 in one
transaction. Existing version 2 startup is idempotent. Reject other versions
without modifying their schema or data. Do not drop, rebuild or clear the v1
tables, change inbox/event IDs, replace token hashes, or lose bodies, headers,
timestamps or idempotency keys. Never expire events during startup.
Use named columns when writing existing tables; an equivalent v1 column order
must not require rebuilding those tables or moving their data.

Add this table and set `PRAGMA user_version=2` in the same transaction:

```sql
CREATE TABLE IF NOT EXISTS retention_policies (
  inbox_id TEXT PRIMARY KEY REFERENCES inboxes(id),
  max_age_seconds INTEGER NOT NULL CHECK(max_age_seconds BETWEEN 1 AND 2592000)
);
```

An absent row disables retention. This applies to migrated and newly created
inboxes, so migration never opts existing users into deletion. The additive table
preserves the v1 column layouts, but downgrades to a v1 program are unsupported:
a v1 program cannot enforce these policies or recover deleted events.

## GET /api/inboxes/{inbox_id}/retention

Require the same admin authentication as every `/api/` route, before looking up
the resource. Return 404 for an unknown inbox, otherwise 200:
`{"max_age_seconds":null}` when disabled, or `{"max_age_seconds":N}`.

## PUT /api/inboxes/{inbox_id}/retention

Require admin authentication. Accept exactly one JSON object key:
`{"max_age_seconds":N}`, where N is a JSON integer 1..2592000 inclusive, or null
to disable retention. Reject missing/extra keys, booleans, floats, strings,
arrays, malformed JSON and out-of-range integers with 400. Unknown inbox: 404.
Commit before returning 200 with the same policy object. Persist across restart.
Enabling or changing a policy must not itself delete or hide any event.
Unsupported methods on both retention endpoints return JSON 405.

## POST /api/inboxes/{inbox_id}/expire

Require admin authentication. Unknown inbox: 404. Accept an empty body or a JSON
object containing only optional `limit`, an integer 1..100 (default 100).
Booleans, floats, null, missing-value strings, malformed JSON and extra keys are
400. Other methods are JSON 405. Read the current policy and delete a batch in
one write transaction, serialized with other writers.

Capture current UTC time once for the batch. An event is eligible when its
`received_at` instant is at or before current time minus `max_age_seconds`.
Delete only that inbox's oldest eligible events, ordered by received instant and
then ID ascending, up to `limit`. Compare instants consistently for UTC ISO 8601
`Z` and `+00:00` timestamps. Disabled policy: delete zero events. Commit before
returning 200: `{"deleted":N}` with the actual number removed.

There is no implicit deletion or read-time hiding. GETs, startup, policy updates
and webhook reception do not expire data. Expiration removes the old event's
idempotency entry: a later delivery using that key creates a new event. Repeated
batches are safe; concurrent expiry requests must not double-count deletions.
Other inboxes and recent events remain intact. The 100-event bound limits rows
deleted per request; it is not a wall-clock or full-database scan guarantee.

## Pilot boundaries and verification

One administrator; no tenant authorization, background scheduler, outbound
webhook delivery, production deployment or deleted-data recovery. Use synthetic
data and a disposable local database. The reference implementation is a
AI-assisted development baseline, not an independent AI rebuild.

`verify-retention` is a focused migration/retention acceptance pilot. Migration
fixtures are written by a real v1 predecessor over HTTP and checked before any
aging fixture is introduced. Subsequent expiry checks explicitly backdate only
synthetic test events via SQLite while the subject is stopped, then issue real
HTTP requests. That is fixture aging, not proof of elapsed real-world retention.
The full v1 acceptance suite and the public reconstruction ledger stay on
contract 001. Integrating a complete v2 reconstruction/harness archive remains a
follow-up; do not label this pilot as a new AI reconstruction result.
