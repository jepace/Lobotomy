"""Capturing a story, and the background fetch that must not eat it.

Reported as "I'm getting the can't-add-the-story-content error every 4-6 stories… just let
me write the story." Intermittent, which is the shape of a race, and this is the race:

  1. Save a URL. `/inbox/add` writes a placeholder with `fetch_failed: true`, returns
     immediately, and fetches the page on a background thread. Returning at once is right.
  2. The user opens that reading-list row and pastes the article text in, because the site
     paywalled it or the fetch is slow. `/inbox/edit` saves it. Correct, and on disk.
  3. Seconds later the fetch lands and `_atomic_write`s the file **with the site's
     boilerplate**, discarding what the user wrote.

Reproduced end to end before the fix: paste, wait, and the story is gone. No error, nothing
in the raw file's history (raw/ has none), nothing to say where it went. Whether it bites
depends on whether the fetch lands before or after the paste, which is exactly "every 4-6".

The fix is an exactness test, not a timestamp or a flag: the capture tells the fetch which
body it wrote, and the fetch declines if the file no longer carries it. A slow fetch landing
after an edit is then the only case skipped.

Three more defects in the same route, found while reading it:

  * `title: "{title}"` interpolated without escaping — a third and fourth site of the
    `create_file` defect. The fetch path's title comes from the ARTICLE'S OWN HEADLINE,
    which routinely carries quotes.
  * `dest.write_text(content)` bypassed `_atomic_write`, so a pasted note inherited none of
    the ownership handling every other write in this project goes through.
  * An empty slug made the filename `.txt` — a DOTFILE. The story saved and then was
    nowhere to be seen, because the listing skips hidden files. Content opening with
    punctuation or in a non-Latin script is enough.
"""
import sys
import time
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
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def tearDown(self):
        self.serve.RAW_DIR, self.serve.WIKI_DIR = self._saved
        self.w.__exit__(None, None, None)

    def _add(self, content):
        r = self.c.post("/inbox/add", json={"content": content})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        return r.get_json()["filename"]

    def _body(self, name):
        return (self.w.raw / name).read_text(encoding="utf-8")


class FetchRaceTest(_Base):
    """The reported one."""

    def _slow_fetch(self, delay=0.4, text="SITE BOILERPLATE"):
        def _f(url):
            time.sleep(delay)
            return (text, None)
        return _f

    def test_a_paste_survives_the_fetch_landing_after_it(self):
        with mock.patch.object(self.serve, "_clip_fetch", self._slow_fetch()):
            name = self._add("https://example.com/a-story")
            self.c.post("/inbox/edit",
                        json={"filename": name, "content": "MY OWN PASTED STORY"})
            time.sleep(1.0)
        self.assertIn("MY OWN PASTED STORY", self._body(name))

    def test_the_fetched_boilerplate_does_not_replace_it(self):
        with mock.patch.object(self.serve, "_clip_fetch", self._slow_fetch()):
            name = self._add("https://example.com/a-story")
            self.c.post("/inbox/edit",
                        json={"filename": name, "content": "MY OWN PASTED STORY"})
            time.sleep(1.0)
        self.assertNotIn("SITE BOILERPLATE", self._body(name))

    def test_an_untouched_capture_is_still_patched(self):
        """The whole point of the background fetch — skipping it always would be a
        'fix' that removes the feature."""
        with mock.patch.object(self.serve, "_clip_fetch",
                               lambda u: ("REAL ARTICLE BODY", None)):
            name = self._add("https://example.com/b-story")
            time.sleep(0.5)
        self.assertIn("REAL ARTICLE BODY", self._body(name))

    def test_a_failed_fetch_leaves_the_placeholder_marked(self):
        with mock.patch.object(self.serve, "_clip_fetch", lambda u: (None, "boom")):
            name = self._add("https://example.com/c-story")
            time.sleep(0.4)
        self.assertIn("fetch_failed", self._body(name))

    def test_a_failed_fetch_does_not_eat_a_paste_either(self):
        """The failure branch rewrites the file too, so it needs the same guard."""
        def _f(url):
            time.sleep(0.4)
            return (None, "boom")
        with mock.patch.object(self.serve, "_clip_fetch", _f):
            name = self._add("https://example.com/d-story")
            self.c.post("/inbox/edit",
                        json={"filename": name, "content": "PASTED BY HAND"})
            time.sleep(1.0)
        self.assertIn("PASTED BY HAND", self._body(name))


