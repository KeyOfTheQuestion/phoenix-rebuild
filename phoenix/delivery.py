"""Explicit, single-attempt delivery between IPv4 loopback HTTP services."""
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit


MAX_EVENT_JSON = 524288
MAX_BODY = 65536
MAX_HEADERS = 8192
MAX_RESPONSE = 8192
FORWARDED_HEADERS = {"content-type", "x-webhook-id", "x-event-type"}


class DeliveryError(ValueError):
    pass


def endpoint(value, source=False):
    if not isinstance(value, str) or not value.isascii() or len(value) > 2048 or "#" in value or any(
        ord(c) <= 32 or ord(c) == 127 for c in value
    ):
        raise DeliveryError("Use an explicit IPv4 loopback HTTP URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise DeliveryError("Invalid delivery URL") from error
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.netloc != "127.0.0.1:%s" % port \
            or port is None or not 1 <= port <= 65535:
        raise DeliveryError("Use http://127.0.0.1:PORT with an explicit port")
    if source and (parsed.path not in ("", "/") or parsed.query):
        raise DeliveryError("The inbox source must be a loopback origin")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return port, path


def safe_header(value):
    return isinstance(value, str) and all(32 <= ord(c) <= 255 and ord(c) != 127 for c in value)


def valid_result(result, post_started):
    if not isinstance(result, dict) or type(result.get("attempts")) is not int \
            or result["attempts"] != int(post_started):
        return False
    status = result.get("status")
    http_status = result.get("http_status")
    if status in ("delivered", "rejected"):
        if not post_started or type(http_status) is not int or not 100 <= http_status <= 599:
            return False
        return (status == "delivered") == (200 <= http_status < 300)
    if http_status is not None:
        return False
    return status == ("delivery-unknown" if post_started else "source-error")


def worker(payload):
    source_port, _ = endpoint(payload["source"], source=True)
    destination_port, path = endpoint(payload["destination"])
    receipt = {"status": "source-error", "attempts": 0, "http_status": None}
    connection = http.client.HTTPConnection("127.0.0.1", source_port, timeout=payload["timeout"])
    try:
        connection.request("GET", "/api/events/" + quote(payload["event_id"], safe=""),
                           headers={"Authorization": "Bearer " + payload["token"]})
        response = connection.getresponse()
        raw = response.read(MAX_EVENT_JSON + 1)
        if response.status != 200 or len(raw) > MAX_EVENT_JSON:
            receipt["error"] = "source-response-refused"
            return receipt
        event = json.loads(raw)
        if not isinstance(event, dict) or event.get("id") != payload["event_id"] \
                or not isinstance(event.get("body"), str) or not isinstance(event.get("headers"), dict):
            raise DeliveryError("Invalid source event")
        body = event["body"].encode("utf-8")
        fingerprint = hashlib.sha256(body).hexdigest()
        if len(body) > MAX_BODY or event.get("body_sha256") != fingerprint:
            raise DeliveryError("Invalid source body or digest")
        headers = {key: value for key, value in event["headers"].items() if key in FORWARDED_HEADERS}
        if any(not safe_header(value) for value in headers.values()) or sum(
            len(key) + len(value) for key, value in headers.items()
        ) > MAX_HEADERS:
            raise DeliveryError("Invalid source headers")
        headers["X-Phoenix-Event-Id"] = payload["event_id"]
        receipt.update(body_sha256=fingerprint, request_bytes=len(body))
    except (OSError, http.client.HTTPException, ValueError, UnicodeError):
        receipt["error"] = "source-response-invalid"
        return receipt
    finally:
        connection.close()
    # Flush the phase marker before a network attempt. The parent uses it if the
    # absolute deadline kills this worker while a delivery may have happened.
    print(json.dumps({"phase": "post-started"}), flush=True)
    receipt.update(status="delivery-unknown", attempts=1)
    connection = http.client.HTTPConnection("127.0.0.1", destination_port, timeout=payload["timeout"])
    try:
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        prefix = response.read(MAX_RESPONSE + 1)
        receipt.update(status="delivered" if 200 <= response.status < 300 else "rejected",
                       http_status=response.status, response_bytes_observed=len(prefix),
                       response_truncated=len(prefix) > MAX_RESPONSE)
    except (OSError, http.client.HTTPException, ValueError, UnicodeError):
        receipt["error"] = "delivery-response-unavailable"
    finally:
        connection.close()
    return receipt


def deliver_event(source, event_id, destination, admin_token, timeout=3):
    """Read one event, post it at most once, and enforce a worker-wide deadline."""
    endpoint(source, source=True)
    endpoint(destination)
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200 or not safe_header(event_id):
        raise DeliveryError("Use an event ID of 1..200 printable header characters")
    if not isinstance(admin_token, str) or not admin_token or len(admin_token) > 512 \
            or not safe_header(admin_token) or not admin_token.isascii():
        raise DeliveryError("PHOENIX_ADMIN_TOKEN must contain 1..512 printable ASCII characters")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.1 <= timeout <= 10:
        raise DeliveryError("Delivery timeout must be 0.1..10 seconds")
    started = time.monotonic()
    timestamp = datetime.now(timezone.utc).isoformat()
    source_bytes = Path(__file__).read_bytes()
    payload = {"source": source, "event_id": event_id, "destination": destination,
               "token": admin_token, "timeout": timeout}
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="phoenix-delivery-") as temporary:
        directory = Path(temporary)
        path = directory / "worker.py"
        path.write_bytes(source_bytes)
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TMPDIR", "LANG") if key in os.environ}
        process = subprocess.Popen([sys.executable, "-I", "-B", str(path)], cwd=directory,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=environment)
        try:
            output, _ = process.communicate(json.dumps(payload).encode("utf-8"),
                                            timeout=max(0.001, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            output, _ = process.communicate(timeout=5)
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            raise
    post_started = False
    result = None
    lines = output.decode("utf-8", errors="replace").splitlines()
    try:
        post_started = bool(lines) and json.loads(lines[0]) == {"phase": "post-started"}
    except ValueError:
        pass
    try:
        if not timed_out and process.returncode == 0:
            expected = 2 if post_started else 1
            if len(lines) == expected:
                final_message = json.loads(lines[-1])
                if isinstance(final_message, dict):
                    result = final_message.get("result")
    except ValueError:
        pass
    if not valid_result(result, post_started):
        result = {"status": "delivery-unknown" if post_started else "source-error",
                  "attempts": int(post_started), "http_status": None,
                  "error": "deadline" if timed_out else "worker-result-unavailable"}
    result.update(schema_version=1, kind="loopback-delivery", event_id=event_id,
                  started_at=timestamp, duration_seconds=round(time.monotonic() - started, 3),
                  timeout_seconds=timeout, worker_sha256=hashlib.sha256(source_bytes).hexdigest())
    return result


if __name__ == "__main__":
    try:
        result = worker(json.loads(sys.stdin.buffer.read(8192)))
        print(json.dumps({"result": result}), flush=True)
    except Exception:
        raise SystemExit(2)
