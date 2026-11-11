"""Local attempt history. Unconfirmed records never authorize another POST."""
from contextlib import contextmanager
import base64
import binascii
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid


APPLICATION_ID = 0x50484A31
RECEIPT_FIELDS = {
    "schema_version", "kind", "event_id", "started_at", "duration_seconds", "timeout_seconds",
    "worker_sha256", "status", "attempts", "http_status", "error", "body_sha256", "request_bytes",
    "response_bytes_observed", "response_truncated",
    "destination_policy_sha256",
}


class JournalError(RuntimeError):
    pass


def initialize(path):
    descriptor, name = tempfile.mkstemp(prefix=".phoenix-journal-", dir=str(path.parent))
    os.close(descriptor)
    temporary = Path(name)
    connection = None
    try:
        connection = sqlite3.connect(str(temporary), timeout=1)
        connection.execute("PRAGMA synchronous=FULL")
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE delivery_attempts (id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
                               "event_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, destination_sha256 TEXT NOT NULL, "
                               "worker_sha256 TEXT NOT NULL, receipt_json TEXT)")
            connection.execute("PRAGMA application_id=%s" % APPLICATION_ID)
            connection.execute("PRAGMA user_version=1")
        connection.close()
        connection = None
        # Publish only a complete database. Competing creators never replace it.
        try:
            os.link(str(temporary), str(path))
        except FileExistsError:
            pass
    finally:
        if connection is not None:
            connection.close()
        temporary.unlink()


def check_header(path):
    if not path.is_file():
        raise JournalError("A regular Phoenix delivery journal is required")
    with path.open("rb") as handle:
        header = handle.read(100)
    # Check identity before SQLite can recover an unrelated hot journal. Refuse
    # WAL before even a read-only SQLite open can create -wal/-shm sidecars.
    if len(header) != 100 or header[:16] != b"SQLite format 3\x00" \
            or int.from_bytes(header[68:72], "big") != APPLICATION_ID \
            or int.from_bytes(header[60:64], "big") != 1:
        raise JournalError("Not a supported Phoenix delivery journal")
    if header[18:20] != b"\x01\x01":
        raise JournalError("Delivery journal requires SQLite rollback mode")


@contextmanager
def database(path, create=False, readonly=False):
    connection = None
    try:
        path = Path(path).absolute()
        if create and not path.exists():
            initialize(path)
        check_header(path)
        uri = path.as_uri() + ("?mode=ro" if readonly else "?mode=rw")
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        if not readonly:
            connection.execute("PRAGMA synchronous=FULL")
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID \
                or connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise JournalError("Not a supported Phoenix delivery journal")
        columns = [row[1] for row in connection.execute("PRAGMA table_info(delivery_attempts)")]
        if columns != ["id", "started_at", "event_id", "source_sha256", "destination_sha256", "worker_sha256", "receipt_json"]:
            raise JournalError("Invalid delivery journal schema")
        yield connection
    except (OSError, sqlite3.Error, ValueError, TypeError) as error:
        raise JournalError("Delivery journal unavailable: " + type(error).__name__) from error
    finally:
        if connection is not None:
            connection.close()


def start_attempt(path, metadata):
    identifier = uuid.uuid4().hex
    with database(path, create=True) as connection, connection:
        connection.execute("INSERT INTO delivery_attempts VALUES (?, ?, ?, ?, ?, ?, NULL)",
                           (identifier, metadata["started_at"], metadata["event_id"], metadata["source_sha256"],
                            metadata["destination_sha256"], metadata["worker_sha256"]))
    return identifier


def finish_attempt(path, identifier, receipt):
    filtered = {key: value for key, value in receipt.items() if key in RECEIPT_FIELDS}
    with database(path) as connection, connection:
        updated = connection.execute("UPDATE delivery_attempts SET receipt_json=? WHERE id=? AND receipt_json IS NULL",
                                     (json.dumps(filtered, ensure_ascii=True, sort_keys=True), identifier))
        if updated.rowcount != 1:
            raise JournalError("Attempt missing or already finished")


def decode_history_cursor(cursor):
    try:
        if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", cursor):
            raise ValueError
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        position = json.loads(raw.decode("ascii"))
        if not isinstance(position, list) or len(position) != 3 or type(position[0]) is not int or position[0] != 1 \
                or not isinstance(position[1], str) or not 1 <= len(position[1]) <= 64 \
                or any(not 32 <= ord(c) <= 126 for c in position[1]) \
                or not isinstance(position[2], str) or not re.fullmatch(r"[0-9a-f]{32}", position[2]):
            raise ValueError
        if json.dumps(position, separators=(",", ":"), ensure_ascii=True).encode("ascii") != raw \
                or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != cursor:
            raise ValueError
        return position[1], position[2]
    except (ValueError, TypeError, binascii.Error, RecursionError) as error:
        raise JournalError("Invalid delivery history cursor") from error


def _attempt_record(row):
    item = dict(row)
    receipt_json = item.pop("receipt_json")
    if receipt_json is None:
        item.update(state="unconfirmed", receipt=None)
    else:
        item.update(state="finished", receipt=json.loads(receipt_json))
    return item


def delivery_attempt(path, identifier):
    """Read one attempt by its journal ID."""
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise JournalError("Invalid delivery attempt ID")
    with database(path, readonly=True) as connection:
        row = connection.execute("SELECT * FROM delivery_attempts WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            return None
        return _attempt_record(row)


def delivery_history_page(path, limit=20, cursor=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise JournalError("History limit must be an integer from 1 to 100")
    position = decode_history_cursor(cursor) if cursor is not None else None
    with database(path, readonly=True) as connection:
        clause = " WHERE (started_at, id) < (?, ?)" if position is not None else ""
        parameters = (*position, limit + 1) if position is not None else (limit + 1,)
        rows = connection.execute("SELECT * FROM delivery_attempts" + clause +
                                  " ORDER BY started_at DESC, id DESC LIMIT ?", parameters).fetchall()
        result = [_attempt_record(row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = result[-1]
            raw = json.dumps([1, last["started_at"], last["id"]], separators=(",", ":"), ensure_ascii=True).encode("ascii")
            next_cursor = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
            decode_history_cursor(next_cursor)
        return {"attempts": result, "next_cursor": next_cursor}


def delivery_history(path, limit=20):
    return delivery_history_page(path, limit)["attempts"]
