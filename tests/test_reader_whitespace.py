"""The reading view's whitespace, and where it came from.

Asked to strip extra blank lines out of the reader. Measured first, and the markdown
renderer was not the problem — it already collapses two blank lines and twenty into the
same paragraph break. The blank lines were real, in the text itself, and `_clip_fetch` put
them there:

    handle_data appends the whitespace BETWEEN tags, so HTML indented like ordinary HTML
    yields "First.\\n\\n  \\n\\n    \\n\\nSecond." — lines that LOOK blank but hold spaces.

`re.sub(r"\\n{3,}", "\\n\\n", ...)` cannot match those, because the spaces sit between the
newlines. **Three paragraphs of normally-indented HTML produced ELEVEN blank-looking
lines.** Stripping trailing whitespace first turns each into a genuinely empty line, and
the existing collapse then works.

Fixed in both places, deliberately:

  * **`_clip_fetch`**, so new captures are clean ON DISK. That is not cosmetic — this text
    is what every ingest round re-sends to the model, so the blank lines were being paid
    for over and over.
  * **`inbox_view`**, for display only, because everything already captured still carries
    them and the file is never rewritten to fix a reading view.

**Leading indentation is left alone in both.** An indented line that has content is a code
block, and flattening those to tidy spacing would be a worse fault than the one being
fixed — so the substitution is trailing-only.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import serve

HTML = """<article>
  <p>First paragraph.</p>

  <div>
    <p>Second paragraph.</p>
  </div>

  <section>
     <p>Third.</p>
  </section>
</article>"""


def _blank_lines(text):
    return sum(1 for l in text.splitlines() if not l.strip())


class TidyForReadingTest(unittest.TestCase):

    def test_blank_looking_lines_collapse(self):
        raw = "First.\n\n  \n\n    \n\nSecond.\n  \n\n  \n\n     \n\nThird."
        self.assertEqual(serve._tidy_for_reading(raw), "First.\n\nSecond.\n\nThird.")

    def test_a_single_blank_line_between_paragraphs_survives(self):
        """Removing it would run the paragraphs together — markdown needs the break."""
        self.assertEqual(serve._tidy_for_reading("One.\n\nTwo."), "One.\n\nTwo.")

    def test_trailing_spaces_go(self):
        """Two trailing spaces are a markdown hard line break, so they were adding <br>s
        nobody typed."""
        self.assertEqual(serve._tidy_for_reading("A line  \nanother"), "A line\nanother")

    def test_indented_content_is_untouched(self):
        """An indented line with content is a code block."""
        out = serve._tidy_for_reading("intro\n\n    def f():\n        return 1\n\nafter")
        self.assertIn("    def f():", out)
        self.assertIn("        return 1", out)

    def test_leading_and_trailing_blank_lines_go(self):
        self.assertEqual(serve._tidy_for_reading("\n\n\nBody.\n\n\n"), "Body.")

    def test_empty_input_is_safe(self):
        for v in ("", None, "   \n  \n"):
            self.assertEqual(serve._tidy_for_reading(v), "")

    def test_it_is_idempotent(self):
        once = serve._tidy_for_reading("a\n\n  \n\nb")
        self.assertEqual(serve._tidy_for_reading(once), once)


class FetcherWhitespaceTest(unittest.TestCase):
    """The source of the blank lines, fixed where they are produced."""

    def _fetched(self, html):
        from unittest import mock

        class _Resp:
            headers = {"Content-Type": "text/html; charset=utf-8"}
            def read(self, n=None): return html.encode("utf-8")
            def __enter__(self): return self
            def __exit__(self, *a): return False

        # urllib is imported INSIDE _clip_fetch, so there is no serve.urllib to patch;
        # the module itself is the only handle.
        import urllib.request
        with mock.patch.object(urllib.request, "urlopen", return_value=_Resp()):
            text, err = serve._clip_fetch("https://example.com/a")
        self.assertIsNone(err, err)
        return text

    def test_indented_html_does_not_produce_blank_looking_lines(self):
        text = self._fetched(HTML)
        self.assertEqual(_blank_lines(text), 2, repr(text))

    def test_the_paragraphs_all_survive(self):
        text = self._fetched(HTML)
        for p in ("First paragraph.", "Second paragraph.", "Third."):
            self.assertIn(p, text)

    def test_paragraphs_are_still_separated(self):
        """Collapsing too far would run them into one block."""
        text = self._fetched(HTML)
        self.assertIn("First paragraph.\n\nSecond paragraph.", text)

    def test_the_stored_text_is_what_an_ingest_re_sends(self):
        """Why this is fixed at the fetcher and not only in the view: every ingest round
        re-sends this text, so the blank lines were paid for repeatedly."""
        text = self._fetched(HTML)
        self.assertLess(len(text), len(HTML))


class ReaderRouteTest(unittest.TestCase):
    """End to end, and the file must not be rewritten to tidy a view."""

    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True
        self.messy = ('---\ntitle: "T"\nurl: https://example.com/a\n---\n\n'
                      'First paragraph.\n\n  \n\n    \n\nSecond paragraph.\n')
        (self.w.raw / "a.md").write_text(self.messy, encoding="utf-8")

    def tearDown(self):
        serve.RAW_DIR, serve.WIKI_DIR = self._saved
        self.w.__exit__(None, None, None)

    def _view(self):
        r = self.c.get("/inbox/view/a.md")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def test_the_reader_text_is_tidied(self):
        self.assertEqual(_blank_lines(self._view()["content"]), 1)

    def test_the_html_has_both_paragraphs(self):
        html = self._view()["html"]
        self.assertIn("First paragraph.", html)
        self.assertIn("Second paragraph.", html)

    def test_the_html_has_no_empty_paragraphs(self):
        self.assertNotIn("<p></p>", self._view()["html"])

    def test_the_file_on_disk_is_not_rewritten(self):
        """Display-only. A reading view must never edit the source it is reading."""
        self._view()
        self.assertEqual((self.w.raw / "a.md").read_text(encoding="utf-8"), self.messy)

    def test_the_source_url_is_returned_for_the_link(self):
        self.assertEqual(self._view()["url"], "https://example.com/a")


class TemplateTest(unittest.TestCase):
    """The presentational half."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parent.parent / "tools" / "templates"
                   / "inbox.html").read_text(encoding="utf-8")

    def test_the_reader_has_a_measure(self):
        """Prose across a wide window is hard to track line to line."""
        self.assertRegex(self.src, r"\.item-reader-html \{[^}]*max-width: 70ch")

    def test_empty_paragraphs_are_hidden(self):
        self.assertIn(".item-reader-html p:empty { display: none; }", self.src)

    def test_the_original_is_linked_when_there_is_text_to_read(self):
        """It used to appear only when there was NOTHING to read, which is backwards —
        'is this the whole article?' is the question you have while reading it."""
        m = re.search(r"\} else if \(data\.html\) \{[\s\S]*?reader\.appendChild\(div\);",
                      self.src)
        self.assertIsNotNone(m)
        self.assertIn("reader-source", m.group(0))
        self.assertIn("Open original", m.group(0))

    def test_the_link_is_built_as_a_node_not_interpolated(self):
        """data.url comes from frontmatter a human pasted; textContent and .href keep it
        out of the markup rather than trusting it."""
        m = re.search(r"\} else if \(data\.html\) \{[\s\S]*?reader\.appendChild\(div\);",
                      self.src)
        self.assertIn("createElement('a')", m.group(0))
        self.assertNotRegex(m.group(0), r'innerHTML\s*=\s*`[^`]*\$\{data\.url\}')


if __name__ == "__main__":
    unittest.main()
