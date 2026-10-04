"""Search says it is working, and says so when it fails.

Reported: "search is slow, so sometimes I can't tell if it is searching or not working."
There was no busy state at all. Between the keystroke and the results the popup kept
showing the PREVIOUS query's hits, which does not read as "pending" — it reads as a wrong
answer, and the slower the search the longer it reads that way. Search greps the whole
wiki on every query, so several seconds is normal.

Two things were wrong, and the second is the worse one:

  * **No indication.** Now a spinner and "Searching the wiki…", but only after **180ms**.
    Showing it immediately makes every fast search flicker, which is its own noise; 180ms
    is under the threshold where a wait registers as a wait, so a quick search goes
    straight from old results to new and only a slow one explains itself.
  * **`doSearch` had no error handling.** `apiFetch` throws now, so a failed search was an
    unhandled rejection: the popup kept the previous query's results and the message went
    to the console. **A search that FAILED looked exactly like a search that found those
    older things.** It reports the failure in the popup instead.

The error text is escaped through a `searchEsc` local to this scope — the search IIFE had
no escaper, and an error message carries text the server chose, a proxy's HTML page among
them. `No results for "..."` is escaped for the same reason.

The CSS is lifted from the template alongside the script, as the hovercard tests do:
`prefers-reduced-motion` is a behaviour, and asserting it against a stylesheet the test
wrote itself would prove nothing.
"""
import re
import sys
import time
import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "tools" / "templates" / "wiki.html"


def _script():
    """The search IIFE and apiFetch, both from what ships."""
    src = TEMPLATE.read_text(encoding="utf-8")
    i = src.index("function highlight(text, q)")
    start = src.rindex("(function", 0, i)
    end = src.index("})();", src.index("inp.addEventListener('input'", start)) + len("})();")
    base = (TEMPLATE.parent / "base.html").read_text(encoding="utf-8")
    bs = base.index("window.apiFetch = async function(url, opts)")
    be = base.index("\n    };", bs) + len("\n    };")
    return base[bs:be] + "\n" + src[start:end]


def _close(page):
    """Drop routes before closing. A request still in flight when the page goes away
    raises inside the handler, and playwright surfaces that on the NEXT new_page()."""
    page.unroute_all(behavior="ignoreErrors")
    page.close()


def _styles():
    src = TEMPLATE.read_text(encoding="utf-8")
    start = src.index("  .search-result {")
    end = src.index("</style>", start)
    return src[start:end]


class StructureTest(unittest.TestCase):
    """What the Python suite can check without a browser."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def test_there_is_a_busy_state(self):
        self.assertIn("search-busy", self.src)
        self.assertIn("Searching the wiki", self.src)

    def test_the_spinner_is_delayed_rather_than_immediate(self):
        """Immediate makes every fast search flicker."""
        self.assertRegex(self.src, r"\}, 180\);")

    def test_a_failed_search_is_caught(self):
        m = re.search(r"async function doSearch\(q\)[\s\S]*?\n  \}", self.src)
        self.assertIsNotNone(m)
        self.assertIn("catch (e)", m.group(0))
        self.assertIn("search-error", m.group(0))

    def test_the_error_text_is_escaped(self):
        """It carries text the server chose — a proxy's HTML page among them."""
        self.assertIn("searchEsc(e.message)", self.src)

    def test_the_query_is_escaped_in_the_no_results_line(self):
        self.assertIn('No results for "${searchEsc(q)}"', self.src)

    def test_reduced_motion_is_honoured_without_hiding_it(self):
        """Someone who turns motion off still needs to know the search is running, so it
        pulses rather than stopping."""
        css = _styles()
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("search-pulse", css)


