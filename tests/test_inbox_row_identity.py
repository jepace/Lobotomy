"""A reading-list row is identified by its filename, never by its position.

Observed: clicking Wikify on the SECOND item started wikification on the FIRST.

Rows were addressed as `item-<n>` / `wikify-<n>` / `progress-<n>`, numbered at render time
by Jinja's `loop.index`. The inbox polls `/inbox/list` every 8 seconds and PREPENDS
newly-arrived rows, numbering them against the FRESH list — so a new arrival and the
original first row both became `wikify-1`, and `getElementById` returns whichever comes
first in the document, which is the prepended one. What you see as item #2 is the original
first row, still carrying id 1; clicking it drove item #1's button and progress bar.

Mostly that was confusing rather than harmful, because every server call already sends the
filename: the right article was ingested, archived or deleted. **saveEdit was the exception
and it lost data** — it read `edit-body-<n>`, which resolved to the wrong row's textarea,
and POSTed that content under the right filename, overwriting one `raw/` file with another
row's box.

Two halves to this module. The structural half asserts the invariant in the template, so a
future hand-rolled `getElementById('x-' + idx)` is caught. The browser half runs the real
helper functions, lifted out of the template by regex, against a real DOM that contains the
duplicate-row condition — because the Python suite cannot otherwise reach any of this, and
the bug lived entirely in the browser.
"""
import re
import shutil
import sys
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "tools" / "templates" / "inbox.html"


