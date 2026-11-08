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

## Optional local attempt journal

Use a new local file to keep an attempt even if the client is interrupted:

```sh
python3 -m phoenix deliver-event --source http://127.0.0.1:8787 \
  --event-id EVENT_ID --destination http://127.0.0.1:9000/webhook \
  --journal ./deliveries.sqlite3
python3 -m phoenix delivery-history --journal ./deliveries.sqlite3 --limit 20
```

The parent commits an `unconfirmed` row before starting the network worker.
The row identifies this invocation, event, UTC start and worker hash, with
SHA-256 fingerprints of the source and destination URL strings. It stores no
URLs, tokens, request bodies or receiver response contents. Fingerprints are
comparison values, not encryption of guessable URLs. Files remain local and are
ignored by Git; they are never uploaded to the AI ledger automatically.

After the worker finishes, a second transaction stores a filtered receipt and
the row becomes `finished`. Its receipt can still say `delivery-unknown`.
Every journaled stdout receipt includes `journal_attempt_id` and `journal_saved`.
If final persistence fails, the observed HTTP result stays in stdout with
`journal_saved: false`, `journal_error: final-receipt-not-saved` and CLI exit 2.
That storage failure does not mean the receiver missed the event.

| Journal state | What is known |
| --- | --- |
| `unconfirmed`, receipt `null` | This invocation was recorded; the client may still be running or may have stopped before or after sending. |
| `finished` | A final client receipt was persisted; inspect its status and HTTP outcome. |

An interruption can leave a row unconfirmed even after a receiver accepted the
POST. History neither labels it failed nor retries it. Another explicit
`deliver-event` invocation creates another attempt and may deliver a duplicate.
This log has no queue, automatic recovery/resend or exactly-once guarantee.

The journal uses SQLite rollback mode with `synchronous=FULL` and a one-second
lock wait per connection. Journal setup and final persistence are separate from
the existing worker-wide network deadline. A new file has mode 0600 and is
initialized privately before exclusive hard-link publication; its directory must
already exist on a filesystem supporting same-directory hard links. Concurrent
first use cannot observe an empty published database. Existing file permissions
are not changed. This does not promise recovery from every OS/storage failure.

The client checks the raw SQLite identity, version and rollback-mode header
before opening an existing file with SQLite. Unrelated databases and WAL-mode
files are refused before HTTP use, avoiding recovery writes or WAL sidecar
creation during refusal. Unsupported, locked or unavailable journals prevent
the invocation from contacting either service. This concerns trusted local
files; it is not protection against adversarial path replacement.

History opens a supported journal read-only, returns the newest 1..100 attempts
(default 20), and never creates a missing file or contacts services. The journal
has no automatic deletion or size cap; manage its storage locally. The
[journal observations](maintenance/2026-11-08-delivery-journal.md) include actual
process interruption, competing clients, a failed final commit, unrelated hot
rollback journals and WAL refusal. The expanded smoke now records 21 checks;
the earlier 12-check reports retain their original sources and outcomes.
