"""A second way into `raw/`, for when the browser paste will not go.

Reported, after three rounds on the save path: "here's an article he's still choking on,
please just fix this… I can't get this article into the system." The article saved fine
through both `/inbox/add` and `/inbox/edit` here, including under fifteen hostile variants
— lone surrogates, BOM, NUL, control characters, CRLF, lone CR, bidi overrides,
noncharacters, a `---` line, ten times the length — so whatever stops it is between that
browser and that server, and the user is blocked meanwhile.

`tools/add_story.py` is the way in that does not involve any of it. No network, no LLM, no
cost. It writes the same frontmatter the web capture writes, so the item appears in the
reading list and Wikify works on it identically.

Three things it does deliberately differently from the web route:

  * **Input is read as BYTES and decoded with errors="replace".** A clipboard that has
    been through a browser can carry a lone UTF-16 surrogate, and that is exactly what used
    to lose a story on the web route. A rescue tool that fails on the same bytes is not a
    rescue.
  * **The title is the headline, not the first line.** A browser copy opens with the site
    name, then often a byline and a reading time. Taking the first line titled this very
    article "nytimes.com", which is useless in a list of thirty.
  * **An empty slug cannot produce a dotfile.** `.md` is hidden, so the story would save
    and then be invisible in the listing — the same bug the web route had.
"""
import datetime
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
import agent
import add_story

ARTICLE = (
    "nytimes.com\n"
    "How Meta Uses A.I. Data Centers to Avoid Billions in Federal Taxes\n"
    "Kashmir Hill, Jesse Drucker, Eli Tan, Mike Isaac\n"
    "13–17 minutes\n\n"
    "Mark Zuckerberg says Meta’s A.I. push is a tremendous success.\n"
)


class _Base(unittest.TestCase):
    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()

    def tearDown(self):
        self.w.__exit__(None, None, None)

    def _run(self, *argv):
        sys.argv = ["add_story.py", *argv]
        return add_story.main()

    def _file(self, text=ARTICLE, name="in.txt", encoding="utf-8"):
        p = self.w.root / name
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding=encoding, errors="surrogatepass")
        return str(p)

    def _written(self):
        return next(self.w.raw.glob("*.md"))


class ItGetsTheStoryInTest(_Base):

    def test_it_writes_a_raw_file(self):
        self.assertEqual(self._run(self._file()), 0)
        self.assertTrue(self._written().exists())

    def test_the_whole_article_is_there(self):
        self._run(self._file())
        self.assertIn("Mark Zuckerberg says Meta", self._written().read_text("utf-8"))

    def test_the_title_is_the_headline_not_the_site_name(self):
        """A browser copy opens with 'nytimes.com'. Titling the story that is useless."""
        self._run(self._file())
        self.assertEqual(agent._fm_title(self._written().read_text("utf-8")),
                         "How Meta Uses A.I. Data Centers to Avoid Billions in Federal Taxes")

    def test_the_reading_time_line_is_not_the_title(self):
        self._run(self._file("site.com\n4–5 minutes\nThe Real Headline Goes Here\n"))
        self.assertEqual(agent._fm_title(self._written().read_text("utf-8")),
                         "The Real Headline Goes Here")

    def test_a_spaced_reading_time_line_is_not_the_title(self):
        """Five words, so the word-count test alone would take it."""
        self._run(self._file("site.com\n13 – 17 minutes read\nThe Real Headline Here\n"))
        self.assertEqual(agent._fm_title(self._written().read_text("utf-8")),
                         "The Real Headline Here")

    def test_an_explicit_title_wins(self):
        self._run(self._file(), "--title", "My Own Title")
        self.assertEqual(agent._fm_title(self._written().read_text("utf-8")), "My Own Title")

    def test_the_url_is_recorded(self):
        self._run(self._file(), "--url", "https://www.nytimes.com/x.html")
        self.assertIn("url: https://www.nytimes.com/x.html",
                      self._written().read_text("utf-8"))

    def test_it_is_not_marked_wikified(self):
        self._run(self._file())
        self.assertIn("wikified: false", self._written().read_text("utf-8"))

    def test_it_appears_in_the_reading_list(self):
        """The point of the tool — not merely a file on disk."""
        self._run(self._file())
        import serve
        saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        try:
            names = [i["name"] for i in serve.list_inbox()]
        finally:
            serve.RAW_DIR, serve.WIKI_DIR = saved
        self.assertIn(self._written().name, names)

    def test_it_goes_through_atomic_write(self):
        """Ownership handling, so it is safe to run as root beside the server."""
        from unittest import mock
        with mock.patch.object(agent, "_atomic_write") as aw:
            self._run(self._file())
        self.assertTrue(aw.called)


class HostileInputTest(_Base):

    def test_a_lone_surrogate_does_not_stop_it(self):
        """The bug that lost stories on the web route. A rescue tool must survive it."""
        raw = ("Headline Goes Here\n\nBody with \ud83d half an emoji.\n"
               ).encode("utf-8", "surrogatepass")
        self.assertEqual(self._run(self._file(raw)), 0)
        self.assertIn("half an emoji", self._written().read_text("utf-8"))

    def test_invalid_utf8_bytes_do_not_stop_it(self):
        self.assertEqual(self._run(self._file(b"Headline Here Now\n\n\xff\xfe body\n")), 0)

    def test_a_nul_byte_does_not_stop_it(self):
        self.assertEqual(self._run(self._file(b"Headline Here Now\n\n\x00body\n")), 0)

    def test_punctuation_only_content_is_not_a_dotfile(self):
        self._run(self._file("«» — ...\n"))
        self.assertFalse(self._written().name.startswith("."), self._written().name)

    def test_a_headline_with_a_quote_is_valid_yaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        self._run(self._file('The "Big Lie" goes to council\n\nBody here.\n'))
        fm = self._written().read_text("utf-8").split("---", 2)[1]
        self.assertEqual(yaml.safe_load(fm)["title"], 'The "Big Lie" goes to council')

    def test_empty_input_is_refused_rather_than_writing_nothing(self):
        self.assertEqual(self._run(self._file("   \n\n")), 1)
        self.assertEqual(list(self.w.raw.glob("*.md")), [])

    def test_an_existing_filename_is_refused_rather_than_overwritten(self):
        """Losing a story to a slug collision would be the bug this tool exists to dodge."""
        self._run(self._file())
        first = self._written()
        before = first.read_text("utf-8")
        self.assertEqual(self._run(self._file("x.txt"), "--name", first.name), 1)
        self.assertEqual(first.read_text("utf-8"), before)


class SlugTest(unittest.TestCase):

    def test_an_ordinary_headline(self):
        self.assertEqual(add_story.slugify("How Meta Uses A.I. Data Centers"),
                         "how-meta-uses-a-i-data-centers")

    def test_it_is_capped(self):
        self.assertLessEqual(len(add_story.slugify("word " * 100)), 60)

    def test_an_empty_slug_never_comes_back_empty(self):
        self.assertTrue(add_story.slugify("«» — ...").startswith("story-"))

    def test_it_never_starts_with_a_dot(self):
        for text in ("«» — ...", "...", "---", "   ", "Ψηφιακή"):
            self.assertFalse(add_story.slugify(text).startswith("."), text)


if __name__ == "__main__":
    unittest.main()
