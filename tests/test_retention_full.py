"""Controls for composed acceptance; no model calls or published AI records."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from phoenix import __main__ as cli
from phoenix import retention
from phoenix.audit import CHECK_IDS as BASE_IDS, audit
from phoenix.records import publish
from phoenix.records import ROOT
from phoenix.verify import evaluate

REFERENCE = ROOT / "examples/reference/server.py"
RETENTION = ROOT / "examples/retention/server.py"


class ComposedRetentionTests(unittest.TestCase):
    def test_reference_passes_32_distinct_checks_without_changing_the_ai_ledger(self):
        before = (ROOT / "docs/runs.json").read_bytes()
        report = retention.evaluate_retention_full(RETENTION, REFERENCE)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual((report["passed"], report["total"], report["expected_total"]), (32, 32, 32))
        self.assertEqual({c["id"] for c in report["checks"]}, retention.COMPOSED_CHECK_IDS)
        self.assertEqual(report["phases"]["baseline"]["passed"], 19)
        self.assertEqual(report["phases"]["retention"]["passed"], 13)
        self.assertEqual(report["baseline_expected_schema_version"], 2)
        self.assertEqual((ROOT / "docs/runs.json").read_bytes(), before)

    def test_a_body_limit_regression_missed_by_the_focused_pilot_fails_composed_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = RETENTION.read_text()
            self.assertIn("if size > 65536:", source)
            candidate = Path(temporary) / "server.py"
            candidate.write_text(source.replace("if size > 65536:", "if size > 65537:"))
            report = retention.evaluate_retention_full(candidate, REFERENCE)
        self.assertEqual(report["phases"]["retention"]["status"], "passed")
        self.assertEqual(report["phases"]["retention"]["passed"], 13)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(next(c for c in report["checks"] if c["id"] == "B-S03")["passed"])

    def test_both_phases_keep_captured_sources_when_original_paths_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate.py"
            predecessor = Path(temporary) / "previous.py"
            original = RETENTION.read_bytes()
            previous = REFERENCE.read_bytes()
            candidate.write_bytes(original)
            predecessor.write_bytes(previous)
            real_baseline = retention.evaluate_base

            def change_originals(*args, **kwargs):
                report = real_baseline(*args, **kwargs)
                candidate.write_text("# replaced original after baseline\n")
                predecessor.write_text("# replaced original predecessor\n")
                return report

            with patch.object(retention, "evaluate_base", side_effect=change_originals):
                report = retention.evaluate_retention_full(candidate, predecessor)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["candidate_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(report["predecessor_sha256"], hashlib.sha256(previous).hexdigest())

    def test_a_phase_with_missing_checks_cannot_claim_a_combined_pass(self):
        real_baseline = retention.evaluate_base

        def missing_check(*args, **kwargs):
            report = real_baseline(*args, **kwargs)
            report["checks"] = report["checks"][:-1]
            # Deliberately inconsistent phase metadata must not make the union pass.
            return report

        with patch.object(retention, "evaluate_base", side_effect=missing_check):
            report = retention.evaluate_retention_full(RETENTION, REFERENCE)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["total"], 31)
        self.assertEqual(report["expected_total"], 32)

    def test_changed_verification_metadata_refuses_a_combined_verdict(self):
        real_focused = retention.evaluate_retention

        def changed_hash(*args, **kwargs):
            report = real_focused(*args, **kwargs)
            report["shared_subject_sha256"] = "0" * 64
            return report

        with patch.object(retention, "evaluate_retention", side_effect=changed_hash):
            with self.assertRaisesRegex(RuntimeError, "verification inputs changed"):
                retention.evaluate_retention_full(RETENTION, REFERENCE)

    def test_v1_default_keeps_exactly_the_original_19_check_identifiers(self):
        report = evaluate(REFERENCE, predecessor=REFERENCE)
        self.assertEqual(report["status"], "passed", report)
        self.assertEqual(report["expected_schema_version"], 1)
        self.assertEqual({c["id"] for c in report["checks"]}, BASE_IDS)
        self.assertEqual(report["total"], 19)

    def test_invalid_schema_expectations_are_rejected_before_starting_a_subject(self):
        for version in (0, 3, True, "2"):
            with self.subTest(version=version), patch("phoenix.verify.Subject") as subject:
                with self.assertRaisesRegex(ValueError, "must be 1 or 2"):
                    evaluate(REFERENCE, expected_schema_version=version)
                subject.assert_not_called()

    def test_full_cli_selects_composed_mode_and_never_publishes_ai_evidence(self):
        with patch.object(sys, "argv", ["phoenix", "verify-retention", "--full"]), \
                patch.object(retention, "evaluate_retention_full", return_value={"status": "passed"}) as full, \
                patch.object(retention, "evaluate_retention") as focused, \
                patch.object(cli, "publish") as publish, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)
        full.assert_called_once()
        focused.assert_not_called()
        publish.assert_not_called()

    def test_v2_baseline_cannot_be_mislabelled_as_a_contract_001_ledger_record(self):
        report = evaluate(RETENTION, predecessor=REFERENCE, expected_schema_version=2)
        self.assertEqual(report["status"], "passed", report)
        with tempfile.TemporaryDirectory() as temporary, patch("phoenix.records.ROOT", Path(temporary)):
            publish(report, RETENTION, predecessor=REFERENCE,
                    verifier_source=(ROOT / "phoenix/verify.py").read_bytes(),
                    contract_source=(ROOT / "specs/webhook-inbox.md").read_bytes())
            self.assertTrue(any("only supports contract-001" in error for error in audit(Path(temporary) / "docs")))


if __name__ == "__main__":
    unittest.main()
