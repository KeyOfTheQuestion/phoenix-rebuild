"""Real browser checks for the static notebook; run explicitly, outside unit discovery."""
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
import json
from pathlib import Path
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

    def test_contract_navigation_loads_the_published_specification(self):
        self.open_notebook()
        self.page.get_by_role("link", name="The contract", exact=True).click()
        specification = (SITE / "contract.md").read_text(encoding="utf-8")
        expect(self.page.locator("#specification")).to_have_text(specification)
        self.assertEqual(self.page.locator("#specification").text_content(), specification)
        self.page.get_by_role("link", name="← Back to the notebook", exact=True).click()
        expect(self.page.locator("#runs [data-run]")).to_have_count(len(self.ledger))

    def test_unavailable_ledger_shows_an_actionable_error(self):
        self.page.route("**/runs.json", lambda route: route.fulfill(status=503, body="Unavailable"))
        self.page.goto(self.base, wait_until="networkidle")
        expect(self.page.locator("#load-error")).to_be_visible()
        expect(self.page.locator("#load-error")).to_contain_text("The ledger could not be loaded.")
        expect(self.page.locator("#runs [data-run]")).to_have_count(0)

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