class BrowserTest(unittest.TestCase):
    """The real script against a real origin, as the hovercard tests do."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright not installed")
        cls._pw = sync_playwright().start()
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

    def _page(self, delay_ms=0, status=200, body='{"results":[],"total":0}'):
        page = self.browser.new_page()

        # The route fulfils AT ONCE and the delay is applied inside the browser, by
        # wrapping fetch in the fixture. Sleeping in the handler instead blocks
        # playwright's own driver, so the test cannot look at the page while the request
        # is in flight — which is the entire thing these assertions are about. Two of them
        # passed that way by luck, observing the DOM only after the sleep ended.
        page.route("**/wiki/search*", lambda route: route.fulfill(
            status=status, content_type="application/json", body=body))
        page.route("http://lobotomy.test/page", lambda route: route.fulfill(
            status=200, content_type="text/html",
            body=f"""<!DOCTYPE html><style>{_styles()}</style>
            <input id="wiki-search"><div id="wiki-search-results"></div>
            <script>
            (function () {{
              const DELAY = {delay_ms};
              if (!DELAY) return;
              const real = window.fetch;
              window.fetch = (u, o) =>
                new Promise(res => setTimeout(() => res(real(u, o)), DELAY));
            }})();
            </script>
            <script>{_script()}</script>"""))
        page.goto("http://lobotomy.test/page")
        return page

    def _type(self, page, q="tariffs"):
        page.fill("#wiki-search", q)
        page.dispatch_event("#wiki-search", "input")

    def test_a_slow_search_shows_the_spinner(self):
        page = self._page(delay_ms=900)
        self._type(page)
        page.wait_for_selector(".search-spinner", timeout=3000)
        self.assertIn("Searching", page.inner_text("#wiki-search-results"))
        _close(page)

    def test_the_spinner_goes_away_when_results_arrive(self):
        # 1200ms, not 400: the spinner appears at debounce+180 and the results replace it
        # at debounce+delay, so a short delay leaves a ~200ms window to observe and the
        # test fails on timing rather than on behaviour.
        page = self._page(delay_ms=1200,
                          body='{"results":[{"path":"entities/a.md","title":"A","excerpt":"x"}],'
                               '"total":1}')
        self._type(page)
        page.wait_for_selector(".search-spinner", timeout=3000)
        page.wait_for_selector(".search-result", timeout=5000)
        self.assertEqual(page.query_selector_all(".search-spinner"), [])
        _close(page)

    def test_a_fast_search_never_shows_it(self):
        """The reason for the 180ms delay — a flicker on every keystroke is its own noise."""
        page = self._page(delay_ms=0,
                          body='{"results":[{"path":"entities/a.md","title":"A","excerpt":"x"}],'
                               '"total":1}')
        self._type(page)
        page.wait_for_selector(".search-result", timeout=5000)
        self.assertEqual(page.query_selector_all(".search-spinner"), [])
        _close(page)

    def test_a_failed_search_says_so_rather_than_showing_stale_results(self):
        """The worse half of the report: a failure looked like a successful search that
        happened to return the previous query's hits."""
        page = self._page(status=500, body="<!DOCTYPE html><title>502 Bad Gateway</title>")
        self._type(page)
        page.wait_for_selector(".search-error", timeout=5000)
        txt = page.inner_text("#wiki-search-results")
        self.assertIn("Search failed", txt)
        _close(page)

    def test_a_failed_search_does_not_inject_the_servers_markup(self):
        page = self._page(status=500,
                          body="<!DOCTYPE html><title><img src=x onerror=alert(1)></title>")
        self._type(page)
        page.wait_for_selector(".search-error", timeout=5000)
        self.assertEqual(page.query_selector_all("#wiki-search-results img"), [])
        _close(page)

    def test_no_results_escapes_the_query(self):
        page = self._page(body='{"results":[],"total":0}')
        page.fill("#wiki-search", "<img src=x onerror=alert(1)>")
        page.dispatch_event("#wiki-search", "input")
        page.wait_for_selector(".search-no-results", timeout=5000)
        self.assertEqual(page.query_selector_all("#wiki-search-results img"), [])
        _close(page)


if __name__ == "__main__":
    unittest.main()
