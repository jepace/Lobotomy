"""A fetch gets JSON back, even when it is refused.

Observed, reported as "I copied and pasted an article and can't save it":

    Save failed: JSON.parse: unexpected character at line 1 column 1 of the JSON data

Nothing in that message names what went wrong or what to do, and the article the user had
just pasted was still sitting unsaved in the textarea.

The chain: the session had expired, `require_login` answered **302 to the login page**, and
`fetch` follows a redirect transparently — so the browser received the login page's HTML
with status **200**, and `await resp.json()` died on the `<` of `<!DOCTYPE`. Every one of
the ~32 `resp.json()` calls across these templates had the same shape, so the same
unreadable message stood in for an expired session, a 502 from a proxy, and a server error
alike.

Two halves, because the bug needed both:

  * `require_login` now answers **401 JSON** when the request came from a fetch, and keeps
    redirecting a browser NAVIGATION — which is what makes the login flow work at all, so
    this module pins both sides. The reply names the move (`login_required`, `login_url`),
    per write-path principle 4.
  * `apiFetch` in base.html reads status before body and turns anything that is not JSON
    into a sentence. It also covers what the server cannot make JSON: a proxy's HTML error
    page, a dropped connection, an abort.

The third thing this found is worse than the reported one. `editItem` swallowed its load
error and left the textarea EMPTY, which presents a failed load as an empty file — and
saving that box would have written the article away to nothing. It closes the editor now,
and `saveEdit` refuses a textarea that was never filled from disk.
"""
import re
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

TEMPLATES = Path(__file__).resolve().parent.parent / "tools" / "templates"
INBOX = TEMPLATES / "inbox.html"
BASE = TEMPLATES / "base.html"


