import argparse
import json
import sys

from .records import ROOT, publish
from .verify import evaluate


def main():
    parser = argparse.ArgumentParser(description="Phoenix · rebuild software from a contract and publish its evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="Run black-box acceptance checks on a trusted candidate")
    verify.add_argument("--candidate", default=str(ROOT / "examples/reference/server.py"))
    verify.add_argument("--publish", action="store_true", help="Save a public reference-verification record")
    verify.add_argument("--predecessor", default=str(ROOT / "examples/reference/server.py"), help="Previous implementation used to create a real database via HTTP")
    container = commands.add_parser("container-verify", help="Run HTTP acceptance inside an optional, restricted Docker container")
    container.add_argument("--candidate", default=str(ROOT / "examples/reference/server.py"))
    container.add_argument("--predecessor", default=str(ROOT / "examples/reference/server.py"))
    container.add_argument("--timeout", type=int, default=90, help="Container execution deadline, 1..300 seconds; image pull is separately bounded")
    generate = commands.add_parser("rebuild", help="Generate a fresh candidate using your authenticated Codex CLI")
    generate.add_argument("--attempts", type=int, choices=range(1, 6), default=2)
    generate.add_argument("--timeout", type=int, default=300)
    generate.add_argument("--model", help="Optional; otherwise use the clean CLI default")
    generate.add_argument("--use-user-config", action="store_true", help="Opt into your user config, including model selection and MCP servers")
    generate.add_argument("--predecessor", help="Trusted previous implementation used to create the continuity database")
    commands.add_parser("serve", help="Serve the public experiment notebook locally")
    commands.add_parser("audit", help="Check published evidence hashes and ledger consistency")
    replay = commands.add_parser("replay", help="Re-run a trusted published candidate with its archived verifier")
    replay.add_argument("run_id")
    args = parser.parse_args()
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
        try:
            reports = rebuild(args.attempts, args.timeout, args.model, args.use_user_config, args.predecessor)
        except (RuntimeError, OSError) as error:
            print(str(error), file=sys.stderr)
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
        print(json.dumps(report, indent=2))
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
