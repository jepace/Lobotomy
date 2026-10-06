"""Adding something already in the reading list.

Asked plainly: *"does the reading list notice if I try to add a duplicate item?"* It did
not — and the reason that never showed up as duplicate ROWS is worse than duplicate rows
would have been. The destination filename is derived deterministically (from the URL, or
from the first 60 characters of pasted text), so a second add resolved to the same path
and `_atomic_write` silently overwrote what was there. All three of these were measured,
and all three returned `{"ok": true}`:

  * **Re-adding a URL already in the list reset `wikified: true` to `wikified: false`.**
    An article already folded into the wiki reappeared as unread, and wikifying it again
    is another ~40-minute ingest at `max_rpm: 1` of a source already ingested.
  * **Re-adding a URL whose text you had pasted in by hand replaced that text** with an
    empty `fetch_failed: true` placeholder. The paste was gone, silently.
  * **Two genuinely DIFFERENT articles pasted from one site collapsed into one file.**
    The slug comes from the first 60 characters and a news site's chrome — "Skip to
    content Skip to site index Sections Search Subscribe for $1 a week Log in Today's
    Paper World U.S. Politics…" — runs to 151 before the headline. The first article was
    destroyed with nothing reported.

The rule that falls out: **`/inbox/add` must never destroy an existing item.** Editing is
a separate route, so the add path has no business overwriting anything. A story already
here is either a duplicate — reported, not rewritten — or a different story whose slug
collided, which gets its own filename.

One deliberate exception, and it destroys nothing: a capture that fetched NOTHING and has
no body is what re-adding the URL is *for*, so that retries the fetch.

`tools/add_story.py` already refused to overwrite (`if dest.exists(): return 1`). The web
route — the one anybody actually uses — had no check at all.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import serve

URL = "https://www.nytimes.com/2026/10/05/world/europe/irkutsk-plague.html"
# A real news site's chrome, which is what the slug is built out of when you paste.
BOILER = ("Skip to content Skip to site index Sections Search Subscribe for $1 a week "
          "Log in Today's Paper World U.S. Politics N.Y. Business Opinion Tech Science ")
ARTICLE_A = BOILER + "Florida ends its Flock Safety contract. " + "x" * 300
ARTICLE_B = BOILER + "Irkutsk plague outbreak spreads. " + "y" * 300


class InboxAddTestCase(unittest.TestCase):

    def setUp(self):
        self.w = TempWiki()
        self.w.__enter__()
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        # The background fetch would reach the network and race the assertions; the
        # capture path's own behaviour is what is under test here.
        self._patch = mock.patch.object(serve, "_fetch_and_patch")
        self.fetch = self._patch.start()
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def tearDown(self):
        self._patch.stop()
        serve.RAW_DIR, serve.WIKI_DIR = self._saved
        self.w.__exit__(None, None, None)

    def add(self, content, filename=""):
        r = self.c.post("/inbox/add", json={"content": content, "filename": filename})
        self.assertEqual(r.status_code, 200, r.data)
        return r.get_json()

    def raw_names(self):
        return sorted(p.name for p in self.w.raw.iterdir() if p.is_file())

    def disk(self, name):
        return (self.w.raw / name).read_text(encoding="utf-8")


class DuplicateUrlTest(InboxAddTestCase):

    def test_the_second_add_says_it_is_a_duplicate(self):
        self.add(URL)
        self.assertTrue(self.add(URL).get("duplicate"))

    def test_the_first_add_does_not(self):
        self.assertFalse(self.add(URL).get("duplicate"))

    def test_it_names_the_item_already_there(self):
        first = self.add(URL)
        self.assertEqual(self.add(URL)["filename"], first["filename"])

    def test_no_second_file_appears(self):
        self.add(URL)
        self.add(URL)
        self.assertEqual(len(self.raw_names()), 1)

    def test_a_tracking_parameter_does_not_make_it_a_different_story(self):
        """A story shared from Twitter arrives with ?utm_source= on the end.

        `_normalize_capture_url` does NOT handle this — it unwraps Firefox's
        about:reader wrapper and nothing else — which is why `_dupe_url_key` exists.
        This test and the next one failed when the code assumed otherwise.
        """
        self.add(URL)
        self.assertTrue(self.add(URL + "?utm_source=twitter").get("duplicate"))

    def test_a_trailing_slash_does_not_either(self):
        self.add(URL)
        self.assertTrue(self.add(URL.rstrip("/") + "/").get("duplicate"))

    def test_the_stored_url_is_what_was_captured_not_the_comparison_key(self):
        """Compare normalized, store verbatim — the same rule as _norm_prose. The
        Original-article link has to go to the URL the user actually saved."""
        name = self.add(URL)["filename"]
        self.assertIn(f"url: {URL}", self.disk(name))

    def test_a_genuinely_different_url_is_added(self):
        self.add(URL)
        other = "https://www.nytimes.com/2026/10/05/us/politics/florida-flock.html"
        self.assertFalse(self.add(other).get("duplicate"))
        self.assertEqual(len(self.raw_names()), 2)


class DupeUrlKeyTest(unittest.TestCase):
    """The comparison key alone. Over-normalizing would merge two different articles,
    which loses the second as surely as the overwrite did."""

    def k(self, u):
        return serve._dupe_url_key(u)

    def test_the_variants_that_are_one_article(self):
        base = self.k(URL)
        for variant in (URL + "?utm_source=twitter", URL + "?fbclid=abc", URL + "/",
                        URL.replace("www.", ""), URL.replace("https", "http"),
                        URL + "#lede", URL.replace("nytimes", "NYTimes")):
            with self.subTest(variant=variant):
                self.assertEqual(self.k(variant), base)

    def test_a_different_path_is_a_different_article(self):
        self.assertNotEqual(self.k(URL), self.k(URL.replace("irkutsk", "florida")))

    def test_a_meaningful_query_parameter_is_kept(self):
        """Plenty of sites put the article's identity in the query string. Stripping it
        would merge every article on the site into one."""
        self.assertNotEqual(self.k("https://example.com/read?id=123"),
                            self.k("https://example.com/read?id=456"))

    def test_parameter_order_does_not_matter(self):
        self.assertEqual(self.k("https://example.com/a?x=1&y=2"),
                         self.k("https://example.com/a?y=2&x=1"))

    def test_a_reader_mode_wrapper_still_unwraps(self):
        """_normalize_capture_url's own job, which this must not have broken."""
        import urllib.parse
        wrapped = "about:reader?url=" + urllib.parse.quote(URL, safe="")
        self.assertEqual(self.k(wrapped), self.k(URL))

    def test_an_empty_url_is_safe(self):
        self.assertEqual(self.k(""), "")

    def test_a_malformed_url_does_not_raise(self):
        self.k("http://[not a url")

