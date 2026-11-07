# Loopback event delivery pilot

The client reads one stored event through its inbox HTTP API and sends its
original UTF-8 body to an explicitly selected IPv4 loopback HTTP receiver.
This implements the first bounded delivery experiment in roadmap item 8.

```sh
export PHOENIX_ADMIN_TOKEN="the-admin-token-used-by-your-local-inbox"
python3 -m phoenix deliver-event --source http://127.0.0.1:8787 \
  --event-id EVENT_ID --destination http://127.0.0.1:9000/webhook --timeout 3
```

Both services must already be running. Every invocation makes at most one POST.
The source must be an origin with an explicit port. The destination can include
a path and query. Both require the literal host `127.0.0.1`, the `http` scheme,
a port from 1 to 65535 and an ASCII URL of at most 2048 characters. Credentials
and fragments in URLs are refused before network use.

The admin token is read from the environment and passed to a temporary worker
through stdin. Only the source GET receives Authorization. The POST copies
captured `content-type`, `x-webhook-id` and `x-event-type` when present and adds
`X-Phoenix-Event-Id`. Transport headers are generated for the receiver.
Header values containing control characters are refused.

| Resource | Limit |
| --- | --- |
| Source event JSON | 512 KiB |
| UTF-8 body | 65,536 bytes |
| Copied header names and values | 8,192 characters, Latin-1 |
| Receiver response inspection | 8,192 bytes plus one lookahead byte |
| Overall worker deadline | 0.1..10 seconds, default 3 |
| Forced worker cleanup | Up to 5 additional seconds |
| Destination POST attempts | At most 1 per invocation |

The worker validates the event ID, body type and SHA-256 before sending.
The parent enforces an overall deadline across worker startup, source GET and
receiver POST, including slow streams. Response contents are discarded after
the bounded read. Receipts contain counts and digests rather than response
bodies or credentials. Redirects are recorded directly. The client performs no
automatic retry and uses a new connection for the receiver.

| Receipt status | Meaning | CLI exit |
| --- | --- | --- |
| `delivered` | A receiver response in the 200..299 range was read | 0 |
| `rejected` | The receiver returned another HTTP status | 1 |
| `source-error` | Fetching or validating the event failed before a POST started | 2 |
| `delivery-unknown` | A POST started but its response could not be established | 2 |

Before attempting the POST, the worker flushes a phase marker. The parent
preserves an observed marker through timeouts or partial results, and validates
attempt counts and HTTP status against the claimed receipt. A missing response
after POST begins leaves an uncertain outcome. Calling the command again may
send the event again. Receiver-side idempotency remains the receiver's responsibility.

The worker source is captured into a temporary file and its SHA-256 appears in
the receipt. Backend reconstruction uses contracts 001/002. Delivery receipts
are separate client observations with no model call or AI-ledger row. This
pilot is for trusted local development services. Delivery to other hosts and a
durable outbox need a subsequent destination policy and contract.

`tests/test_delivery.py` uses independent HTTP receivers and an actual inbox.
It checks raw bytes, metadata, credential isolation, redirects, receiver failure,
malformed/oversized sources, refused inputs, deadlines, uncertain outcomes,
response limits, CLI exits and body boundaries. A separate control injects
inconsistent and partial worker messages.

`tests/delivery_smoke.py` runs those controls using the latest successful v2 AI
inbox. Its `DELIVERY_EVIDENCE=` report identifies the source record, runtime
and hashes of the current worker, HTTP helper, inbox, checks, runner, CLI and
record support. CI and Pages run it without model credentials. Historical
checkout observations can be reproduced from their commit and source snapshots.
See the [first observations](maintenance/2026-11-07-loopback-delivery.md).