class TemplateInvariantTest(unittest.TestCase):
    """Row identity must not be positional anywhere in the template."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def test_no_positional_row_ids_are_rendered(self):
        """Two of each could end up in one document, which is what caused the bug."""
        for stem in ("item-", "wikify-", "progress-", "reader-", "edit-", "edit-body-"):
            for idx in ("{{ loop.index }}", "${idx}"):
                self.assertNotIn(f'id="{stem}{idx}"', self.src,
                                 f"positional id {stem}{idx} is back")

    def test_no_element_is_looked_up_by_index(self):
        self.assertNotRegex(self.src, r"getElementById\(['\"][a-z-]+-['\"]\s*\+\s*idx\)")
        self.assertNotRegex(self.src, r"querySelector\(`#item-\$\{idx\}")

    def test_every_row_carries_its_name(self):
        # Both renderers: the Jinja loop and the poll's prepend path.
        self.assertIn('data-name="{{ item.name | e }}"', self.src)
        self.assertIn("data-name=\"${item.name.replace(/\"/g, '&quot;')}\"", self.src)

    def test_the_row_helpers_exist(self):
        self.assertIn("function rowFor(name)", self.src)
        self.assertIn("function partFor(name, sel)", self.src)

    def test_handlers_take_a_name_and_no_index(self):
        for fn in ("toggleItem", "archiveItem", "unarchiveItem", "deleteItem",
                   "editItem", "saveEdit", "wikifyItem"):
            m = re.search(rf"function {fn}\(([^)]*)\)", self.src)
            self.assertIsNotNone(m, fn)
            self.assertEqual(m.group(1).strip(), "name",
                             f"{fn} still takes an index")
        m = re.search(r"function cancelEdit\(([^)]*)\)", self.src)
        self.assertEqual(m.group(1).strip(), "name")

    def test_wikify_all_reads_names_off_the_rows(self):
        """It used to select by id prefix and dig the filename back out of the onclick
        attribute with a regex, which also broke on any name containing an apostrophe."""
        self.assertNotIn('[id^="wikify-"]', self.src)
        self.assertIn("r.dataset.name", self.src)


def _helpers_js():
    """The real rowFor/partFor, lifted from the template so the test runs the shipped code
    rather than a copy that can drift away from it."""
    src = TEMPLATE.read_text(encoding="utf-8")
    out = []
    for fn in ("rowFor", "partFor"):
        m = re.search(rf"^function {fn}\(.*?^\}}", src, re.MULTILINE | re.DOTALL)
        assert m, f"could not lift {fn} out of the template"
        out.append(m.group(0))
    return "\n".join(out)


@unittest.skipIf(shutil.which("node") is None and True is False, "")
class BrowserTest(unittest.TestCase):
    """The half that actually reproduces the bug: a document holding two rows, as the poll
    leaves it after prepending an arrival."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright not installed")
        cls._pw = sync_playwright().start()
        # Launch whatever chromium is actually on disk. The pinned playwright build number
        # and the installed browser build number drift apart, and the default launch then
        # fails telling you to download one — which a test must not do. Any chromium can run
        # two <div>s and a function.
        candidates = [None] + sorted(
            str(p) for pat in ("chromium*/chrome-linux/chrome",
                               "chromium_headless_shell*/chrome-linux/headless_shell")
            for p in Path("/opt/pw-browsers").glob(pat))
        last = None
        for exe in candidates:
            try:
                cls.browser = cls._pw.chromium.launch(executable_path=exe)
                return
            except Exception as e:
                last = e
        cls._pw.stop()
        raise unittest.SkipTest(f"chromium unavailable: {last}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "browser"):
            cls.browser.close()
            cls._pw.stop()

    def _page(self):
        """Two rows. The FIRST in document order is the one the poll prepended — exactly the
        arrangement in which the old positional lookup returned the wrong element."""
        page = self.browser.new_page()
        page.set_content(f"""
        <div id="list">
          <div class="reading-item" data-name="new-arrival.md">
            <div class="item-meta">meta A</div>
            <div class="item-actions">
              <button class="act-btn wikify">Wikify</button>
            </div>
            <div class="item-edit"><textarea>TEXT-A</textarea></div>
            <div class="item-progress"></div>
            <div class="item-reader"></div>
          </div>
          <div class="reading-item" data-name="the-one-i-clicked.md">
            <div class="item-meta">meta B</div>
            <div class="item-actions">
              <button class="act-btn wikify">Wikify</button>
            </div>
            <div class="item-edit"><textarea>TEXT-B</textarea></div>
            <div class="item-progress"></div>
            <div class="item-reader"></div>
          </div>
        </div>
        <script>{_helpers_js()}</script>
        """)
        return page

    def test_the_row_found_is_the_one_named(self):
        page = self._page()
        self.assertEqual(
            page.evaluate("rowFor('the-one-i-clicked.md').querySelector('.item-meta')"
                          ".textContent"),
            "meta B")
        page.close()

    def test_the_progress_bar_belongs_to_the_named_row(self):
        """The reported symptom: clicking row two lit up row one."""
        page = self._page()
        page.evaluate("partFor('the-one-i-clicked.md', '.item-progress')"
                      ".textContent = 'WORKING'")
        rows = page.evaluate(
            "[...document.querySelectorAll('.reading-item')]"
            ".map(r => r.dataset.name + '=' + r.querySelector('.item-progress').textContent)")
        self.assertEqual(rows, ["new-arrival.md=", "the-one-i-clicked.md=WORKING"])
        page.close()

    def test_the_textarea_read_is_the_named_rows(self):
        """The data-loss path. Reading the wrong textarea here meant saving row A's text
        into row B's file."""
        page = self._page()
        self.assertEqual(
            page.evaluate("partFor('the-one-i-clicked.md', '.item-edit textarea').value"),
            "TEXT-B")
        self.assertEqual(
            page.evaluate("partFor('new-arrival.md', '.item-edit textarea').value"),
            "TEXT-A")
        page.close()

    def test_an_unknown_name_returns_nothing_rather_than_a_wrong_row(self):
        page = self._page()
        self.assertIsNone(page.evaluate("rowFor('not-here.md')"))
        self.assertIsNone(page.evaluate("partFor('not-here.md', '.item-progress')"))
        page.close()

    def test_a_name_with_quotes_and_brackets_still_resolves(self):
        """Why dataset.name is compared directly instead of through a CSS attribute
        selector: a filename can carry characters that would need escaping there."""
        page = self.browser.new_page()
        page.set_content(
            '<div class="reading-item" data-name="odd [name] &quot;quoted&quot;.md">'
            '<div class="item-meta">odd</div></div>'
            f"<script>{_helpers_js()}</script>")
        self.assertEqual(
            page.evaluate("""rowFor('odd [name] "quoted".md')
                             .querySelector('.item-meta').textContent"""),
            "odd")
        page.close()


if __name__ == "__main__":
    unittest.main()