class NothingIsDestroyedTest(InboxAddTestCase):
    """The reported failures, each as its own assertion."""

    def _captured_then_pasted(self):
        self.add(URL)
        name = self.raw_names()[0]
        (self.w.raw / name).write_text(
            self.disk(name) + "\nThe full article text I pasted by hand.\n",
            encoding="utf-8")
        return name

    def test_pasted_text_survives_a_re_add(self):
        name = self._captured_then_pasted()
        self.add(URL)
        self.assertIn("pasted by hand", self.disk(name))

    def test_a_wikified_capture_is_not_reset_to_unread(self):
        """It used to come back as wikified: false, which costs a whole second ingest."""
        name = self._captured_then_pasted()
        serve._mark_inbox_wikified(name)
        self.add(URL)
        self.assertIn("wikified: true", self.disk(name))

    def test_the_duplicate_reply_says_it_was_already_wikified(self):
        """The difference between "go read it" and "it is already in the wiki"."""
        name = self._captured_then_pasted()
        serve._mark_inbox_wikified(name)
        self.assertTrue(self.add(URL)["wikified"])

    def test_the_fetch_is_not_restarted_for_a_capture_that_has_text(self):
        self._captured_then_pasted()
        self.fetch.reset_mock()
        self.add(URL)
        self.fetch.assert_not_called()


class EmptyCaptureRetryTest(InboxAddTestCase):
    """The one case where re-adding does something, because it destroys nothing."""

    def test_re_adding_an_empty_capture_retries_the_fetch(self):
        self.add(URL)
        self.fetch.reset_mock()
        r = self.add(URL)
        self.assertTrue(r.get("refetched"))
        self.fetch.assert_called_once()

    def test_it_is_still_reported_as_a_duplicate(self):
        self.add(URL)
        self.assertTrue(self.add(URL).get("duplicate"))

    def test_it_does_not_create_a_second_file(self):
        self.add(URL)
        self.add(URL)
        self.assertEqual(len(self.raw_names()), 1)


class SlugCollisionTest(InboxAddTestCase):
    """Two different articles that slugify the same. The destructive one."""

    def test_both_articles_get_their_own_file(self):
        a = self.add(ARTICLE_A)
        b = self.add(ARTICLE_B)
        self.assertNotEqual(a["filename"], b["filename"])
        self.assertEqual(len(self.raw_names()), 2)

    def test_neither_article_is_destroyed(self):
        a = self.add(ARTICLE_A)
        b = self.add(ARTICLE_B)
        self.assertIn("Flock Safety", self.disk(a["filename"]))
        self.assertIn("Irkutsk", self.disk(b["filename"]))

    def test_the_second_is_not_reported_as_a_duplicate(self):
        """It is a different article — calling it a duplicate would lose it just as
        surely as overwriting did, by declining to save it at all."""
        self.add(ARTICLE_A)
        self.assertFalse(self.add(ARTICLE_B).get("duplicate"))

    def test_the_unique_name_is_a_readable_variant(self):
        a = self.add(ARTICLE_A)
        b = self.add(ARTICLE_B)
        self.assertEqual(b["filename"], a["filename"].replace(".txt", "-2.txt"))

    def test_a_third_collision_also_lands(self):
        self.add(ARTICLE_A)
        self.add(ARTICLE_B)
        c = self.add(BOILER + "A third story entirely. " + "z" * 300)
        self.assertEqual(len(self.raw_names()), 3)
        self.assertIn("third story", self.disk(c["filename"]))

    def test_an_explicit_filename_is_not_overwritten_either(self):
        self.add(ARTICLE_A, filename="story.txt")
        self.add(ARTICLE_B, filename="story.txt")
        self.assertEqual(len(self.raw_names()), 2)
        self.assertIn("Flock Safety", self.disk("story.txt"))