class LoggedOutResponseTest(unittest.TestCase):
    """What the server says to a request it will not serve."""

    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        import serve
        self.serve = serve
        serve.app.config["TESTING"] = True
        self.c = serve.app.test_client()

    def tearDown(self):
        self.w.__exit__(None, None, None)

    def test_a_json_post_gets_json(self):
        r = self.c.post("/inbox/edit", json={"filename": "x.md", "content": "hello"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("application/json", r.headers.get("Content-Type", ""))

    def test_it_is_not_a_redirect(self):
        """The whole bug: fetch follows a 302 and hands resp.json() an HTML page."""
        r = self.c.post("/inbox/edit", json={"filename": "x.md", "content": "hi"})
        self.assertNotIn(r.status_code, (301, 302, 303, 307, 308))
        self.assertIsNone(r.headers.get("Location"))

    def test_the_reply_names_the_move(self):
        """Principle 4. 'Unauthorized' is not actionable; 'sign in again' is."""
        r = self.c.post("/inbox/edit", json={"filename": "x.md", "content": "hi"})
        body = r.get_json()
        self.assertTrue(body.get("login_required"))
        self.assertTrue(body.get("login_url"))
        self.assertIn("sign in", body.get("error", "").lower())

    def test_the_body_is_parseable_as_json(self):
        """The literal assertion the user's error message was about."""
        import json
        r = self.c.post("/inbox/edit", json={"filename": "x.md", "content": "hi"})
        json.loads(r.get_data(as_text=True))

    def test_a_get_from_fetch_also_gets_json(self):
        """editItem's load path, which failed the same way and then blanked the textarea."""
        r = self.c.get("/inbox/view/x.md", headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json().get("login_required"))

    def test_an_accept_json_request_gets_json(self):
        r = self.c.get("/inbox/view/x.md", headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 401)

    def test_a_browser_navigation_still_redirects_to_the_login_page(self):
        """Not a detail — without this the login flow breaks and nobody can sign in."""
        r = self.c.get("/inbox", headers={"Accept": "text/html,application/xhtml+xml"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/auth/login", r.headers["Location"])

    def test_the_redirect_still_carries_where_to_come_back_to(self):
        r = self.c.get("/inbox", headers={"Accept": "text/html"})
        self.assertIn("next=/inbox", r.headers["Location"])

    def test_a_navigation_with_no_accept_header_redirects(self):
        """curl and old clients send none; they must not get a 401 page they cannot use."""
        r = self.c.get("/inbox", headers={"Accept": ""})
        self.assertEqual(r.status_code, 302)


class TemplateInvariantTest(unittest.TestCase):
    """Every fetch goes through the one helper that can report a failure."""

    @classmethod
    def setUpClass(cls):
        cls.inbox = INBOX.read_text(encoding="utf-8")
        cls.base = BASE.read_text(encoding="utf-8")

    def test_the_helper_exists_and_is_global(self):
        self.assertIn("window.apiFetch = async function(url, opts)", self.base)

    def test_the_helper_marks_the_request_as_a_fetch(self):
        """The header require_login keys on; without it a navigation and a fetch are
        indistinguishable and one of them gets the wrong answer."""
        self.assertIn("'X-Requested-With': 'fetch'", self.base)

    def test_the_helper_reads_the_body_as_text_before_parsing(self):
        """resp.json() throws on the body it was given and tells you nothing about it."""
        self.assertIn("const body = await resp.text()", self.base)
        self.assertIn("JSON.parse(body)", self.base)

    def test_the_helper_recognises_an_html_page(self):
        self.assertRegex(self.base, r"doctype\|html")

    def test_the_inbox_calls_apiFetch_not_fetch_for_its_writes(self):
        for fn in ("saveEdit", "editItem"):
            m = re.search(rf"async function {fn}\(.*?\n\}}", self.inbox, re.DOTALL)
            self.assertIsNotNone(m, fn)
            self.assertIn("apiFetch(", m.group(0), f"{fn} does not use apiFetch")
            self.assertNotRegex(m.group(0), r"(?<!api)[^.\w]fetch\(",
                                f"{fn} still calls fetch directly")

    def test_no_bare_resp_json_survives_in_those_paths(self):
        for fn in ("saveEdit", "editItem"):
            m = re.search(rf"async function {fn}\(.*?\n\}}", self.inbox, re.DOTALL)
            self.assertNotIn("resp.json()", m.group(0), fn)

    def test_a_failed_load_does_not_leave_an_empty_textarea(self):
        """The worst of the three: an empty box reads as an empty file, and saving it
        would have written the article away to nothing."""
        m = re.search(r"async function editItem\(.*?\n\}", self.inbox, re.DOTALL)
        catch = m.group(0).split("catch", 1)[1]
        self.assertNotIn("textarea.value = ''", catch)
        self.assertIn("cancelEdit(name)", catch)

    def test_save_refuses_a_textarea_that_was_never_loaded(self):
        m = re.search(r"async function saveEdit\(.*?\n\}", self.inbox, re.DOTALL)
        self.assertIn("textarea.dataset.loaded !== '1'", m.group(0))

    def test_a_failed_save_stashes_the_draft(self):
        m = re.search(r"async function saveEdit\(.*?\n\}", self.inbox, re.DOTALL)
        catch = m.group(0).split("catch", 1)[1]
        self.assertIn("stashDraft(name, content)", catch)

    def test_a_successful_save_clears_the_draft(self):
        """Or the next open offers to restore text that is already on disk."""
        m = re.search(r"async function saveEdit\(.*?\n\}", self.inbox, re.DOTALL)
        self.assertIn("clearDraft(name)", m.group(0))

    def test_every_draft_access_is_wrapped(self):
        """localStorage can be absent or throw in a private window, and a draft is a
        convenience — it must never be able to break the editor it protects."""
        for fn in ("stashDraft", "loadDraft", "clearDraft"):
            m = re.search(rf"function {fn}\(.*?\n\}}", self.inbox, re.DOTALL)
            self.assertIsNotNone(m, fn)
            self.assertIn("catch", m.group(0), f"{fn} is unguarded")


def _lift(names):
    """Run the shipped functions, not a copy of them that can drift."""
    inbox = INBOX.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8")
    out = []
    for fn in names:
        src = base if fn == "apiFetch" else inbox
        pat = (r"window\.apiFetch = async function\(.*?\n    \};" if fn == "apiFetch"
               else rf"^(?:async )?function {fn}\(.*?^\}}")
        m = re.search(pat, src, re.MULTILINE | re.DOTALL)
        assert m, f"could not lift {fn}"
        out.append(m.group(0))
    return "\n".join(out)


class BrowserTest(unittest.TestCase):
    """apiFetch against the actual responses that produced the bug."""

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

    def _page(self, status, body, content_type="text/html"):
        """Served from a REAL origin, never set_content. On about:blank a relative
        fetch('/api-test') has no base, throws, and every assertion then fails for a reason
        that has nothing to do with the code under test — which is exactly how the first
        run of this module failed."""
        page = self.browser.new_page()
        page.route("**/api-test", lambda route: route.fulfill(
            status=status, content_type=content_type, body=body))
        page.route("http://lobotomy.test/page", lambda route: route.fulfill(
            status=200, content_type="text/html",
            body=f"<!DOCTYPE html><script>{_lift(['apiFetch'])}</script>"))
        page.goto("http://lobotomy.test/page")
        return page

    def _err(self, page):
        return page.evaluate("""async () => {
            try { await window.apiFetch('/api-test', {method:'POST'}); return null; }
            catch (e) { return {message: e.message, login: !!e.loginRequired}; }
        }""")

    def test_the_login_page_is_reported_as_a_sign_out(self):
        """The exact reported failure: HTML, status 200, after fetch followed the 302."""
        page = self._page(200, "<!DOCTYPE html>\n<html><head><title>Sign in</title>")
        err = self._err(page)
        self.assertIsNotNone(err)
        self.assertNotIn("JSON.parse", err["message"])
        self.assertNotIn("column 1", err["message"])
        self.assertIn("signed out", err["message"])
        page.close()

    def test_a_401_json_reply_is_flagged_as_login_required(self):
        page = self._page(401, '{"error":"Your session has expired — sign in again to '
                               'continue.","login_required":true,"login_url":"/auth/login"}',
                          content_type="application/json")
        err = self._err(page)
        self.assertTrue(err["login"])
        self.assertIn("session has expired", err["message"])
        page.close()

    def test_a_proxy_error_page_names_the_status(self):
        page = self._page(502, "<html><body>502 Bad Gateway</body></html>")
        err = self._err(page)
        self.assertIn("502", err["message"])
        self.assertNotIn("JSON.parse", err["message"])
        page.close()

    def test_a_500_is_not_reported_as_a_sign_out(self):
        """The second report. The first version of this helper guessed 'you may have been
        signed out' for ANY html body, and then said exactly that about a real HTTP 500
        from /inbox/edit — locally correct about what it saw, wrong about the move, since
        signing in again cannot fix a server fault. Principle 4, in the UI."""
        page = self._page(500, "<!DOCTYPE html><title>Internal Server Error</title>")
        msg = self._err(page)["message"]
        self.assertNotIn("signed out", msg)
        self.assertNotIn("sign in", msg.replace("signing in again will not help", ""))
        self.assertIn("500", msg)
        page.close()

    def test_a_500_says_where_to_look(self):
        """It is the server's fault, so the move is reading the server log."""
        page = self._page(500, "<!DOCTYPE html><title>Internal Server Error</title>")
        msg = self._err(page)["message"]
        self.assertIn("server log", msg)
        page.close()

    def test_a_401_login_page_still_says_sign_in(self):
        """The distinction has to cut both ways, or the first bug comes back."""
        page = self._page(200, "<!DOCTYPE html><title>Sign in</title>")
        self.assertIn("signed out", self._err(page)["message"])
        page.close()

    def test_a_json_error_reply_is_quoted(self):
        page = self._page(400, '{"error":"No filename"}', content_type="application/json")
        self.assertIn("No filename", self._err(page)["message"])
        page.close()

    def test_a_good_reply_comes_back_parsed(self):
        page = self._page(200, '{"ok":true,"filename":"x.md"}',
                          content_type="application/json")
        got = page.evaluate("async () => await window.apiFetch('/api-test', "
                            "{method:'POST'})")
        self.assertEqual(got, {"ok": True, "filename": "x.md"})
        page.close()

    def test_a_non_json_200_is_still_an_error(self):
        """A 200 that is not JSON is the bug's signature — it must not be mistaken for
        success just because the status was fine."""
        page = self._page(200, "totally not json", content_type="text/plain")
        self.assertIsNotNone(self._err(page))
        page.close()


if __name__ == "__main__":
    unittest.main()
