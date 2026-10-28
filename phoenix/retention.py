"""Focused contract-002 pilot; does not publish or replace contract-001 evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import time

from .records import ROOT
from .verify import Subject, require, evaluate as evaluate_base
from .audit import CHECK_IDS as BASE_CHECK_IDS

CHECK_IDS = {"M01", "M02", "M03", "M04", "S01", "P01", "P02", "F01",
             "E01", "E02", "E03", "E04", "P03"}
COMPOSED_CHECK_IDS = {"B-" + key for key in BASE_CHECK_IDS} | CHECK_IDS


def evaluate_retention_full(candidate, predecessor):
    """Compose baseline and retention checks using the same captured source bytes."""
    started = time.monotonic()
    timestamp = datetime.now(timezone.utc).isoformat()
    candidate_source = Path(candidate).read_bytes()
    predecessor_source = Path(predecessor).read_bytes()
    bindings = {field: hashlib.sha256(path.read_bytes()).hexdigest() for field, path in (
        ("verifier_sha256", Path(__file__)),
        ("shared_subject_sha256", ROOT / "phoenix/verify.py"),
        ("contract_sha256", ROOT / "specs/webhook-inbox-retention.md"),
    )}
    with tempfile.TemporaryDirectory(prefix="phoenix-retention-full-") as temporary:
        directory = Path(temporary)
        subject = directory / "server.py"
        prior = directory / "predecessor.py"
        subject.write_bytes(candidate_source)
        prior.write_bytes(predecessor_source)
        baseline = evaluate_base(subject, kind="reference-verification", predecessor=prior,
                                 expected_schema_version=2)
        # The two phases have separate fixture databases, but identical source bytes.
        focused = evaluate_retention(subject, prior)
        for report in (baseline, focused):
            if report["candidate_sha256"] != hashlib.sha256(candidate_source).hexdigest() \
                    or report["predecessor_sha256"] != hashlib.sha256(predecessor_source).hexdigest():
                raise RuntimeError("Composed acceptance source changed between phases; no combined verdict")
        if any(focused[field] != expected for field, expected in bindings.items()):
            raise RuntimeError("Composed verification inputs changed between phases; no combined verdict")
    report = dict(focused)
    report.update(
        started_at=timestamp,
        verification_scope="contract-002 composed acceptance; 19 baseline plus 13 retention checks",
        source_selection="candidate and predecessor bytes captured once before either phase",
        fixture_separation="independent baseline and retention databases; both populated via predecessor HTTP",
        baseline_expected_schema_version=2,
        expected_total=len(COMPOSED_CHECK_IDS),
        checks=[dict(check, id="B-" + check["id"], phase="baseline") for check in baseline["checks"]]
               + [dict(check, phase="retention") for check in focused["checks"]],
        phases={key: {field: value[field] for field in ("status", "passed", "total", "duration_seconds")}
                for key, value in (("baseline", baseline), ("retention", focused))},
        duration_seconds=round(time.monotonic() - started, 3),
    )
    report["passed"] = sum(check["passed"] for check in report["checks"])
    report["total"] = len(report["checks"])
    report["status"] = "passed" if (
        baseline["status"] == focused["status"] == "passed"
        and report["passed"] == report["total"] == len(COMPOSED_CHECK_IDS)
        and {check["id"] for check in report["checks"]} == COMPOSED_CHECK_IDS
    ) else "failed"
    return report


def evaluate_retention(candidate, predecessor):
    """Execute trusted local programs; SQLite fixture aging is disclosed separately."""
    started = time.monotonic()
    report = {
        "schema_version": 1, "kind": "retention-reference-verification",
        "contract_id": "002", "contract_version": "2.0.0",
        "verification_scope": "focused migration/retention pilot; not full v2 reconstruction acceptance",
        "started_at": datetime.now(timezone.utc).isoformat(), "checks": [],
        "execution": "local-subprocess", "human_interventions": None,
        "candidate_sha256": hashlib.sha256(Path(candidate).read_bytes()).hexdigest(),
        "predecessor_sha256": hashlib.sha256(Path(predecessor).read_bytes()).hexdigest(),
        "contract_sha256": hashlib.sha256((ROOT / "specs/webhook-inbox-retention.md").read_bytes()).hexdigest(),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "shared_subject_sha256": hashlib.sha256((ROOT / "phoenix/verify.py").read_bytes()).hexdigest(),
        "migration_fixture": "v1 predecessor creates data through HTTP; logical rows compared before fixture aging",
        "expiry_fixture": "only synthetic v2 events are backdated via SQLite while stopped; no elapsed-time claim",
    }
    with tempfile.TemporaryDirectory(prefix="phoenix-retention-") as temporary:
        subject = Subject(candidate, temporary)
        context = {"inboxes": []}

        def check(identifier, title, callback):
            before = time.monotonic()
            try:
                callback()
                result = {"id": identifier, "title": title, "passed": True}
            except Exception as error:
                message = str(error).replace(subject.token, "[redacted]")
                for inbox in context["inboxes"]:
                    message = message.replace(inbox["token"], "[redacted]")
                result = {"id": identifier, "title": title, "passed": False, "error": message[:400]}
            result["duration_ms"] = round((time.monotonic() - before) * 1000, 2)
            report["checks"].append(result)
            return result["passed"]

        def expect(method, path, status, body=None, admin=True, headers=None):
            actual, data, response_headers = subject.request(method, path, body, headers, admin)
            require(actual == status, "Unexpected HTTP status: expected %s, got %s" % (status, actual))
            require("application/json" in response_headers.get("Content-Type", ""), "Missing JSON content type")
            if status >= 400:
                require(isinstance(data, dict) and isinstance(data.get("error"), str), "Missing error string")
            return data

        def rows():
            with sqlite3.connect(subject.database) as db:
                return {name: db.execute("SELECT * FROM " + name + " ORDER BY id").fetchall()
                        for name in ("inboxes", "events")}

        def policy(inbox):
            return "/api/inboxes/" + inbox["id"] + "/retention"

        def expiry(inbox):
            return "/api/inboxes/" + inbox["id"] + "/expire"

        def hook(inbox):
            return "/hooks/%s/%s" % (inbox["id"], inbox["token"])

        def create(name):
            item = expect("POST", "/api/inboxes", 201, {"name": name})
            context["inboxes"].append(item)
            return item

        def delivery(inbox, key, body):
            return expect("POST", hook(inbox), 201, body, admin=False, headers={"X-Webhook-Id": key})

        def detail(identifier, status=200):
            return expect("GET", "/api/events/" + identifier, status)

        def boot():
            old = Subject(predecessor, temporary)
            try:
                old.start()
                status, inbox, _ = old.request("POST", "/api/inboxes", {"name": "Migrated v1 inbox"})
                require(status == 201, "Predecessor did not create an inbox")
                context["inboxes"].append(inbox)
                body = "v1 migration: 保留原文 🔥\n"
                status, event, _ = old.request("POST", hook(inbox), body,
                                              {"X-Webhook-Id": "migration-key", "X-Event-Type": "migration.fixture"}, admin=False)
                require(status == 201, "Predecessor did not create an event")
                context.update(legacy_inbox=inbox, legacy_event=event["id"], legacy_body=body)
                context["before"] = rows()
                subject.token = old.token
            finally:
                old.stop()
            subject.start()
            require(expect("GET", "/health", 200, admin=False) == {"status": "ok", "schema_version": 2},
                    "Contract 002 must report schema version 2")

        def migration():
            require(rows() == context["before"], "Migration changed v1 rows, IDs, tokens or event metadata")
            event = detail(context["legacy_event"])
            require(event["body"] == context["legacy_body"], "Migration changed the original UTF-8 body")
            require(event["headers"]["x-event-type"] == "migration.fixture", "Migration changed event headers")
            duplicate = expect("POST", hook(context["legacy_inbox"]), 200, context["legacy_body"], admin=False,
                               headers={"X-Webhook-Id": "migration-key"})
            require(duplicate == {"id": context["legacy_event"], "duplicate": True}, "Migration lost idempotency")
            with sqlite3.connect(subject.database) as db:
                require(db.execute("PRAGMA user_version").fetchone()[0] == 2, "Migration did not commit version 2")

        def default():
            require(expect("GET", policy(context["legacy_inbox"]), 200) == {"max_age_seconds": None},
                    "Migration enabled retention implicitly")
            require(expect("POST", expiry(context["legacy_inbox"]), 200) == {"deleted": 0},
                    "Disabled retention removed events")
            require(rows() == context["before"], "Disabled retention changed old data")

        def restart():
            subject.stop()
            subject.start()
            migration()
            default()

        def authorization():
            for method, path, body in (("GET", "/api/inboxes/missing/retention", None),
                                       ("PUT", policy(context["legacy_inbox"]), {"max_age_seconds": 1}),
                                       ("POST", expiry(context["legacy_inbox"]), {})):
                expect(method, path, 401, body, admin=False)
                expect(method, path, 401, body, headers={"Authorization": "Bearer incorrect"})
            expect("GET", "/api/inboxes/missing/retention", 404)
            expect("PUT", "/api/inboxes/missing/retention", 404, {"max_age_seconds": 1})
            expect("POST", "/api/inboxes/missing/expire", 404, {})
            expect("POST", policy(context["legacy_inbox"]), 405, {})
            expect("GET", expiry(context["legacy_inbox"]), 405)

        def invalid():
            path = policy(context["legacy_inbox"])
            for value in ({}, [], "null", "{broken", {"max_age_seconds": 0}, {"max_age_seconds": 2592001},
                          {"max_age_seconds": True}, {"max_age_seconds": 1.5}, {"max_age_seconds": "60"},
                          {"max_age_seconds": 60, "extra": 1}):
                expect("PUT", path, 400, value)
            for value in ([], "null", "{broken", {"limit": 0}, {"limit": 101}, {"limit": True},
                          {"limit": 1.5}, {"limit": "1"}, {"extra": 1}):
                expect("POST", expiry(context["legacy_inbox"]), 400, value)
            require(expect("GET", path, 200) == {"max_age_seconds": None}, "Invalid input changed the policy")

        def persistence():
            for age in (1, 2592000, 3600):
                require(expect("PUT", policy(context["legacy_inbox"]), 200, {"max_age_seconds": age})
                        == {"max_age_seconds": age}, "Policy boundary value did not round-trip")
            subject.stop()
            subject.start()
            require(expect("GET", policy(context["legacy_inbox"]), 200) == {"max_age_seconds": 3600},
                    "Policy did not persist across restart")
            require(rows() == context["before"], "Policy update or startup deleted old data")

        def age_fixture():
            active = create("One-hour retention")
            disabled = create("Retention disabled")
            longer = create("One-day retention")
            for inbox, age in ((active, 3600), (longer, 86400)):
                expect("PUT", policy(inbox), 200, {"max_age_seconds": age})
            old = [delivery(active, "expired-%s" % i, "expired body %s" % i)["id"] for i in range(3)]
            recent = delivery(active, "recent", "recent body")["id"]
            retained = delivery(disabled, "retained", "disabled body")["id"]
            long_event = delivery(longer, "long", "longer policy body")["id"]
            subject.stop()
            # Explicit deterministic fixture aging, after migration has already passed.
            # No v1 predecessor row is modified here.
            now = datetime.now(timezone.utc)
            with sqlite3.connect(subject.database) as db:
                for index, identifier in enumerate(old + [retained, long_event]):
                    instant = (now - timedelta(hours=2) + timedelta(seconds=index)).isoformat()
                    if index % 2 == 0:
                        instant = instant.replace("+00:00", "Z")
                    db.execute("UPDATE events SET received_at=? WHERE id=?", (instant, identifier))
            subject.start()
            for identifier in old + [recent, retained, long_event]:
                detail(identifier)
            # Updating a policy also must not implicitly remove already eligible rows.
            expect("PUT", policy(active), 200, {"max_age_seconds": 3600})
            for identifier in old:
                detail(identifier)
            context.update(active=active, disabled=disabled, longer=longer, expired=old,
                           recent=recent, retained=retained, long_event=long_event)

        def bounded():
            require(expect("POST", expiry(context["active"]), 200, {"limit": 1}) == {"deleted": 1},
                    "Batch did not delete exactly one eligible event")
            detail(context["expired"][0], 404)
            for identifier in context["expired"][1:] + [context["recent"]]:
                detail(identifier)

        def concurrent():
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: subject.request("POST", expiry(context["active"]), {"limit": 1}), range(2)))
            require(all(status == 200 and body == {"deleted": 1} for status, body, _ in results),
                    "Concurrent batches double-counted, exceeded the limit or missed eligible events")
            for identifier in context["expired"]:
                detail(identifier, 404)

        def isolation():
            for inbox in (context["active"], context["disabled"], context["longer"]):
                require(expect("POST", expiry(inbox), 200) == {"deleted": 0},
                        "Expiry removed a recent event or ignored an inbox policy")
            for identifier in (context["recent"], context["retained"], context["long_event"], context["legacy_event"]):
                detail(identifier)

        def reuse():
            new = delivery(context["active"], "expired-0", "replacement body")
            require(new["id"] != context["expired"][0], "Expired key retained its deleted event identity")
            duplicate = expect("POST", hook(context["active"]), 200, "replacement body", admin=False,
                               headers={"X-Webhook-Id": "expired-0"})
            require(duplicate == {"id": new["id"], "duplicate": True}, "New idempotency state was not retained")
            expect("POST", hook(context["active"]), 409, "conflict", admin=False, headers={"X-Webhook-Id": "expired-0"})

        def disable():
            inbox = context["disabled"]
            expect("PUT", policy(inbox), 200, {"max_age_seconds": 3600})
            detail(context["retained"])
            require(expect("PUT", policy(inbox), 200, {"max_age_seconds": None}) == {"max_age_seconds": None},
                    "Policy could not be disabled")
            subject.stop()
            subject.start()
            require(expect("GET", policy(inbox), 200) == {"max_age_seconds": None}, "Disabled policy did not persist")
            require(expect("POST", expiry(inbox), 200, {"limit": 100}) == {"deleted": 0}, "Disabled policy expired an old event")
            detail(context["retained"])

        try:
            if check("M01", "Boot a v1 predecessor and migrate its real HTTP-created database", boot) \
                    and check("M02", "Preserve every v1 row and its live token/idempotency behavior", migration):
                for identifier, title, callback in (
                    ("M03", "Migration leaves retention disabled and events visible", default),
                    ("M04", "Repeated startup preserves migration data and version", restart),
                    ("S01", "New routes authenticate before lookup and reject unsupported methods", authorization),
                    ("P01", "Reject malformed policies and invalid expiry limits without changing policy", invalid),
                    ("P02", "Accept policy bounds, persist configuration and never delete implicitly", persistence),
                    ("F01", "Age only disclosed synthetic v2 fixtures; restart and GET do not expire them", age_fixture),
                    ("E01", "Delete only the oldest eligible event within the requested batch limit", bounded),
                    ("E02", "Concurrent expiry batches serialize without double-counting", concurrent),
                    ("E03", "Keep recent, disabled-policy, other-inbox and longer-policy events", isolation),
                    ("E04", "Release expired idempotency keys and deduplicate their replacement", reuse),
                    ("P03", "Disabling retention survives restart and protects eligible data", disable),
                ):
                    check(identifier, title, callback)
        finally:
            subject.stop()
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    report["passed"] = sum(c["passed"] for c in report["checks"])
    report["total"] = len(report["checks"])
    report["status"] = "passed" if report["passed"] == len(CHECK_IDS) and {c["id"] for c in report["checks"]} == CHECK_IDS else "failed"
    return report
