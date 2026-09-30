"""Hover a wiki link, get a peek at the page it goes to.

Two halves, because they fail in different ways.

The endpoint half is ordinary Python: /api/wiki/<path>/preview returns the title, type,
blurb and section names for one page. The blurb comes from `agent.first_desc_line`, which
was hoisted out of `_rebuild_index` for this — the same function that writes the wiki
index, so a page's index entry and the card you get hovering a link to it cannot say
different things.

The browser half is the part the Python suite cannot reach at all, and where this kind of
feature actually breaks: a card that swallows the click it is describing, one that fires
while the pointer is merely crossing a link, one that re-fetches on every mouseover, one
that is invisible to the keyboard. Playwright drives the real template for those.
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase, TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent
import serve

TEMPLATE = Path(__file__).resolve().parent.parent / "tools" / "templates" / "wiki.html"


class PreviewEndpointTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self._saved = (serve.WIKI_DIR, serve.REPO_ROOT)
        serve.WIKI_DIR, serve.REPO_ROOT = self.w.wiki, self.w.root
        self.addCleanup(lambda: setattr(serve, "WIKI_DIR", self._saved[0]))
        self.addCleanup(lambda: setattr(serve, "REPO_ROOT", self._saved[1]))
        serve.app.config["TESTING"] = True
        self.c = serve.app.test_client()
        with self.c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "t"

    def _get(self, rel):
        return self.c.get(f"/api/wiki/{rel}/preview")

    def test_it_returns_title_type_and_blurb(self):
        self.w.page("entities/newsom.md", title="Gavin Newsom", type="entity",
                    body="# Gavin Newsom\n\n## Overview\n\nGavin Newsom is the governor of "
                         "[California](../entities/california.md), elected in 2018.\n")
        d = json.loads(self._get("entities/newsom.md").get_data(as_text=True))
        self.assertEqual(d["title"], "Gavin Newsom")
        self.assertEqual(d["type"], "entity")
        self.assertEqual(d["snippet"],
                         "Gavin Newsom is the governor of California, elected in 2018.")
        self.assertEqual(d["url"], "/wiki/entities/newsom")

    def test_the_blurb_has_links_flattened(self):
        """Raw markdown on a hover card would be worse than no card. This is why the
        index's own helper is reused rather than a second one written here."""
        self.w.page("concepts/x.md", title="X", type="concept",
                    body="# X\n\n## Definition\n\nSee [Y](../concepts/y.md) for context.\n")
        d = json.loads(self._get("concepts/x.md").get_data(as_text=True))
        self.assertEqual(d["snippet"], "See Y for context.")
        self.assertNotIn("](", d["snippet"])

    def test_it_agrees_with_the_index(self):
        self.w.page("entities/a.md", title="A", type="entity",
                    body="# A\n\n## Overview\n\nA thing worth describing.\n")
        d = json.loads(self._get("entities/a.md").get_data(as_text=True))
        self.assertEqual(d["snippet"],
                         agent.first_desc_line(self.w.disk("entities/a.md")))

    def test_sections_are_listed_and_sources_is_not(self):
        self.w.page("entities/b.md", title="B", type="entity",
                    body="# B\n\n## Overview\n\nx\n\n## Career\n\ny\n\n## Sources\n\n- a\n")
        d = json.loads(self._get("entities/b.md").get_data(as_text=True))
        self.assertEqual(d["sections"], ["Overview", "Career"])

    def test_a_page_with_no_prose_yet_returns_an_empty_blurb(self):
        self.w.page("entities/c.md", title="C", type="entity", body="# C\n\n## Overview\n")
        d = json.loads(self._get("entities/c.md").get_data(as_text=True))
        self.assertEqual(d["snippet"], "")
        self.assertEqual(d["title"], "C")

    def test_a_missing_page_is_404_not_a_traceback(self):
        self.assertEqual(self._get("entities/nope.md").status_code, 404)

    def test_it_does_not_escape_the_wiki(self):
        self.assertIn(self._get("../../etc/passwd").status_code, (400, 404))

    def test_it_writes_nothing(self):
        self.w.page("entities/d.md", title="D", type="entity",
                    body="# D\n\n## Overview\n\nx\n")
        before = self.w.disk("entities/d.md")
        self._get("entities/d.md")
        self.assertEqual(self.w.disk("entities/d.md"), before)


