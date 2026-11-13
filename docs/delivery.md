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

History opens a supported journal read-only, returns a page of 1..100 attempts
(default 20), and never creates a missing file or contacts services. The journal
has no automatic deletion or size cap; manage its storage locally. The
[journal observations](maintenance/2026-11-08-delivery-journal.md) include actual
process interruption, competing clients, a failed final commit, unrelated hot
rollback journals and WAL refusal. Those initial journal observations recorded 21 checks;
the earlier 12-check reports retain their original sources and outcomes.

## Optional destination policy

An invocation can require an explicit approved receiver list. Create the
ignored local directory with `mkdir -p .phoenix`, then save this UTF-8 JSON as
`.phoenix/destinations.json`:

```json
{
  "schema_version": 1,
  "destinations": ["http://127.0.0.1:9000/webhook"]
}
```

```sh
python3 -m phoenix deliver-event --source http://127.0.0.1:8787 \
  --event-id EVENT_ID --destination http://127.0.0.1:9000/webhook \
  --destination-policy .phoenix/destinations.json --journal ./deliveries.sqlite3
```

The file must be a regular file of at most 16,384 bytes, containing exactly
`schema_version` (integer 1) and `destinations` (1..16 distinct URL strings).
Every entry must satisfy the existing literal IPv4 loopback HTTP address rules,
including unused entries. Duplicate JSON keys, unknown fields, malformed JSON,
unsupported versions, duplicate URLs and unavailable/nonregular files are refused.
Directory and FIFO refusals release their file descriptors.

The destination argument must match one approved string exactly, including
port, path, query, escaping and an optional trailing slash. There are no
wildcards, prefix matching, URL normalization or hostname resolution rules.
The client still follows no redirects and sends at most one POST. These
additional restrictions never permit an address refused by the original client.

Validation happens before either HTTP operation or journal creation. Refusal
returns CLI exit 2 with a diagnostic that omits policy contents, file paths and
destination values; it creates no journal attempt and no AI ledger entry.
The option is per invocation. Omitting it retains the original explicit-URL
behavior; this is an operator guard, not account-wide or OS enforcement.

The policy is read once before the invocation starts its network work. Edits
after that capture affect subsequent invocations, not an in-progress POST.
Every returned final receipt includes `destination_policy_sha256`, the SHA-256
of exactly those captured bytes, including whitespace. A saved final journal
receipt retains the same field for delivered, rejected, source-error and
delivery-unknown outcomes. Policy URLs, paths and contents are not stored there.
Interrupted `unconfirmed` rows keep their prior format: they do not establish
which policy was used. History does not re-check policies or resend attempts.

Policy files are plaintext local configuration. The hash is a comparison value,
not encryption or an independent signature. As with the journal, trusted local
files are assumed; this does not defend against a malicious caller replacing
the code or choosing to omit the option. Captured bytes are bounded; arbitrary
filesystem I/O is outside the network worker's absolute deadline. The FIFO and
descriptor-lifetime observations use POSIX hosts. Delivery to external hosts,
durable queues and recovery resends remain future work.

See the [actual policy observations](maintenance/2026-11-09-destination-policy.md).
Those policy observations recorded 30 checks and froze the policy module and checks
alongside the existing source closure. Earlier 12/21-check reports remain intact.

## Continue delivery history

The first query returns the newest page with a `next_cursor` field:

```sh
python3 -m phoenix delivery-history --journal ./deliveries.sqlite3 --limit 20
python3 -m phoenix delivery-history --journal ./deliveries.sqlite3 --limit 20 \
  --cursor TOKEN_FROM_NEXT_CURSOR
```

Use the returned non-null token with the same journal to read older pages.
`next_cursor: null` means that this query found no additional row. Each page
returns at most 1..100 rows, default 20; an empty journal returns an empty list
and null cursor. Even an exact terminal page has no continuation token.

The order is `(started_at DESC, id DESC)`. Continuation uses a strict comparison
against the last visible pair, so equal timestamps use the ID tie-breaker and
records with newer keys do not shift subsequent pages. A read-only SELECT
fetches at most `limit + 1` rows to detect continuation. Sorting/scanning work
is not bounded by that returned-row limit; no query-latency guarantee is made.

