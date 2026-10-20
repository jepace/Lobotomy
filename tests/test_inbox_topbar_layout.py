"""The reading list's header overlapped itself on a phone.

Reported with a screenshot at iPhone width: *"This is kinda a mess on iPhone. Maybe
dripdowns for the filters?"* — the count rendered UNDERNEATH the filter buttons, and
"Wikify All" sat on top of "+ Save".

**Nothing was too small; everything was too wide and nothing was allowed to move.** The
bar was one `display:flex` row with no `flex-wrap`, and `flex-shrink: 0` on both the
filter group and the action group. A flex row that may not wrap and may not shrink does
not scroll either — it overflows, and `position: sticky` with a background then draws the
pieces over one another.

Two changes. The bar WRAPS, with the search-and-filter group carrying a `flex-basis` so
it takes a line of its own rather than crushing the row. And the three segmented filter
buttons became one `<select>`, which is what was asked for: the bar had no room for three
controls, and a native select shows its current value as its own label — the property the
segmented buttons existed to provide — while being a full-height tap target on iOS.

**The test is on the rendered PAGE, not on the stylesheet**, because every assertion worth
making here is about geometry: a CSS rule can be present and still not prevent an overlap.
It renders the real `/inbox` route and measures boxes in a real browser at 390×844.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harness  # noqa: F401  — copies config.json.example so `import serve` can work
import serve

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1200, "height": 900}


def _chromium(pw):
    """Whatever chromium is on disk: the pinned playwright build and the installed
    browser build drift apart, and the default launch then tells you to download one."""
    candidates = [None] + sorted(
        str(p) for pat in ("chromium*/chrome-linux/chrome",
                           "chromium_headless_shell*/chrome-linux/headless_shell")
        for p in Path("/opt/pw-browsers").glob(pat))
    last = None
    for exe in candidates:
        try:
            return pw.chromium.launch(executable_path=exe)
        except Exception as e:
            last = e
    raise unittest.SkipTest(f"chromium unavailable: {last}")


class TopbarLayoutTest(unittest.TestCase):
    BASE = "http://lobotomy.test/"

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright not installed")
        cls._pw = sync_playwright().start()
        try:
            cls.browser = _chromium(cls._pw)
        except Exception:
            cls._pw.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "browser"):
            cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        self.addCleanup(lambda: setattr(serve, "RAW_DIR", self._saved[0]))
        self.addCleanup(lambda: setattr(serve, "WIKI_DIR", self._saved[1]))
        serve._src_index_cache = None
        # A long title is the realistic case and the one that overflows; the screenshot
        # was taken over "Horror Novels Filled With Slow-Burn Dread…" at 222 items.
        for i in range(3):
            (self.w.raw / f"story-{i}.md").write_text(
                f'---\ntitle: "Horror Novels Filled With Slow-Burn Dread, Part {i}"\n'
                f"url: https://example.com/{i}\nsaved: 2026-01-01\nwikified: false\n"
                f"---\n\nBody {i}.\n", encoding="utf-8")
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def _html(self):
        r = self.c.get("/inbox")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:400])
        return r.get_data(as_text=True)

    def _page(self, viewport):
        """Served from a real origin rather than set_content: the page's own scripts
        issue relative fetches, which on about:blank have no base and throw."""
        html = self._html()
        page = self.browser.new_page(viewport=viewport)
        # Everything else the page reaches for (the polls, the queue badge) is answered
        # emptily, so a pending request can never be what an assertion is measuring.
        # Registered BEFORE the page's own route, because playwright matches the LAST
        # registered handler first — with these the other way round the catch-all served
        # `{}` for the NAVIGATION, and every assertion failed looking for an element on
        # a page that was never the reading list.
        page.route("**/*", lambda route: route.fulfill(
            status=200, content_type="application/json", body="{}"))
        page.route(self.BASE, lambda route: route.fulfill(
            status=200, content_type="text/html", body=html))
        page.goto(self.BASE)
        self.addCleanup(page.close)
        return page

    # ---- the reported symptom -------------------------------------------------

    def test_the_page_does_not_overflow_sideways_on_a_phone(self):
        page = self._page(PHONE)
        over = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(over, 0, f"the page scrolls {over}px sideways at 390px")

    def test_nothing_in_the_topbar_overlaps_on_a_phone(self):
        """The screenshot's actual fault. Two boxes that intersect is the finding; a
        stylesheet assertion could not have caught it, because the old CSS was valid."""
        page = self._page(PHONE)
        boxes = page.evaluate("""() => {
            const bar = document.querySelector('.page-topbar');
            const sel = 'h1, .topbar-count, #search-input, #status-filter,'
                      + ' .more-btn, .topbar-actions > .btn';
            return [...bar.querySelectorAll(sel)]
                .filter(e => e.offsetParent !== null || e.tagName === 'H1')
                .map(e => {
                    const r = e.getBoundingClientRect();
                    return {tag: e.id || e.className || e.tagName,
                            l: r.left, r: r.right, t: r.top, b: r.bottom};
                });
        }""")
        self.assertGreaterEqual(len(boxes), 4, f"the bar rendered almost nothing: {boxes}")
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = (a["l"] < b["r"] - 1 and b["l"] < a["r"] - 1
                           and a["t"] < b["b"] - 1 and b["t"] < a["b"] - 1)
                self.assertFalse(overlap, f"{a['tag']} overlaps {b['tag']}: {a} {b}")

    def test_every_topbar_control_is_inside_the_viewport_on_a_phone(self):
        """A control pushed off the right edge is unreachable: the bar does not scroll."""
        page = self._page(PHONE)
        bad = page.evaluate("""(w) => {
            const bar = document.querySelector('.page-topbar');
            const sel = '#search-input, #status-filter, .more-btn,'
                      + ' .topbar-actions > .btn';
            return [...bar.querySelectorAll(sel)]
                .filter(e => e.offsetParent !== null)
                .filter(e => { const r = e.getBoundingClientRect();
                               return r.right > w + 1 || r.left < -1; })
                .map(e => e.id || e.className);
        }""", PHONE["width"])
        self.assertEqual(bad, [], f"controls fall outside the viewport: {bad}")

    # ---- the dropdown that was asked for --------------------------------------

    def test_the_status_filter_is_a_real_select_showing_its_state(self):
        page = self._page(PHONE)
        self.assertEqual(page.eval_on_selector("#status-filter", "e => e.tagName"), "SELECT")
        self.assertEqual(
            page.eval_on_selector("#status-filter",
                                  "e => e.options[e.selectedIndex].textContent.trim()"),
            "All")

    def test_choosing_from_it_filters_the_list(self):
        """The control has to be wired to the one function that writes visibility —
        a dropdown that looks right and filters nothing is the worse failure."""
        page = self._page(PHONE)
        page.select_option("#status-filter", "done")
        shown = page.evaluate(
            "() => [...document.querySelectorAll('.reading-item')]"
            ".filter(e => e.style.display !== 'none').length")
        self.assertEqual(shown, 0, "no item is wikified, so Wikified should show none")
        page.select_option("#status-filter", "all")
        shown = page.evaluate(
            "() => [...document.querySelectorAll('.reading-item')]"
            ".filter(e => e.style.display !== 'none').length")
        self.assertEqual(shown, 3)

    # ---- one DOM, two presentations -------------------------------------------

    def test_the_overflow_button_appears_only_on_a_phone(self):
        narrow = self._page(PHONE)
        self.assertTrue(narrow.is_visible(".more-btn"))
        wide = self._page(DESKTOP)
        self.assertFalse(wide.is_visible(".more-btn"),
                         "the ⋯ button is showing on a wide screen, beside the buttons "
                         "it is supposed to be standing in for")

    def test_the_actions_are_inline_on_a_wide_screen(self):
        page = self._page(DESKTOP)
        self.assertTrue(page.is_visible("#wikify-all-btn"),
                        "Wikify All is hidden on a desktop, where there is room for it")

    def test_the_actions_are_behind_the_button_on_a_phone_and_open_from_it(self):
        page = self._page(PHONE)
        self.assertFalse(page.is_visible("#wikify-all-btn"))
        page.click(".more-btn")
        self.assertTrue(page.is_visible("#wikify-all-btn"),
                        "the menu opened and its contents did not appear")

    def test_the_menu_closes_when_you_tap_away(self):
        """A <details> stays open until something closes it, and an open panel sits over
        the first rows of the list."""
        page = self._page(PHONE)
        page.click(".more-btn")
        self.assertTrue(page.eval_on_selector("#topbar-more", "e => e.classList.contains('open')"))
        page.click("h1")
        self.assertFalse(page.eval_on_selector("#topbar-more", "e => e.classList.contains('open')"))

    def test_the_actions_exist_once_in_the_dom(self):
        """Not a tidiness point: rendering them twice and hiding one copy means a change
        to one is a bug in the other, which this project has already paid for in the
        reading list's two row renderers."""
        html = self._html()
        self.assertEqual(html.count('id="wikify-all-btn"'), 1)

    def test_the_save_button_is_reachable_without_opening_anything(self):
        """It is the primary action and the one the empty state tells you to use, so it
        stays out of the menu at every width."""
        page = self._page(PHONE)
        self.assertTrue(page.is_visible(".topbar-actions > .btn-primary"))


if __name__ == "__main__":
    unittest.main()
