"""Real browser checks for the static notebook; run explicitly, outside unit discovery."""
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys
import threading
import unittest
from urllib.parse import urljoin

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    raise SystemExit("Install requirements-browser.txt and run: python -m playwright install --only-shell chromium")


SITE = Path(__file__).resolve().parents[1] / "docs"
PREFIX = "/phoenix-rebuild/"


class NotebookHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        # Exercise the project subpath used by Pages, not only a domain root.
        if not self.path.startswith(PREFIX):
            self.send_error(404)
            return
        self.path = self.path[len(PREFIX) - 1:]
        super().do_GET()


class NotebookBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads((SITE / "runs.json").read_text(encoding="utf-8"))
        cls.ai = [row for row in cls.ledger if row["kind"] == "ai-rebuild"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(NotebookHandler, directory=str(SITE)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

        def stop_server():
            cls.server.shutdown()
            cls.thread.join(timeout=5)
            cls.server.server_close()

        cls.addClassCleanup(stop_server)
        cls.base = "http://127.0.0.1:%s%s" % (cls.server.server_port, PREFIX)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(headless=True)
        cls.addClassCleanup(cls.browser.close)
        print("Browser: Chromium %s; Playwright %s" % (cls.browser.version, version("playwright")), file=sys.stderr)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.script_errors = []
        self.page.on("pageerror", lambda error: self.script_errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.script_errors, [], "Uncaught notebook JavaScript errors")

    def open_notebook(self):
        response = self.page.goto(self.base, wait_until="networkidle")
        self.assertEqual(response.status, 200)
        expect(self.page.locator("#runs [data-run]")).to_have_count(len(self.ledger))
        expect(self.page.locator("#load-error")).to_be_hidden()

    def test_metrics_match_the_published_ledger(self):
        self.open_notebook()
        expect(self.page.locator("#ai-count")).to_have_text(str(len(self.ai)))
        expect(self.page.locator("#success-count")).to_have_text(str(sum(r["status"] == "passed" for r in self.ai)))
        compatible = any(r["status"] == "passed" and r.get("predecessor_sha256") for r in self.ai)
        expect(self.page.locator("#compat-status")).to_have_text("Verified" if compatible else "Pending")

    def test_filters_switch_between_all_records_and_ai_only(self):
        self.open_notebook()
        self.assertTrue(self.ai, "This notebook needs an AI record to exercise filtering")
        self.assertGreater(len(self.ledger), len(self.ai), "Reference records must exercise the other filter branch")
        ai_filter = self.page.get_by_role("button", name="AI rebuilds", exact=True)
        ai_filter.click()
        expect(ai_filter).to_have_attribute("aria-pressed", "true")
        expect(self.page.locator("#runs [data-run]")).to_have_count(len(self.ai))
        self.assertEqual(self.page.locator("#runs .badge").all_text_contents(), ["AI REBUILD"] * len(self.ai))
        all_filter = self.page.get_by_role("button", name="All records", exact=True)
        all_filter.click()
        expect(all_filter).to_have_attribute("aria-pressed", "true")
        expect(ai_filter).to_have_attribute("aria-pressed", "false")
        expect(self.page.locator("#runs [data-run]")).to_have_count(len(self.ledger))

    def test_outcome_filter_distinguishes_statuses_and_composes_with_ai_filter(self):
        self.open_notebook()
        expect(self.page.get_by_role("combobox", name="Filter by outcome")).to_be_visible()
        outcome = self.page.locator("#outcome-filter")
        buttons = self.page.locator("#runs [data-run]")
        for status in ("passed", "failed", "generation-failed", "verification-error"):
            with self.subTest(status=status):
                outcome.select_option(status)
                expected = [row["id"] for row in self.ledger if row["status"] == status]
                self.assertEqual(buttons.evaluate_all("rows => rows.map(row => row.dataset.run)"), expected)
                if not expected:
                    expect(self.page.locator("#runs .empty")).to_be_visible()
        expect(self.page.locator("#ai-count")).to_have_text(str(len(self.ai)))
        expect(self.page.locator("#success-count")).to_have_text(str(sum(row["status"] == "passed" for row in self.ai)))
        outcome.select_option("passed")
        self.page.get_by_role("button", name="AI rebuilds", exact=True).click()
        expected = [row["id"] for row in self.ai if row["status"] == "passed"]
        self.assertEqual(buttons.evaluate_all("rows => rows.map(row => row.dataset.run)"), expected)
        self.page.locator("#language").click()
        expect(self.page.get_by_role("combobox", name="按结果筛选")).to_have_value("passed")
        expect(outcome.locator("option[value='generation-failed']")).to_have_text("生成失败")
        self.assertEqual(buttons.evaluate_all("rows => rows.map(row => row.dataset.run)"), expected)
        self.page.get_by_role("button", name="全部记录", exact=True).click()
        self.page.locator("#outcome-filter").select_option("all")
        expect(buttons).to_have_count(len(self.ledger))

    def test_notebook_loads_without_the_outcome_control_in_cached_markup(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        cached, removed = re.subn(r'<select id="outcome-filter">.*?</select>', "", html, flags=re.DOTALL)
        self.assertEqual(removed, 1)
        self.page.route(self.base, lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=cached))
        self.open_notebook()
        self.page.locator("#runs [data-run]").first.click()
        expect(self.page.locator("#evidence-dialog")).to_be_visible()
        expect(self.page.locator("#dialog-run-id")).to_have_text(self.ledger[0]["id"])

    def test_language_switch_persists_after_reload(self):
        self.open_notebook()
        self.page.locator("#language").click()
        expect(self.page.locator("html")).to_have_attribute("lang", "zh-CN")
        expect(self.page.locator("h1")).to_contain_text("同一份规格。")
        expect(self.page.get_by_role("button", name="仅 AI 重建", exact=True)).to_be_visible()
        self.page.reload(wait_until="networkidle")
        expect(self.page.locator("html")).to_have_attribute("lang", "zh-CN")
        expect(self.page.locator("#runs [data-run]")).to_have_count(len(self.ledger))
        expect(self.page.locator("#language")).to_have_text("EN")
        self.page.locator("#language").click()
        expect(self.page.locator("html")).to_have_attribute("lang", "en")
        expect(self.page.locator("h1")).to_contain_text("A backend.")

    def test_every_evidence_dialog_links_to_the_matching_archived_bytes(self):
        self.open_notebook()
        for row in self.ledger:
            with self.subTest(run_id=row["id"]):
                run_id = row["id"]
                directory = SITE / "evidence" / run_id
                report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
                manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                self.page.locator('[data-run="%s"]' % run_id).click()
                expect(self.page.locator("#evidence-dialog")).to_be_visible()
                expect(self.page.locator("#dialog-run-id")).to_have_text(run_id)
                expect(self.page.locator("#dialog-summary")).to_contain_text("%s/%s checks passed" % (report["passed"], report["total"]))
                expect(self.page.locator("#dialog-provenance")).to_contain_text(report["candidate_sha256"])
                expect(self.page.locator("#checks-list .check-row")).to_have_count(len(report["checks"]))
                replay = self.page.locator("#replay-instructions")
                if report["status"] == "generation-failed":
                    expect(replay).to_be_hidden()
                    expect(self.page.locator("#replay-command")).to_have_text("")
                else:
                    expect(replay).to_be_visible()
                    expect(self.page.locator("#replay-command")).to_have_text("python3 -m phoenix replay " + run_id)
                    expect(self.page.locator("#replay-notice")).to_contain_text("trusted archived code")
                if report["status"] == "generation-failed":
                    expect(self.page.locator("#checks-list")).to_contain_text(report["error"])
                expected = {"evidence/%s/%s" % (run_id, name) for name in set(manifest) | {"manifest.json"}}
                links = self.page.locator("#artifact-links a")
                hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
                self.assertEqual(set(hrefs), expected)
                self.assertEqual(len(hrefs), len(expected))
                for href in hrefs:
                    response = self.page.request.get(urljoin(self.base, href))
                    self.assertEqual(response.status, 200, href)
                    name = href.rsplit("/", 1)[-1]
                    if name == "manifest.json":
                        self.assertEqual(response.json(), manifest)
                    else:
                        self.assertEqual(hashlib.sha256(response.body()).hexdigest(), manifest[name], href)
                    response.dispose()
                # Follow a real target=_blank report link as well as checking every URL.
                with self.page.expect_popup() as opened:
                    self.page.locator('#artifact-links a[href$="/report.json"]').click()
                popup = opened.value
                popup.wait_for_load_state("domcontentloaded")
                self.assertEqual(popup.url, urljoin(self.base, "evidence/%s/report.json" % run_id))
                popup.close()
                self.page.get_by_role("button", name="Close evidence", exact=True).click()
                expect(self.page.locator("#evidence-dialog")).to_be_hidden()

    def test_replay_command_stays_safe_and_is_cleared_between_records(self):
        self.open_notebook()
        failed = next(row for row in self.ledger if row["status"] == "generation-failed")
        successful = next(row for row in self.ledger if row["status"] == "passed")
        self.page.locator('[data-run="%s"]' % successful["id"]).click()
        expect(self.page.locator("#replay-instructions")).to_be_visible()
        self.page.locator("#close-dialog").click()
        self.page.locator("#language").click()
        self.page.locator('[data-run="%s"]' % failed["id"]).click()
        expect(self.page.locator("#replay-instructions")).to_be_hidden()
        expect(self.page.locator("#replay-command")).to_have_text("")
        self.page.locator("#close-dialog").click()
        self.page.locator('[data-run="%s"]' % successful["id"]).click()
        expect(self.page.locator("#replay-heading")).to_have_text("重放此记录")
        expect(self.page.locator("#replay-notice")).to_contain_text("可信的归档代码")
        expect(self.page.locator("#replay-command")).to_have_text("python3 -m phoenix replay " + successful["id"])
        self.page.locator("#close-dialog").click()

        # Synthetic untrusted IDs never become executable shell snippets.
        row = dict(successful, id="synthetic;echo unsafe")
        report = json.loads((SITE / "evidence" / successful["id"] / "report.json").read_bytes())
        report["id"] = row["id"]
        self.page.route("**/runs.json", lambda route: route.fulfill(json=[row]))
        self.page.route("**/evidence/**/report.json", lambda route: route.fulfill(json=report))
        self.page.goto(self.base, wait_until="networkidle")
        self.page.locator("#runs [data-run]").click()
        expect(self.page.locator("#evidence-dialog")).to_be_visible()
        expect(self.page.locator("#replay-instructions")).to_be_hidden()
        expect(self.page.locator("#replay-command")).to_have_text("")

    def test_notebook_opens_evidence_without_replay_section_in_cached_markup(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        cached, removed = re.subn(r'<section id="replay-instructions".*?</section>', "", html, flags=re.DOTALL)
        self.assertEqual(removed, 1)
        self.page.route(self.base, lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=cached))
        self.open_notebook()
        self.page.locator("#runs [data-run]").first.click()
        expect(self.page.locator("#evidence-dialog")).to_be_visible()
        expect(self.page.locator("#dialog-run-id")).to_have_text(self.ledger[0]["id"])

    def test_contract_navigation_loads_the_published_specification(self):
        self.open_notebook()
        self.page.get_by_role("link", name="The contract", exact=True).click()
        specification = (SITE / "contract.md").read_text(encoding="utf-8")
        expect(self.page.locator("#specification")).to_have_text(specification)
        self.assertEqual(self.page.locator("#specification").text_content(), specification)
        self.page.get_by_role("link", name="← Back to the notebook", exact=True).click()
        expect(self.page.locator("#runs [data-run]")).to_have_count(len(self.ledger))

    def test_runtime_observations_and_missing_legacy_metadata_are_explicit_in_both_languages(self):
        row = self.ai[0]
        original = json.loads((SITE / "evidence" / row["id"] / "report.json").read_text())
        pattern = "**/evidence/%s/report.json" % row["id"]
        for recorded in (False, True):
            report = dict(original)
            report.pop("execution_runtime", None)
            if recorded:
                report["execution_runtime"] = {"python_implementation": "CPython", "python_version": "3.9.6",
                                               "sqlite_version": "3.43.2"}
            self.page.route(pattern, lambda route: route.fulfill(json=report))
            self.open_notebook()
            self.page.locator('[data-run="%s"]' % row["id"]).click()
            expect(self.page.locator("#dialog-provenance")).to_contain_text(
                "Runtime: CPython 3.9.6; SQLite 3.43.2" if recorded else "Runtime: Not recorded")
            self.page.locator("#close-dialog").click()
            self.page.locator("#language").click()
            self.page.locator('[data-run="%s"]' % row["id"]).click()
            expect(self.page.locator("#dialog-provenance")).to_contain_text(
                "运行时：CPython 3.9.6; SQLite 3.43.2" if recorded else "运行时：未记录")
            self.page.locator("#close-dialog").click()
            self.page.locator("#language").click()
            self.page.unroute(pattern)

    def test_contract_002_is_labelled_reference_and_excluded_from_ai_filter(self):
        record = next(row for row in self.ledger if row.get("contract_id") == "002" and row["kind"] == "retention-reference-verification")
        self.assertEqual(record["kind"], "retention-reference-verification")
        self.open_notebook()
        button = self.page.locator('[data-run="%s"]' % record["id"])
        row = self.page.locator("#runs tr").filter(has=button)
        expect(row).to_contain_text("Contract 002")
        expect(row.locator(".badge")).to_have_text("REFERENCE")
        button.click()
        expect(self.page.locator("#dialog-summary")).to_contain_text("Contract 002")
        expect(self.page.locator("#dialog-provenance")).to_contain_text("Reference verification; no model call")
        expect(self.page.locator('#artifact-links a[href$="/baseline.py"]')).to_be_visible()
        expect(self.page.locator('#artifact-links a[href$="/retention.py"]')).to_be_visible()
        self.page.locator("#close-dialog").click()
        self.page.get_by_role("button", name="AI rebuilds", exact=True).click()
        expect(self.page.locator('[data-run="%s"]' % record["id"])).to_have_count(0)
        self.page.get_by_role("button", name="All records", exact=True).click()
        self.page.locator("#language").click()
        expect(self.page.locator("#runs tr").filter(has=self.page.locator('[data-run="%s"]' % record["id"]))
               ).to_contain_text("契约 002")

    def test_contract_002_ai_profile_links_dependencies_and_preserves_failure_classification(self):
        original = next(r for r in self.ledger if r.get("contract_id") == "002"
                        and r["kind"] == "retention-reference-verification")
        base = json.loads((SITE / "evidence" / original["id"] / "report.json").read_text())
        pattern = "**/evidence/%s/report.json" % original["id"]
        for status in ("passed", "failed", "generation-failed"):
            report = dict(base, kind="ai-rebuild", verifier_profile="retention-ai-v1", status=status)
            row = dict(original, kind="ai-rebuild", verifier_profile="retention-ai-v1", status=status)
            if status == "generation-failed":
                report.update(checks=[], passed=0, total=0, execution="not-executed", error="Synthetic generation failure")
                row.update(passed=0, total=0)
            self.page.route("**/runs.json", lambda route: route.fulfill(json=[row]))
            self.page.route(pattern, lambda route: route.fulfill(json=report))
            self.page.goto(self.base, wait_until="networkidle")
            expect(self.page.locator("#ai-count")).to_have_text("1")
            expect(self.page.locator("#success-count")).to_have_text("1" if status == "passed" else "0")
            self.page.get_by_role("button", name="AI rebuilds", exact=True).click()
            expect(self.page.locator("#runs .badge")).to_have_text("AI REBUILD")
            expect(self.page.locator("#runs")).to_contain_text("Contract 002")
            self.page.locator("#runs [data-run]").click()
            for artifact in ("baseline.py", "retention.py", "records.py", "audit.py", "prompt.md"):
                expect(self.page.locator('#artifact-links a[href$="/%s"]' % artifact)).to_be_visible()
            if status == "generation-failed":
                expect(self.page.locator("#checks-list")).to_contain_text("Synthetic generation failure")
            self.page.locator("#close-dialog").click()
            self.page.unroute(pattern)
            self.page.unroute("**/runs.json")

    def test_container_results_and_runtime_errors_do_not_claim_local_execution(self):
        # Synthetic responses exercise modes without inventing a published run.
        row = next(r for r in self.ai if r["status"] == "passed")
        original = json.loads((SITE / "evidence" / row["id"] / "report.json").read_text())
        for failed in (False, True):
            with self.subTest(runtime_error=failed):
                report = dict(original, execution="docker-container-attempted" if failed else "docker-container")
                summary = dict(row)
                if failed:
                    report.update(status="verification-error", passed=0, total=0, checks=[],
                                  error="Synthetic runtime control; no acceptance verdict")
                    summary.update(status="verification-error", passed=0, total=0)
                self.page.route("**/runs.json", lambda route: route.fulfill(json=[summary]))
                pattern = "**/evidence/%s/report.json" % row["id"]
                self.page.route(pattern, lambda route: route.fulfill(json=report))
                self.page.goto(self.base, wait_until="networkidle")
                if failed:
                    self.page.locator("#outcome-filter").select_option("failed")
                    expect(self.page.locator("#runs [data-run]")).to_have_count(0)
                    self.page.locator("#outcome-filter").select_option("verification-error")
                    expect(self.page.locator("#runs .verdict")).to_have_text("VERIFICATION ERROR")
                self.page.locator("#runs [data-run]").click()
                expect(self.page.locator("#dialog-provenance")).not_to_contain_text("Local subprocess")
                expect(self.page.locator("#dialog-provenance")).to_contain_text(
                    "no acceptance verdict" if failed else "Docker container")
                if failed:
                    expect(self.page.locator("#checks-list")).to_contain_text(report["error"])
                self.page.locator("#close-dialog").click()
                self.page.locator("#language").click()
                if failed:
                    expect(self.page.locator("#runs .verdict")).to_have_text("验收环境错误")
                self.page.locator("#runs [data-run]").click()
                expect(self.page.locator("#dialog-provenance")).to_contain_text(
                    "未产生验收结论" if failed else "Docker 容器")
                self.page.locator("#close-dialog").click()
                self.page.locator("#language").click()
                self.page.unroute(pattern)
                self.page.unroute("**/runs.json")

    def test_unavailable_ledger_shows_an_actionable_error(self):
        self.page.route("**/runs.json", lambda route: route.fulfill(status=503, body="Unavailable"))
        self.page.goto(self.base, wait_until="networkidle")
        expect(self.page.locator("#load-error")).to_be_visible()
        expect(self.page.locator("#load-error")).to_contain_text("The ledger could not be loaded.")
        expect(self.page.locator("#runs [data-run]")).to_have_count(0)

    def test_evidence_permalink_opens_the_same_archived_run_in_a_fresh_context(self):
        self.open_notebook()
        row = next(row for row in self.ledger if row["kind"] != "ai-rebuild")
        run_id = row["id"]
        self.page.locator('[data-run="%s"]' % run_id).click()
        link = self.page.get_by_role("link", name="Permalink ↗", exact=True)
        expect(link).to_have_attribute("href", "?run=" + run_id)
        permalink = urljoin(self.page.url, link.get_attribute("href"))
        self.assertEqual(self.page.url, self.base)
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        page.add_init_script("localStorage.setItem('phoenix-language', 'zh')")
        page.goto(permalink)
        expect(page.locator("#evidence-dialog")).to_be_visible()
        expect(page.locator("#dialog-run-id")).to_have_text(run_id)
        expect(page.get_by_role("link", name="永久链接 ↗", exact=True)).to_have_attribute("href", "?run=" + run_id)
        page.keyboard.press("Escape")
        expect(page.locator("#evidence-dialog")).to_be_hidden()
        expect(page.locator("#runs [data-run]")).to_have_count(len(self.ledger))
        page.reload()
        expect(page.locator("#dialog-run-id")).to_have_text(run_id)
        expect(page.locator("#evidence-dialog")).to_be_visible()

    def test_unknown_permalink_leaves_the_ledger_usable_and_fetches_no_report(self):
        requests = []
        self.page.on("request", lambda request: requests.append(request.url))
        self.page.goto(self.base + "?run=..%2Fmissing", wait_until="networkidle")
        expect(self.page.locator("#load-error")).to_contain_text("This evidence link does not identify a published run.")
        expect(self.page.locator("#evidence-dialog")).to_be_hidden()
        self.assertFalse(any("report.json" in url for url in requests))
        expect(self.page.locator("#runs [data-run]")).to_have_count(len(self.ledger))
        self.page.locator("#language").click()
        expect(self.page.locator("#load-error")).to_have_text("此证据链接未指向已公开的实验记录。")
        self.page.locator("#runs [data-run]").first.click()
        expect(self.page.locator("#evidence-dialog")).to_be_visible()
        expect(self.page.locator("#load-error")).to_be_hidden()

    def test_permalink_refuses_a_report_for_a_different_run(self):
        requested = self.ledger[0]["id"]
        wrong = self.ledger[1]["id"]
        report = json.loads((SITE / "evidence" / wrong / "report.json").read_bytes())
        self.page.route("**/evidence/%s/report.json" % requested, lambda route: route.fulfill(json=report))
        self.page.goto(self.base + "?run=" + requested)
        expect(self.page.locator("#load-error")).to_contain_text("Could not load this report.")
        expect(self.page.locator("#evidence-dialog")).to_be_hidden()

    def test_late_report_cannot_replace_a_newer_evidence_selection(self):
        self.open_notebook()
        slow, latest = (row["id"] for row in self.ledger[:2])
        pattern = "**/evidence/%s/report.json" % slow
        pending = []
        self.page.route(pattern, lambda route: pending.append(route))
        slow_button = self.page.locator('[data-run="%s"]' % slow)
        slow_button.click()
        expect(slow_button).to_be_disabled()
        self.page.locator('[data-run="%s"]' % latest).click()
        expect(self.page.locator("#dialog-run-id")).to_have_text(latest)
        self.page.locator("#close-dialog").click()
        self.assertEqual(len(pending), 1)
        pending[0].continue_()
        expect(slow_button).to_be_enabled()
        expect(self.page.locator("#evidence-dialog")).to_be_hidden()

    def test_unavailable_report_allows_a_retry(self):
        self.open_notebook()
        route_pattern = "**/evidence/**/report.json"
        self.page.route(route_pattern, lambda route: route.fulfill(status=404, body="Unavailable"))
        button = self.page.locator('[data-run="%s"]' % self.ledger[0]["id"])
        button.click()
        expect(self.page.locator("#load-error")).to_contain_text("Could not load this report.")
        expect(button).to_be_enabled()
        expect(self.page.locator("#evidence-dialog")).to_be_hidden()
        self.page.unroute(route_pattern)
        button.click()
        expect(self.page.locator("#evidence-dialog")).to_be_visible()
        expect(button).to_be_enabled()


if __name__ == "__main__":
    unittest.main(verbosity=2)