Pages are not a transaction snapshot across invocations. Receipt finalization
can become visible on a later page without changing order. A late commit with
an older key can appear later; external timestamp edits/deletions or clock
changes are not hidden by a snapshot promise. History never freezes writers,
retries delivery, changes receipt states or writes the journal.

The token is unpadded URL-safe base64 of canonical compact ASCII JSON
`[1, started_at, id]`, at most 256 characters. The key is a printable ASCII
string of 1..64 characters and the ID is 32 lowercase hexadecimal characters.
Malformed types, unsupported versions, invalid/oversized text, noncanonical JSON
or base64, and padded tokens refuse before the journal is opened, with CLI exit
2 and a diagnostic omitting the token. The cursor contains only a key already
visible in local history. It is unsigned, unencrypted and not bound to a journal
identity; it grants no authorization and can be edited to choose a seek position.

Python callers can use `delivery_history_page(path, limit=20, cursor=None)` to
receive `{"attempts": [...], "next_cursor": ...}`. The existing
`delivery_history(path, limit=20)` still returns a list with the same newest-row
behavior. The CLI's `next_cursor` is additive. Database schema, supported file
mode, permission checks and missing-file behavior are unchanged.

The [pagination observations](maintenance/2026-11-10-history-pagination.md)
include complete traversal of 123 synthetic rows, timestamp ties, newer inserts,
finalization between pages and the legacy API. H05 instruments the database
boundary for invalid-input refusal; negative controls exercise canonical checks.
D01 also reads the actual AI-backed delivery receipt through the page API.
The expanded delivery smoke records 38 checks; old reports retain their bytes.

## Filter delivery history by state

```sh
python3 -m phoenix delivery-history --journal ./deliveries.sqlite3 --state unconfirmed
python3 -m phoenix delivery-history --journal ./deliveries.sqlite3 --state finished --limit 20
```

`unconfirmed` selects attempts awaiting a saved receipt. `finished` selects
attempts with any saved outcome, including `delivery-unknown` and `rejected`.
Omit `--state` to read both states.

The query filters rows before applying the page limit. Pass the same state
with `--cursor TOKEN` to continue that filtered list. Each read sees the journal
state at query time. An attempt finalized between pages leaves the unconfirmed
list. Python callers can pass `state="unconfirmed"` or `state="finished"` to
`delivery_history_page`.

## Filter delivery history by event ID

```sh
python3 -m phoenix delivery-history --journal ./deliveries.sqlite3 --event-id EVENT_ID --state finished
python3 -m phoenix delivery-history --journal ./deliveries.sqlite3 --event-id EVENT_ID \
  --state finished --cursor TOKEN_FROM_NEXT_CURSOR
```

Use the event ID returned by the inbox. SQLite matches it exactly and
case-sensitively against the stored `event_id` before applying the page limit.
If a journal contains attempts from different source origins with the same
event ID, the query returns matches from each origin. An absent match returns
an empty `attempts` list and a null cursor. The filter accepts the same event
IDs as `deliver-event`, and invalid values return exit code 2 before opening
the journal.

Repeat the same event and state filters when continuing a page. Python callers
can pass `event_id=EVENT_ID` to `delivery_history_page`.
[Event filter observations](maintenance/2026-11-13-history-event.md).

## Look up a delivery receipt

Use the `journal_attempt_id` from `deliver-event` with the same journal file.

```sh
python3 -m phoenix delivery-receipt --journal ./deliveries.sqlite3 --attempt-id ID
```

The lookup reads one row by its primary key. A found attempt has the same
fields as a history row. Its state is `unconfirmed` with `receipt: null` while
the final receipt is pending, or `finished` with the stored receipt. Read
`receipt.status` on a finished attempt to determine the delivery outcome.

| Exit | Meaning |
| --- | --- |
| 0 | The output contains the matching record under `attempt`. |
| 1 | The journal has no matching row. Output contains `attempt: null`. |
| 2 | The identifier is malformed or the journal could not be read. A diagnostic goes to stderr. |

IDs contain 32 lowercase hexadecimal characters. Validation precedes journal
access. Python callers can use `delivery_attempt(path, identifier)` to retrieve
the record or `None`. Both entry points use read-only access.