class FilenameTest(_Base):

    def test_punctuation_only_content_does_not_become_a_dotfile(self):
        """`.txt` is hidden, so the story saved and then could not be seen."""
        name = self._add("«» — ...")
        self.assertFalse(name.startswith("."), name)
        self.assertTrue((self.w.raw / name).exists())

    def test_non_latin_content_does_not_become_a_dotfile(self):
        name = self._add("Ψηφιακή ιστορία")
        self.assertFalse(name.startswith("."), name)

    def test_ordinary_content_still_gets_a_readable_slug(self):
        self.assertEqual(self._add("A story about tariffs and trade"),
                         "a-story-about-tariffs-and-trade.txt")

    def test_the_content_is_what_lands_on_disk(self):
        name = self._add("A story about tariffs and trade")
        self.assertIn("A story about tariffs and trade", self._body(name))


class FrontmatterQuotingTest(_Base):

    def _yaml(self, name):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        return yaml.safe_load(self._body(name).split("---", 2)[1])

    def test_a_url_derived_title_with_a_quote_is_valid_yaml(self):
        with mock.patch.object(self.serve, "_clip_fetch", lambda u: (None, "no")):
            name = self._add('https://example.com/the-"big-lie"-explained')
        self.assertIsInstance(self._yaml(name), dict)

    def _patched_with_headline(self, headline):
        """Drive _fetch_and_patch on a file whose title still equals its URL.

        That is the only state in which it adopts the article's headline — `/inbox/add`
        normally sets a title from the URL's last path segment, and the patch keeps it. The
        first version of this test asserted the headline always won, which is a thing I
        assumed rather than measured; it does not, and the test was wrong, not the code.
        """
        url = "https://example.com/story"
        name = "story.md"
        (self.w.raw / name).write_text(
            f'---\ntitle: {url}\nurl: {url}\nwikified: false\nfetch_failed: true\n---\n\n',
            encoding="utf-8")
        with mock.patch.object(self.serve, "_clip_fetch",
                               lambda u: (f"{headline}\n\nBody text.", None)):
            self.serve._fetch_and_patch(self.w.raw / name, url, expect_body="")
            time.sleep(0.4)
        return name

    def test_a_fetched_headline_with_a_quote_is_valid_yaml(self):
        """The headline comes from the article, so quotes in it are routine."""
        name = self._patched_with_headline('The "Big Lie" goes to council')
        self.assertEqual(self._yaml(name)["title"], 'The "Big Lie" goes to council')

    def test_the_title_round_trips_through_the_readers(self):
        name = self._patched_with_headline('The "Big Lie" goes to council')
        fm, _body = self.serve._parse_frontmatter(self._body(name))
        self.assertEqual(fm["title"], 'The "Big Lie" goes to council')

    def test_a_plain_headline_is_unchanged(self):
        name = self._patched_with_headline("Carney calls the tariffs a rupture")
        self.assertEqual(self._yaml(name)["title"],
                         "Carney calls the tariffs a rupture")

    def test_a_url_derived_title_is_kept_over_the_headline(self):
        """Measured, not assumed: the patch adopts the headline only when the title is
        empty or still the URL itself."""
        with mock.patch.object(self.serve, "_clip_fetch",
                               lambda u: ("A Completely Different Headline\n\nBody.", None)):
            name = self._add("https://example.com/the-slug-title")
            time.sleep(0.4)
        self.assertEqual(self._yaml(name)["title"], "the slug title")


class WritePathTest(_Base):

    def test_a_pasted_note_goes_through_atomic_write(self):
        """raw/ sits beside a server running as another user; a plain write_text inherits
        none of the ownership handling every other write here goes through."""
        with mock.patch.object(self.serve, "_atomic_write") as aw:
            self.c.post("/inbox/add", json={"content": "A plain pasted note"})
        self.assertTrue(aw.called)

    def test_empty_content_is_refused(self):
        r = self.c.post("/inbox/add", json={"content": "   "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_a_logged_out_add_gets_json(self):
        c = self.serve.app.test_client()
        r = c.post("/inbox/add", json={"content": "x"})
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json()["login_required"])


if __name__ == "__main__":
    unittest.main()
