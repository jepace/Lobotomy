"""No template reads a response with a bare `.json()`.

This bug was reported three times, in three different places, and the third one was my
fault for scoping the fix too tightly:

  1. Saving a pasted article from the reading list — an expired session, `require_login`
     answered 302, `fetch` followed it, `resp.json()` met the login page's `<!DOCTYPE`.
  2. Saving again — a real HTTP 500, reported to the user as "you may have been signed
     out", a move that could not fix it.
  3. Editing a WIKI PAGE — `Error: JSON.parse: unexpected character at line 1 column 1`.

After (1) I fixed two call sites in `inbox.html` and wrote, in CLAUDE.md, that the
server-side 401 had "fixed all 52 protected routes at once, including the ~30 resp.json()
call sites nobody touched". **That was true only for the authentication case.** A server
can answer with something that is not JSON for reasons the server never sees — a proxy's
HTML 502 or 504, a request that never arrived, a response cut off mid-flight — and every
one of those thirty sites still turned it into "unexpected character at line 1 column 1".
Leaving a known-broken pattern in thirty places because the commonest cause was handled is
how the same bug gets reported three times.

All twenty-eight live sites now go through `apiFetch`, which reads the status and body
before parsing and produces a sentence naming what happened. This module is what stops the
twenty-ninth from being written: it fails on any `.json()` call on a fetch response
anywhere in `tools/templates/`.

The one allowed `JSON.parse` is inside `apiFetch` itself, which is the point — one place
that knows what to do when the body is not JSON.
"""
import re
import sys
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "tools" / "templates"


def _strip_comments(src: str) -> str:
    """Line and block comments, so prose ABOUT the bug does not look like the bug."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return "\n".join(re.sub(r"//.*$", "", ln) for ln in src.splitlines())


class NoBareJsonTest(unittest.TestCase):

    def _templates(self):
        return sorted(TEMPLATES.glob("*.html"))

    def test_every_template_exists_to_be_checked(self):
        """A guard over an empty set passes and proves nothing."""
        self.assertGreater(len(self._templates()), 5)

    def test_no_template_calls_json_on_a_response(self):
        bad = []
        for p in self._templates():
            src = _strip_comments(p.read_text(encoding="utf-8"))
            for m in re.finditer(r"(\w+)\.json\(\)", src):
                line = src[:m.start()].count("\n") + 1
                bad.append(f"{p.name}:{line}  {m.group(0)}")
        self.assertEqual(bad, [], "use apiFetch instead:\n  " + "\n  ".join(bad))

    def test_no_template_fetches_without_the_helper(self):
        """A plain fetch is allowed only where the body is a STREAM, never where it is an
        answer — the chat stream reads with a reader and must not be buffered as text."""
        allowed = ("/chat/stream/", "/chat/events/")
        bad = []
        for p in self._templates():
            src = _strip_comments(p.read_text(encoding="utf-8"))
            for m in re.finditer(r"(?<!\w)fetch\(([^\n]*)", src):
                if p.name == "base.html" and "fetch(url, opts)" in m.group(0):
                    continue                      # apiFetch's own call
                if any(a in m.group(1) for a in allowed):
                    continue
                line = src[:m.start()].count("\n") + 1
                bad.append(f"{p.name}:{line}  {m.group(0)[:60]}")
        self.assertEqual(bad, [], "use apiFetch instead:\n  " + "\n  ".join(bad))

    def test_the_helper_is_the_one_place_that_parses(self):
        base = _strip_comments((TEMPLATES / "base.html").read_text(encoding="utf-8"))
        self.assertEqual(len(re.findall(r"JSON\.parse\(", base)), 1)

    def test_the_detector_would_catch_a_regression(self):
        """A test that cannot fail proves nothing."""
        src = "const r = await fetch('/x');\nconst d = await r.json();\n"
        self.assertTrue(re.search(r"(\w+)\.json\(\)", _strip_comments(src)))

    def test_a_comment_mentioning_it_is_not_a_hit(self):
        src = "// this used to do resp.json() with no status check\nconst d = 1;\n"
        self.assertIsNone(re.search(r"(\w+)\.json\(\)", _strip_comments(src)))


if __name__ == "__main__":
    unittest.main()