class UrlSlugCollisionTest(InboxAddTestCase):
    """Two DIFFERENT articles whose URLs produce the same filename.

    The capture slug is built from the URL's last path segment only, so two sites that
    both end a story `/plague.html` collided — and before the fix the second silently
    replaced the first. Reaching this needs distinct URLs, which is why the duplicate
    tests above do not cover it: `mutate.py` reported the guard MISSED.
    """

    A = "https://www.nytimes.com/2026/10/05/world/europe/plague.html"
    B = "https://www.theguardian.com/2026/10/06/science/plague.html"

    def test_both_captures_get_their_own_file(self):
        a = self.add(self.A)
        b = self.add(self.B)
        self.assertNotEqual(a["filename"], b["filename"])
        self.assertEqual(len(self.raw_names()), 2)

    def test_the_second_is_not_mistaken_for_a_duplicate(self):
        self.add(self.A)
        self.assertFalse(self.add(self.B).get("duplicate"))

    def test_each_file_keeps_its_own_url(self):
        a = self.add(self.A)
        b = self.add(self.B)
        self.assertIn(self.A, self.disk(a["filename"]))
        self.assertIn(self.B, self.disk(b["filename"]))


class IdenticalTextTest(InboxAddTestCase):

    def test_the_same_article_pasted_twice_is_a_duplicate(self):
        self.add(ARTICLE_A)
        self.assertTrue(self.add(ARTICLE_A).get("duplicate"))

    def test_it_does_not_create_a_second_file(self):
        self.add(ARTICLE_A)
        self.add(ARTICLE_A)
        self.assertEqual(len(self.raw_names()), 1)

    def test_whitespace_differences_do_not_make_it_a_new_story(self):
        """A second copy-paste can pick up different line wrapping."""
        self.add(ARTICLE_A)
        self.assertTrue(self.add(ARTICLE_A.replace(" ", "  ")).get("duplicate"))

    def test_the_stored_text_is_unchanged(self):
        name = self.add(ARTICLE_A)["filename"]
        before = self.disk(name)
        self.add(ARTICLE_A)
        self.assertEqual(self.disk(name), before)


class HelperTest(InboxAddTestCase):

    def test_unique_name_returns_the_name_when_free(self):
        self.assertEqual(serve._unique_raw_name("a.txt"), "a.txt")

    def test_unique_name_steps_past_what_exists(self):
        (self.w.raw / "a.txt").write_text("x", encoding="utf-8")
        (self.w.raw / "a-2.txt").write_text("x", encoding="utf-8")
        self.assertEqual(serve._unique_raw_name("a.txt"), "a-3.txt")

    def test_unique_name_handles_a_name_with_no_extension(self):
        (self.w.raw / "a").write_text("x", encoding="utf-8")
        self.assertEqual(serve._unique_raw_name("a"), "a-2")

    def test_raw_items_skips_dotfiles(self):
        (self.w.raw / ".hidden").write_text("x", encoding="utf-8")
        (self.w.raw / "real.txt").write_text("x", encoding="utf-8")
        self.assertEqual([f.name for f, _fm, _b in serve._raw_items()], ["real.txt"])

    def test_find_existing_returns_none_on_an_empty_list(self):
        self.assertIsNone(serve._find_existing_capture(url=URL))

    def test_find_existing_is_not_confused_by_a_file_with_no_url(self):
        (self.w.raw / "note.txt").write_text("just a note", encoding="utf-8")
        self.assertIsNone(serve._find_existing_capture(url=URL))


class TemplateTest(unittest.TestCase):
    """Saying "saved" for an add that changed nothing on disk would be a lie."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parent.parent / "tools" / "templates"
                   / "inbox.html").read_text(encoding="utf-8")

    def test_the_save_handler_reads_the_duplicate_flag(self):
        self.assertIn("data.duplicate", self.src)

    def test_it_says_nothing_was_changed(self):
        self.assertIn("Nothing was changed", self.src)

    def test_it_distinguishes_an_already_wikified_item(self):
        self.assertIn("already been wikified", self.src)

    def test_it_reports_a_retried_fetch(self):
        self.assertIn("data.refetched", self.src)


if __name__ == "__main__":
    unittest.main()
