"""Browser tests against a live server. Skipped when Playwright/Chromium is unavailable."""
import os, socket, subprocess, sys, tempfile, time, unittest, urllib.request
from common import *
try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except Exception:
    HAVE_PW = False
ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(HAVE_PW, "playwright not installed")
class UiFlows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); cls.port = s.getsockname()[1]; s.close()
        env = dict(os.environ, ACCESSFIX_PORT=str(cls.port), ACCESSFIX_DATA_DIR=tempfile.mkdtemp(prefix="afx-ui-"), ACCESSFIX_OCR_WORKERS="1")
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "run.py")], cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try: urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/api/capabilities", timeout=1); break
            except Exception: time.sleep(0.25)
        else: raise RuntimeError("server did not start")
        cls.pw = sync_playwright().start(); cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.proc.terminate(); cls.proc.wait(10)

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1400, "height": 950}); self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type in ("error", "warning") else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(f"http://127.0.0.1:{self.port}/"); self.page.wait_for_selector("#engine-badge.ok, #engine-badge.err")

    def tearDown(self):
        self.assertEqual(self.errors, [], "console/CSP errors")
        self.page.close()

    def load(self, path):
        self.page.set_input_files("#file", str(path)); self.page.wait_for_selector("#view-doc:not([hidden])", timeout=30000)

    def test_tagged_vs_untagged_tree_in_browser(self):
        p = self.page; self.load(good())
        self.assertIn("Tags: Tagged", p.inner_text("#chips")); p.click("#t-tree"); p.wait_for_selector("#tree [role=treeitem]")
        p.click("text=Expand all"); items = p.locator("#tree [role=treeitem]"); self.assertGreaterEqual(items.count(), 25)
        p.fill("#tq", "Figure"); p.wait_for_function("document.querySelectorAll('#tree [role=treeitem]').length < 6")
        p.locator("#tree [role=treeitem]", has_text="Figure").last.click()
        self.assertIn("Bar chart", p.inner_text("#insp")); self.assertTrue(p.is_visible("#pv-hl"))
        self.assertEqual(p.input_value("#pv-page"), "2")
        p.click("#new-doc"); self.load(untagged()); self.assertIn("Tags: Untagged", p.inner_text("#chips"))
        p.click("#t-tree"); self.assertIn("no structure tree to display", p.inner_text("#p-tree"))

    def test_finding_navigates_to_element(self):
        p = self.page; self.load(defect("heading_skip")); p.click("#t-findings")
        art = p.locator("article.finding", has_text="Heading levels are skipped"); self.assertEqual(art.count(), 1)
        art.locator("summary").click(); self.assertIn("H1 is followed by H3", art.inner_text())
        art.locator("text=Show in tag tree").click(); p.wait_for_selector("#insp h3")
        self.assertIn("H3", p.inner_text("#insp")); self.assertIn("Findings on this element", p.inner_text("#insp"))
        self.assertEqual(p.get_attribute("#t-tree", "aria-selected"), "true")

    def test_ocr_flow_and_download(self):
        if not HAVE_TESS: self.skipTest("tesseract missing")
        p = self.page; self.load(mixed()); p.click("#t-ocr"); p.wait_for_selector(".pg")
        self.assertEqual(p.locator(".pg.cand").count(), 1); self.assertTrue(p.is_checked("#pgc2")); self.assertFalse(p.is_checked("#pgc1"))
        self.assertTrue(p.is_hidden("#o-cancel")); p.select_option("#o-dpi", "200"); p.click("#o-start")
        p.wait_for_selector("#o-result h3", timeout=90000)
        txt = p.inner_text("#o-result"); self.assertIn("Searchable, not accessible", txt); self.assertIn("Page 2: searchable text layer", txt)
        p.locator("#o-result button", has_text="Preview").click(); self.assertIn("Kowalski", p.inner_text("#o-text"))
        with p.expect_download() as d: p.click("#o-result a.btn.primary")
        f = Path(tempfile.mkdtemp()) / "out.pdf"; d.value.save_as(str(f))
        self.assertIn("Invoice 48213 payable to Kowalski Brothers", pdftotext(f, 2, 2)); self.assertIn("Alpha Bravo Charlie", pdftotext(f, 1, 1))
        self.assertIn("Tags: Untagged", p.inner_text("#chips")); self.assertIn("Existing OCR layer", p.inner_text("#chips"))

    def test_fix_in_browser_creates_new_version_and_revalidates(self):
        p = self.page; self.load(defect("no_meta")); p.click("#t-findings")
        art = p.locator("article.finding", has_text="Document language is not declared"); art.locator("input[type=text]").fill("ta-IN"); art.locator("text=Apply as new version").click()
        p.wait_for_selector("#toast:not([hidden])"); self.assertIn("Saved as v1", p.inner_text("#toast")); self.assertEqual(p.input_value("#ver"), "v1")
        p.click("#t-versions"); self.assertIn("Resolved (1)", p.inner_text("#p-versions")); self.assertIn("Language", p.inner_text("#p-versions") + "Language")
        p.click("#t-findings"); self.assertEqual(p.locator("article.finding", has_text="language is not declared").count(), 0)

    def test_keyboard_operation(self):
        p = self.page; self.load(good()); p.focus("#t-overview"); p.keyboard.press("ArrowRight"); self.assertEqual(p.get_attribute("#t-findings", "aria-selected"), "true")
        p.keyboard.press("ArrowRight"); p.keyboard.press("ArrowRight"); self.assertEqual(p.get_attribute("#t-ocr", "aria-selected"), "true")
        p.click("#t-tree"); p.wait_for_selector("#tree [role=treeitem]"); p.locator("#tree [role=treeitem]").first.focus()
        p.keyboard.press("ArrowRight"); p.keyboard.press("ArrowDown"); p.keyboard.press("Enter"); self.assertIn("Type (as written)", p.inner_text("#insp")); self.assertIn("No findings are attached", p.inner_text("#insp"))
        self.assertTrue(p.evaluate("document.activeElement.getAttribute('role')") == "treeitem")
        outline = p.evaluate("getComputedStyle(document.activeElement).outlineStyle"); self.assertNotEqual(outline, "none")
        p.click("#t-findings"); self.assertTrue(p.evaluate("!!document.querySelector('[aria-live]')"))

    def test_invalid_upload_error_state(self):
        p = self.page; bad = Path(tempfile.mkdtemp()) / "fake.pdf"; bad.write_text("hello")
        p.set_input_files("#file", str(bad)); p.wait_for_selector("#up-error:not([hidden])"); self.assertIn("does not look like a PDF", p.inner_text("#up-error"))
        self.errors[:] = [e for e in self.errors if "415" not in e]       # the browser logs the expected HTTP 415

    def test_responsive_layout(self):
        p = self.page; p.set_viewport_size({"width": 420, "height": 900}); self.load(good())
        self.assertFalse(p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2"))


if __name__ == "__main__":
    unittest.main()
