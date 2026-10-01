"""A failed save says what failed, and names a move that can fix it.

Reported twice, with two different causes behind one message.

The first was an expired session: `require_login` answered 302, `fetch` followed it, and
`resp.json()` met the login page's `<!DOCTYPE`. Fixed by answering 401 JSON to a fetch.

The second was a real **HTTP 500** from `/inbox/edit`, and the client — which had just
learned to recognise an HTML body — reported it as *"You may have been signed out — reload
the page and sign in."* That is write-path principle 4's exact failure in the UI: a message
locally correct about what it saw (an HTML page) and wrong about the move, because signing
in again cannot fix a server fault. The status picks the explanation now.

Three things make the next one diagnosable instead of guessable:

  * an `errorhandler` that logs the traceback to `lobotomy.serve` — Flask's default puts it
    only on `app.logger` — and answers a fetch in JSON;
  * `inbox_edit` catching `OSError` and naming the file, the errno and the likeliest cause.
    These writes do NOT go through `agent._atomic_write` (raw/ is outside the wiki), so they
    get none of its ownership handling, and this project's standing hazard is a file left
    root-owned by a maintenance tool run beside a server running as another user;
  * the `.url` promotion routing its title through `fm_quote`. It interpolated into
    `title: "{title}"`, the defect already documented for `create_file`. News headlines
    carry quotes, and `The "Big Lie" goes to council` was written as
    `title: "The "Big Lie" goes to council"`.

    **Measured before asserting, and the first version of this module asserted the wrong
    thing.** It claimed the two readers in this codebase disagree about such a line. They
    do not: `fm_scalar` strips only MATCHED outer pairs, so `_fm_title` and
    `serve._parse_frontmatter` both recover the right title by luck. The damage is that
    the file is **not valid YAML** — PyYAML raises `ParserError` on it — so every consumer
    outside these two hand-rolled readers is wrong about the page. That is what is
    asserted, structurally always and against a real parser where one is installed.

The permission case is injected rather than provoked: these tests may run as root, and root
ignores the mode bits, so chmod proves nothing.
"""
import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class _Base(unittest.TestCase):
    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        import serve
        self.serve = serve
        # serve.py does `from agent import RAW_DIR`, which binds at IMPORT — so once any
        # other module in the suite has imported serve, its copy still points at the real
        # raw/ and every request here answers "File not found". The module-globals trap,
        # one module further out than usual. Rebound per test and restored after.
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def tearDown(self):
        self.serve.RAW_DIR, self.serve.WIKI_DIR = self._saved
        self.w.__exit__(None, None, None)

    def _md(self, name="x.md", fm='---\ntitle: "T"\n---\n\nold body\n'):
        (self.w.raw / name).write_text(fm, encoding="utf-8")
        return name