def _script():
    """The hovercard IIFE, lifted out of the real template so the test cannot drift from
    what ships."""
    src = TEMPLATE.read_text(encoding="utf-8")
    start = src.index("// Hover cards:")
    start = src.rindex("(function () {", 0, src.index("const DWELL", start))
    end = src.index("})();", src.index("Escape", start)) + len("})();")
    return src[start:end]


def _styles():
    """The .hovercard rules, lifted from the same template. Injected alongside the script
    so the browser tests check the CSS that ships — pointer-events:none is a behaviour,
    not decoration, and asserting it against a stylesheet the test wrote itself would
    prove nothing."""
    src = TEMPLATE.read_text(encoding="utf-8")
    start = src.index("  /* Hover cards.")
    end = src.index("</style>", start)
    return src[start:end]


class BrowserTest(unittest.TestCase):
    """What the Python suite cannot reach. These are the ways a hovercard goes wrong."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright not installed")
        cls._pw = sync_playwright().start()
        # Launch whatever chromium is on disk: the pinned playwright build and the
        # installed browser build drift apart, and the default launch then tells you to
        # run "playwright install", which a test must never do.
        cands = [None] + sorted(
            str(p) for pat in ("chromium*/chrome-linux/chrome",
                               "chromium_headless_shell*/chrome-linux/headless_shell")
            for p in Path("/opt/pw-browsers").glob(pat))
        last = None
        for exe in cands:
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

    # The document is SERVED from a real origin rather than set with set_content. On
    # about:blank a relative fetch('/api/...') has no base to resolve against, throws, and
    # the catch swallows it — so no card is ever built and every assertion here fails for
    # a reason that has nothing to do with the code under test.
    BASE = "http://wiki.test/"

    def _serve(self, body_html, payload=None, status=200):
        page = self.browser.new_page(viewport={"width": 1000, "height": 700})
        html = (f"<!doctype html><html><head><style>{_styles()}</style></head>"
                f"<body>{body_html}<script>{_script()}</script></body></html>")
        page.route(self.BASE, lambda route: route.fulfill(
            status=200, content_type="text/html", body=html))
        if payload is None:
            page.route("**/api/wiki/**/preview", lambda route: route.fulfill(status=status))
        else:
            page.route("**/api/wiki/**/preview", lambda route: route.fulfill(
                status=status, content_type="application/json", body=json.dumps(payload)))
        page.goto(self.BASE)
        return page

    def _page(self, sections=2):
        return self._serve(
            """<div class="wiki-content" style="padding:40px">
                 <p>Text before
                   <a href="/wiki/entities/newsom" id="L">Gavin Newsom</a>
                   and <a href="https://example.com" id="EXT">external</a>
                   and <a href="#top" id="ANCH">anchor</a>.</p>
               </div>""",
            {"title": "Gavin Newsom", "type": "entity",
             "snippet": "Governor of California, elected in 2018.",
             "sections": [f"Sec {i}" for i in range(sections)],
             "url": "/wiki/entities/newsom"})

    def _hover_and_wait(self, page, sel="#L"):
        page.hover(sel)
        try:
            page.wait_for_selector(".hovercard", timeout=1500)
        except Exception:
            return False
        return True

    def test_hovering_a_wiki_link_shows_a_card(self):
        page = self._page()
        self.assertTrue(self._hover_and_wait(page))
        self.assertIn("Gavin Newsom", page.inner_text(".hovercard"))
        self.assertIn("Governor of California", page.inner_text(".hovercard"))
        page.close()

    def test_the_card_cannot_swallow_the_click(self):
        """pointer-events:none. A preview that intercepts the click it is describing is
        worse than no preview."""
        page = self._page()
        self._hover_and_wait(page)
        self.assertEqual(
            page.eval_on_selector(".hovercard",
                                  "e => getComputedStyle(e).pointerEvents"), "none")
        page.close()

    def test_an_external_link_gets_no_card(self):
        page = self._page()
        page.hover("#EXT")
        page.wait_for_timeout(600)
        self.assertEqual(page.query_selector_all(".hovercard"), [])
        page.close()

    def test_an_anchor_link_gets_no_card(self):
        page = self._page()
        page.hover("#ANCH")
        page.wait_for_timeout(600)
        self.assertEqual(page.query_selector_all(".hovercard"), [])
        page.close()

    def test_moving_away_hides_it(self):
        page = self._page()
        self._hover_and_wait(page)
        page.hover("#EXT")
        page.wait_for_timeout(200)
        self.assertEqual(page.query_selector_all(".hovercard"), [])
        page.close()

    def test_a_distinct_link_is_fetched_once_however_often_it_is_hovered(self):
        """A wiki page carries many links to few targets; re-hovering must not re-ask."""
        calls = []
        page = self.browser.new_page(viewport={"width": 1000, "height": 700})
        html = (f'<!doctype html><html><head><style>{_styles()}</style></head><body>'
                '<div class="wiki-content" style="padding:40px">'
                '<p><a href="/wiki/entities/t" id="L">T</a> and <a href="#x" id="O">o</a></p>'
                f"</div><script>{_script()}</script></body></html>")
        page.route(self.BASE, lambda route: route.fulfill(
            status=200, content_type="text/html", body=html))

        def handler(route):
            calls.append(route.request.url)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"title": "T", "type": "entity",
                                           "snippet": "s", "sections": []}))
        page.route("**/api/wiki/**/preview", handler)
        page.goto(self.BASE)
        for _ in range(3):
            page.hover("#L")
            page.wait_for_timeout(450)
            page.hover("#O")
            page.wait_for_timeout(120)
        self.assertEqual(len(calls), 1, calls)
        page.close()

    def test_a_passing_pointer_does_not_fire_a_request(self):
        """The dwell delay. Without it, dragging the mouse across a paragraph fires a
        request per link it crosses."""
        calls = []
        page = self.browser.new_page(viewport={"width": 1000, "height": 700})
        html = (f'<!doctype html><html><head><style>{_styles()}</style></head><body>'
                '<div class="wiki-content" style="padding:40px">'
                '<p><a href="/wiki/entities/t" id="L">T</a> <a href="#x" id="O">o</a></p>'
                f"</div><script>{_script()}</script></body></html>")
        page.route(self.BASE, lambda route: route.fulfill(
            status=200, content_type="text/html", body=html))
        page.route("**/api/wiki/**/preview", lambda r: (calls.append(1), r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"title": "T", "type": "", "snippet": "s", "sections": []}))))
        page.goto(self.BASE)
        page.hover("#L")
        page.wait_for_timeout(80)          # well under the dwell
        page.hover("#O")
        page.wait_for_timeout(500)
        self.assertEqual(calls, [], "fired while the pointer was only passing over")
        page.close()

    def test_keyboard_focus_shows_it_too(self):
        """Hover alone makes the feature invisible to anyone not using a mouse."""
        page = self._page()
        page.eval_on_selector("#L", "e => e.focus()")
        page.wait_for_selector(".hovercard", timeout=1500)
        self.assertIn("Gavin Newsom", page.inner_text(".hovercard"))
        page.close()

    def test_escape_dismisses_it(self):
        page = self._page()
        self._hover_and_wait(page)
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        self.assertEqual(page.query_selector_all(".hovercard"), [])
        page.close()

    def test_sections_are_shown_only_when_there_are_several(self):
        few = self._page(sections=3)
        self._hover_and_wait(few)
        self.assertNotIn("sections", few.inner_text(".hovercard"))
        few.close()
        many = self._page(sections=9)
        self._hover_and_wait(many)
        self.assertIn("9 sections", many.inner_text(".hovercard"))
        many.close()

    def test_the_markup_is_escaped(self):
        page = self._serve(
            '<div class="wiki-content" style="padding:40px">'
            '<p><a href="/wiki/entities/t" id="L">T</a></p></div>',
            {"title": "<img src=x onerror=alert(1)>", "type": "entity",
             "snippet": "<b>bold</b>", "sections": []})
        page.hover("#L")
        page.wait_for_selector(".hovercard", timeout=1500)
        self.assertEqual(page.query_selector_all(".hovercard img"), [])
        self.assertEqual(page.query_selector_all(".hovercard b"), [])
        self.assertIn("<b>bold</b>", page.inner_text(".hovercard"))
        page.close()

    def test_a_failed_fetch_does_not_leave_a_broken_card(self):
        page = self._serve(
            '<div class="wiki-content" style="padding:40px">'
            '<p><a href="/wiki/entities/t" id="L">T</a></p></div>',
            payload=None, status=500)
        page.hover("#L")
        page.wait_for_timeout(700)
        cards = page.query_selector_all(".hovercard")
        if cards:
            self.assertIn("No preview", page.inner_text(".hovercard"))
        page.close()


if __name__ == "__main__":
    unittest.main()
