import argparse
import json
import os
import sys

from .records import ROOT, publish
from .verify import evaluate, runtime_identity


def main():
    parser = argparse.ArgumentParser(description="Phoenix · rebuild software from a contract and publish its evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="Run black-box acceptance checks on a trusted candidate")
    verify.add_argument("--candidate", default=str(ROOT / "examples/reference/server.py"))
    verify.add_argument("--publish", action="store_true", help="Save a public reference-verification record")
    verify.add_argument("--predecessor", default=str(ROOT / "examples/reference/server.py"), help="Previous implementation used to create a real database via HTTP")
    retention = commands.add_parser("verify-retention", help="Run the focused contract-002 reference/migration pilot; does not publish AI records")
    retention.add_argument("--candidate", default=str(ROOT / "examples/retention/server.py"))
    retention.add_argument("--predecessor", default=str(ROOT / "examples/reference/server.py"), help="Trusted v1 program used to create migration data through HTTP")
    retention.add_argument("--full", action="store_true", help="Compose 19 schema-v2 baseline checks with the 13 focused retention checks; no model call")
    retention.add_argument("--publish", action="store_true", help="With --full, publish a frozen contract-002 reference bundle; no model call")
    continuity = commands.add_parser("verify-continuity", help="Check policy and data continuity between two trusted v2 programs")
    continuity.add_argument("--candidate", required=True, help="Trusted receiving v2 implementation")
    continuity.add_argument("--predecessor", required=True, help="Trusted v2 program that creates the handoff database")
    continuity.add_argument("--output-dir", help="Save a separate frozen continuity bundle in a new directory")
    handoff_replay = commands.add_parser("replay-continuity", help="Validate and replay a trusted frozen v2 continuity bundle")
    handoff_replay.add_argument("directory")
    delivery = commands.add_parser("deliver-event", help="Deliver one event to an explicit IPv4 loopback HTTP receiver")
    delivery.add_argument("--source", required=True, help="Inbox origin http://127.0.0.1:PORT")
    delivery.add_argument("--event-id", required=True)
    delivery.add_argument("--destination", required=True, help="Explicit http://127.0.0.1:PORT/path")
    delivery.add_argument("--timeout", type=float, default=3, help="Overall worker deadline, 0.1..10 seconds")
    delivery.add_argument("--journal", help="Optional local SQLite attempt history; committed before network activity")
    delivery.add_argument("--destination-policy", help="Optional exact loopback URL allowlist JSON file")
    history = commands.add_parser("delivery-history", help="Read local delivery attempts without retrying them")
    history.add_argument("--journal", required=True)
    history.add_argument("--limit", type=int, default=20, help="Newest 1..100 attempts, default 20")
    history.add_argument("--cursor", help="Continue from the previous response's next_cursor")
    history.add_argument("--state", help="Filter by journal state: unconfirmed or finished")
    history.add_argument("--event-id", help="Filter by exact stored event ID")
    receipt = commands.add_parser("delivery-receipt", help="Read one local delivery attempt by ID")
    receipt.add_argument("--journal", required=True)
    receipt.add_argument("--attempt-id", required=True, help="The journal_attempt_id returned by deliver-event")
    container = commands.add_parser("container-verify", help="Run HTTP acceptance inside an optional, restricted Docker container")
    container.add_argument("--candidate", default=str(ROOT / "examples/reference/server.py"))
    container.add_argument("--predecessor", default=str(ROOT / "examples/reference/server.py"))
    container.add_argument("--timeout", type=int, default=90, help="Container execution deadline, 1..300 seconds; image pull is separately bounded")
    generate = commands.add_parser("rebuild", help="Generate a fresh candidate using your authenticated Codex CLI")
    generate.add_argument("--attempts", type=int, choices=range(1, 6), default=2)
    generate.add_argument("--timeout", type=int, default=300)
    generate.add_argument("--model", help="Optional; otherwise use the clean CLI default")
    generate.add_argument("--contract", choices=("001", "002"), default="001", help="Contract to reconstruct; 002 uses full frozen 32-check acceptance")
    generate.add_argument("--use-user-config", action="store_true", help="Opt into your user config, including model selection and MCP servers")
    generate.add_argument("--predecessor", help="Trusted previous implementation used to create the continuity database")
    generate.add_argument("--container", action="store_true", help="Verify generated code inside Docker; generation still uses the host CLI")
    generate.add_argument("--verification-timeout", type=int, default=90, help="Container execution deadline, 1..300 seconds; used only with --container")
    commands.add_parser("serve", help="Serve the public experiment notebook locally")
    commands.add_parser("audit", help="Check published evidence hashes and ledger consistency")
    replay = commands.add_parser("replay", help="Re-run a trusted published candidate with its archived verifier")
    replay.add_argument("run_id")
    replay.add_argument("--container", action="store_true", help="Run the verified archive inside Docker without loading its verifier on the host")
    replay.add_argument("--timeout", type=int, default=90, help="Container execution deadline, 1..300 seconds; used only with --container")
    matrix = commands.add_parser("replay-matrix", help="Replay one verified archive in 1..4 installed Python runtimes; POSIX only; no model call")
    matrix.add_argument("run_id")
    matrix.add_argument("--python", dest="interpreters", action="append", required=True, help="Installed Python executable; repeat up to four times")
    matrix.add_argument("--timeout", type=int, default=90, help="Deadline per runtime, 1..300 seconds; default 90")
    args = parser.parse_args()
    if args.command == "deliver-event":
        from .delivery import DeliveryError, deliver_event
        from .delivery_journal import JournalError
        try:
            report = deliver_event(args.source, args.event_id, args.destination,
                                   os.environ.get("PHOENIX_ADMIN_TOKEN", ""), args.timeout,
                                   journal=args.journal, policy=args.destination_policy)
        except (DeliveryError, JournalError, OSError) as error:
            print("Delivery refused: " + str(error), file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if report.get("journal_saved") is False:
            return 2
        if report["status"] == "delivered":
            return 0
        if report["status"] == "rejected":
            return 1
        return 2
    if args.command == "delivery-history":
        from .delivery_journal import JournalError, delivery_history_page
        try:
            page = delivery_history_page(args.journal, args.limit, args.cursor, state=args.state,
                                         event_id=args.event_id)
        except JournalError as error:
            print("Delivery history refused: " + str(error), file=sys.stderr)
            return 2
        print(json.dumps({"kind": "local-delivery-history", **page}, indent=2, ensure_ascii=False))
        return 0
    if args.command == "delivery-receipt":
        from .delivery_journal import JournalError, delivery_attempt
        try:
            attempt = delivery_attempt(args.journal, args.attempt_id)
        except JournalError as error:
            print("Delivery receipt refused: " + str(error), file=sys.stderr)
            return 2
        print(json.dumps({"kind": "local-delivery-receipt", "attempt": attempt}, indent=2, ensure_ascii=False))
        return 0 if attempt is not None else 1
    if args.command in ("verify-continuity", "replay-continuity"):
        from .audit import EvidenceError
        from .continuity import verify_continuity, replay_continuity
        try:
            if args.command == "verify-continuity":
                report = verify_continuity(args.candidate, args.predecessor, args.output_dir)
            else:
                report = replay_continuity(args.directory)
        except (EvidenceError, OSError) as error:
            print("Continuity verification refused: " + str(error), file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["status"] == "passed" else 1
    if args.command == "replay-matrix":
        from .matrix import replay_matrix
        from .audit import EvidenceError
        try:
            report = replay_matrix(ROOT / "docs", args.run_id, args.interpreters, args.timeout)
        except EvidenceError as error:
            print("Runtime matrix refused: " + str(error), file=sys.stderr)
            return 2
        except OSError as error:
            print("Runtime matrix refused: " + type(error).__name__, file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2))
        return 0 if report["status"] == "passed" else 1 if report["status"] == "failed" else 2
    if args.command == "verify-retention":
        from .retention import evaluate_retention, evaluate_retention_full
        if args.publish and not args.full:
            parser.error("verify-retention --publish requires --full")
        try:
            if args.publish:
                from .retention_archive import publish_retention
                report = publish_retention(args.candidate, args.predecessor)
            else:
                report = (evaluate_retention_full if args.full else evaluate_retention)(args.candidate, args.predecessor)
        except (OSError, RuntimeError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["status"] == "passed" else 1
    if args.command == "container-verify":
        from .container import evaluate_in_container
        try:
            report = evaluate_in_container(args.candidate, predecessor=args.predecessor, timeout=args.timeout)
        except (RuntimeError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["status"] == "passed" else 1
    if args.command == "verify":
        report = evaluate(args.candidate, predecessor=args.predecessor)
        if args.publish:
            report = publish(report, args.candidate, predecessor=args.predecessor)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["status"] == "passed" else 1
    if args.command == "rebuild":
        from .rebuild import rebuild
        contract_options = {"contract_id": args.contract} if args.contract != "001" else {}
        try:
            reports = rebuild(args.attempts, args.timeout, args.model, args.use_user_config, args.predecessor,
                              use_container=args.container, verification_timeout=args.verification_timeout, **contract_options)
        except (RuntimeError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 2
        if reports and reports[-1]["status"] == "verification-error":
            return 2
        return 0 if reports and reports[-1]["status"] == "passed" else 1
    if args.command == "serve":
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        from functools import partial
        print("Phoenix notebook: http://127.0.0.1:8788")
        ThreadingHTTPServer(("127.0.0.1", 8788), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "docs"))).serve_forever()
    if args.command == "audit":
        from .audit import audit
        errors = audit(ROOT / "docs")
        for error in errors:
            print(error, file=sys.stderr)
        print("Evidence audit: " + ("FAILED" if errors else "passed"))
        return 1 if errors else 0
    if args.command == "replay":
        import tempfile
        import types
        from pathlib import Path
        from .audit import EvidenceError, verified_run
        try:
            archived, artifacts = verified_run(ROOT / "docs", args.run_id)
            if archived["status"] == "generation-failed":
                raise EvidenceError("Generation failed; there is no executable candidate to replay")
            if "verifier.py" not in artifacts:
                raise EvidenceError("This record has no archived verifier; use verify --candidate instead")
        except EvidenceError as error:
            print("Replay refused: " + str(error), file=sys.stderr)
            return 2
        if archived.get("verifier_profile") in ("retention-composed-v1", "retention-ai-v1") and not args.container:
            from .retention_archive import replay_frozen_retention
            report = replay_frozen_retention(artifacts)
            report["execution_runtime"] = runtime_identity()
            report.update(replay_run_id=args.run_id, replay_source="verified-archive-bytes")
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "passed" else 1
        if args.container:
            from .container import RETENTION_SUPPORT, evaluate_snapshot_in_container
            options = {}
            if archived.get("verifier_profile") in ("retention-composed-v1", "retention-ai-v1"):
                options = {"verifier_profile": archived["verifier_profile"],
                           "support_sources": {name: artifacts[name] for name in RETENTION_SUPPORT}}
            try:
                report = evaluate_snapshot_in_container(
                    artifacts["server.py"], artifacts["verifier.py"],
                    predecessor_source=artifacts["predecessor.py"] if archived.get("predecessor_sha256") else None,
                    timeout=args.timeout, **options,
                )
            except (RuntimeError, OSError) as error:
                print("Replay refused: " + str(error), file=sys.stderr)
                return 2
            report.update(replay_run_id=args.run_id, replay_source="verified-archive-bytes",
                          contract_sha256=archived["contract_sha256"])
            print(json.dumps(report, indent=2))
            return 0 if report["status"] == "passed" else 1
        # Execute precisely the verified bytes, without importing stale bytecode
        # or reopening source files that may have changed after the audit.
        module = types.ModuleType("archived_verifier")
        exec(compile(artifacts["verifier.py"], "archived-verifier.py", "exec"), module.__dict__)
        with tempfile.TemporaryDirectory(prefix="phoenix-replay-") as temporary:
            directory = Path(temporary)
            candidate = directory / "server.py"
            candidate.write_bytes(artifacts["server.py"])
            kwargs = {}
            if archived.get("predecessor_sha256"):
                predecessor = directory / "predecessor.py"
                predecessor.write_bytes(artifacts["predecessor.py"])
                kwargs["predecessor"] = predecessor
            report = module.evaluate(candidate, **kwargs)
        report["execution_runtime"] = runtime_identity()
        print(json.dumps(report, indent=2))
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