class UnwritableFileTest(_Base):
    """The likeliest cause of the reported 500, given raw/ bypasses _atomic_write."""

    def _save(self, name):
        err = PermissionError(13, "Permission denied")
        with mock.patch.object(Path, "write_text", side_effect=err):
            return self.c.post("/inbox/edit",
                               json={"filename": name, "content": "new text"})

    def test_it_is_json_not_an_html_500(self):
        r = self._save(self._md())
        self.assertEqual(r.status_code, 500)
        self.assertIn("application/json", r.headers.get("Content-Type", ""))

    def test_it_names_the_file(self):
        r = self._save(self._md("monterey.md"))
        self.assertIn("monterey.md", r.get_json()["error"])

    def test_it_names_the_reason_not_just_that_it_failed(self):
        r = self._save(self._md())
        body = r.get_json()
        self.assertIn("Permission denied", body["error"])
        self.assertEqual(body["errno"], 13)

    def test_it_names_a_move_that_could_fix_it(self):
        """Principle 4. 'The server hit an error' is true and unactionable; the owner of
        the file is something the user can go and look at."""
        r = self._save(self._md())
        self.assertIn("own", r.get_json()["error"])

    def test_it_does_not_blame_the_session(self):
        """The reported wrong turn: a 500 told the user to sign in again."""
        self.assertNotIn("sign in", self._save(self._md())["error"].lower()
                         if isinstance(self._save(self._md()), dict)
                         else self._save(self._md()).get_json()["error"].lower())

    def test_a_url_promotion_failure_is_caught_too(self):
        (self.w.raw / "p.url").write_text("Title\nURL: https://e.com/x\n", encoding="utf-8")
        r = self._save("p.url")
        self.assertEqual(r.status_code, 500)
        self.assertIn("p.url", r.get_json()["error"])

    def test_the_original_file_is_left_alone(self):
        """A failed promotion must not unlink the .url it could not replace."""
        (self.w.raw / "p.url").write_text("Title\nURL: https://e.com/x\n", encoding="utf-8")
        self._save("p.url")
        self.assertTrue((self.w.raw / "p.url").exists())

    def test_a_normal_save_still_works(self):
        name = self._md()
        r = self.c.post("/inbox/edit", json={"filename": name, "content": "new text"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("new text", (self.w.raw / name).read_text(encoding="utf-8"))


class UnhandledErrorTest(_Base):
    """Anything else that crashes mid-request."""

    def _boom(self, name):
        with mock.patch.object(self.serve.re, "match", side_effect=RuntimeError("boom")):
            return self.c.post("/inbox/edit",
                               json={"filename": name, "content": "x"})

    def test_a_crash_answers_a_fetch_in_json(self):
        r = self._boom(self._md())
        self.assertEqual(r.status_code, 500)
        self.assertIn("application/json", r.headers.get("Content-Type", ""))
        self.assertIn("RuntimeError", r.get_json()["error"])

    def test_it_says_the_fault_is_the_server_s(self):
        self.assertIn("fault on the server", self._boom(self._md())["error"]
                      if isinstance(self._boom(self._md()), dict)
                      else self._boom(self._md()).get_json()["error"])

    def test_the_traceback_reaches_our_log(self):
        """Flask's default puts it only on app.logger, so it is missing from the file
        everything else is in — which is where someone goes looking."""
        records = []

        class _C(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _C()
        self.serve.log.addHandler(h)
        try:
            self._boom(self._md())
        finally:
            self.serve.log.removeHandler(h)
        hit = [r for r in records if "unhandled error serving" in r.getMessage()]
        self.assertTrue(hit, [r.getMessage() for r in records])
        self.assertIsNotNone(hit[0].exc_info, "logged without the traceback")

    def test_a_404_is_not_reported_as_a_crash(self):
        """HTTPExceptions are answers, not faults — turning them into 500s would hide
        every legitimate not-found."""
        records = []

        class _C(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _C()
        self.serve.log.addHandler(h)
        try:
            r = self.c.get("/raw/does-not-exist.md")
        finally:
            self.serve.log.removeHandler(h)
        self.assertEqual(r.status_code, 404)
        self.assertEqual([r for r in records if "unhandled" in r.getMessage()], [])


class UrlPromotionTitleTest(_Base):
    """The latent defect found in the same function: unescaped interpolation."""

    def _promote(self, first_line):
        (self.w.raw / "p.url").write_text(f"{first_line}\nURL: https://e.com/x\n",
                                          encoding="utf-8")
        r = self.c.post("/inbox/edit", json={"filename": "p.url", "content": "body"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return (self.w.raw / "p.md").read_text(encoding="utf-8")

    def _fm_line(self, text, key="title"):
        return next(l for l in text.splitlines() if l.startswith(key + ":"))

    def test_a_quoted_headline_is_escaped_on_disk(self):
        """The structural half, which needs no YAML parser and so always runs: the quote
        inside the value is escaped, which is exactly what interpolation did not do."""
        line = self._fm_line(self._promote('The "Big Lie" goes to council'))
        self.assertIn(r'\"', line)
        self.assertNotIn('"The "Big Lie"', line)

    def test_a_quoted_headline_is_valid_yaml(self):
        """The real damage. Both hand-rolled readers here recover the right title from the
        unescaped form by luck — fm_scalar strips only matched outer pairs — so asserting
        on them proves nothing. A real parser is the honest check."""
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        text = self._promote('The "Big Lie" goes to council')
        fm = text.split("---", 2)[1]
        self.assertEqual(yaml.safe_load(fm)["title"], 'The "Big Lie" goes to council')

    def test_the_unescaped_form_really_is_invalid(self):
        """Proves the test above can fail — otherwise it asserts nothing about escaping."""
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load('title: "The "Big Lie" goes to council"\n')

    def test_a_quoted_headline_round_trips(self):
        text = self._promote('The "Big Lie" goes to council')
        self.assertEqual(agent._fm_title(text), 'The "Big Lie" goes to council')

    def test_a_plain_headline_is_unchanged(self):
        text = self._promote("Rent stabilization goes to the ballot box")
        self.assertEqual(agent._fm_title(text), "Rent stabilization goes to the ballot box")

    def test_a_backslash_round_trips(self):
        text = self._promote(r"Trade war: US\Canada")
        self.assertEqual(agent._fm_title(text), r"Trade war: US\Canada")

    def test_a_colon_round_trips(self):
        """Plain YAML would read everything before the colon as a key."""
        text = self._promote("Analysis: why the tariffs bite")
        self.assertEqual(agent._fm_title(text), "Analysis: why the tariffs bite")


if __name__ == "__main__":
    unittest.main()
