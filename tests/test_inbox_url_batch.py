"""A paste of nothing but links is a batch of articles, not one note.

Asked for by someone reading the NYT app on a phone: *"The share sheet is like 3 clicks,
with a big slow step, away. Is there a way to access my NYT saved list or something? I'd
like to improve this workflow."*

There is no API for a NYT saved list, so the cheap phone-side move is **Copy Link** —
instant, native, no Shortcut launch — into a running note, and then one paste here. Until
now that produced a SINGLE junk item: `/inbox/add` decided `is_url` with
`"\\n" not in content`, so twenty links became one pasted note whose filename was the
first 60 characters of the first URL.

**The discriminator is the whole safety argument.** Every non-blank line must be a bare
URL, and there must be at least two. An article that merely CONTAINS a line with a URL on
it must never be split into pieces — destroying a paste is the thing this route has
already been fixed for three separate times, and a batch rule that fires on prose would
be a fourth. One URL is not a batch; it is the existing single-URL path, untouched.

A batch has three outcomes AT ONCE — saved, already here, failed — so it reports per URL.
Collapsing them into one verdict is how twenty links report "ok" while three of them went
nowhere.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harness
import serve


class SplitterTest(unittest.TestCase):
    """What counts as a batch, decided before anything is written."""

    def test_two_urls_on_their_own_lines(self):
        self.assertEqual(
            serve._split_url_batch("https://example.com/a\nhttps://example.com/b"),
            ["https://example.com/a", "https://example.com/b"])

    def test_blank_lines_and_stray_whitespace_are_fine(self):
        """What a phone note actually looks like after a week of Copy Link."""
        self.assertEqual(
            serve._split_url_batch("\n  https://example.com/a  \n\n\nhttps://example.com/b\n"),
            ["https://example.com/a", "https://example.com/b"])

    def test_order_is_preserved_and_exact_repeats_collapse(self):
        got = serve._split_url_batch(
            "https://example.com/b\nhttps://example.com/a\nhttps://example.com/b")
        self.assertEqual(got, ["https://example.com/b", "https://example.com/a"])

    def test_one_url_is_not_a_batch(self):
        """It is the existing single-URL path, which must keep behaving exactly as it did."""
        self.assertIsNone(serve._split_url_batch("https://example.com/a"))

    # -- the half that protects a paste ----------------------------------------

    def test_an_article_containing_a_url_line_is_never_a_batch(self):
        """The failure this must not have. An article with a bare URL in it is prose,
        and splitting it would destroy the paste — which is precisely the damage
        `/inbox/add` has already been repaired for three times."""
        article = ("The agency published its findings on Tuesday.\n\n"
                   "https://example.com/the-report\n\n"
                   "Critics said the numbers were incomplete.")
        self.assertIsNone(serve._split_url_batch(article))

    def test_a_url_with_trailing_prose_on_the_same_line_is_not_a_batch(self):
        self.assertIsNone(serve._split_url_batch(
            "https://example.com/a — worth reading\nhttps://example.com/b"))

    def test_one_non_url_line_disqualifies_the_whole_paste(self):
        """All or nothing, deliberately: a list with a stray heading is ambiguous, and the
        safe reading of an ambiguous paste is 'this is one note'."""
        self.assertIsNone(serve._split_url_batch(
            "Saved for later\nhttps://example.com/a\nhttps://example.com/b"))

    def test_empty_and_whitespace(self):
        for s in ("", "   ", "\n\n"):
            self.assertIsNone(serve._split_url_batch(s))

    def test_a_non_http_scheme_is_not_a_url_here(self):
        self.assertIsNone(serve._split_url_batch(
            "ftp://example.com/a\nhttps://example.com/b"))


class BatchAddTest(unittest.TestCase):
    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        serve.REPO_ROOT = self.w.root
        self.addCleanup(lambda: (setattr(serve, "RAW_DIR", self._saved[0]),
                                 setattr(serve, "WIKI_DIR", self._saved[1]),
                                 setattr(serve, "REPO_ROOT", self._saved[2])))
        serve._src_index_cache = None
        # The fetch is a thread doing network IO; capture the call instead.
        self._patch = mock.patch.object(serve, "_fetch_and_patch")
        self.fetch = self._patch.start()
        self.addCleanup(self._patch.stop)
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def _add(self, content, **kw):
        payload = {"content": content}
        payload.update(kw)
        r = self.c.post("/inbox/add", json=payload)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        return r.get_json()

    def _names(self):
        return sorted(p.name for p in self.w.raw.iterdir() if p.is_file())

    def test_each_link_becomes_its_own_article(self):
        data = self._add("https://example.com/one\nhttps://example.com/two\n"
                         "https://example.com/three")
        self.assertTrue(data["batch"])
        self.assertEqual(data["added"], 3)
        self.assertEqual(len(self._names()), 3)

    def test_the_rows_come_back_so_the_browser_need_not_ask_again(self):
        data = self._add("https://example.com/one\nhttps://example.com/two")
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual({i["name"] for i in data["items"]}, set(self._names()))

    def test_a_link_already_in_the_list_is_reported_not_rewritten(self):
        self._add("https://example.com/one\nhttps://example.com/two")
        before = {p.name: p.read_text(encoding="utf-8") for p in self.w.raw.iterdir()}
        data = self._add("https://example.com/one\nhttps://example.com/three")
        self.assertEqual(data["added"], 1)
        self.assertEqual(len(data["duplicates"]), 1)
        after = {p.name: p.read_text(encoding="utf-8") for p in self.w.raw.iterdir()}
        for name, text in before.items():
            self.assertEqual(after[name], text, f"{name} was rewritten by a re-add")

    def test_one_bad_link_does_not_lose_the_others(self):
        """Twenty links and one failure must save nineteen, not none — and must SAY so."""
        real = serve._capture_article

        def flaky(url, **kw):
            if url.endswith("/bad"):
                raise RuntimeError("boom")
            return real(url, **kw)

        with mock.patch.object(serve, "_capture_article", side_effect=flaky):
            data = self._add("https://example.com/one\nhttps://example.com/bad\n"
                             "https://example.com/two")
        self.assertEqual(data["added"], 2)
        self.assertEqual(len(data["failed"]), 1)
        self.assertIn("bad", data["failed"][0]["url"])

    def test_the_batch_is_capped_and_the_overflow_is_reported(self):
        """A paste must not open hundreds of sockets: _fetch_and_patch spawns a thread
        per capture. The ones over the cap are named rather than silently dropped."""
        urls = "\n".join(f"https://example.com/{i}" for i in range(serve._MAX_URL_BATCH + 5))
        data = self._add(urls)
        self.assertEqual(data["added"], serve._MAX_URL_BATCH)
        self.assertEqual(len(data["failed"]), 5)
        self.assertIn("limit", data["failed"][0]["error"])

    def test_each_link_gets_its_fetch(self):
        self._add("https://example.com/one\nhttps://example.com/two")
        self.assertEqual(self.fetch.call_count, 2)

    # -- the paths that must not change ----------------------------------------

    def test_a_pasted_article_is_still_one_item(self):
        article = ("The agency published its findings on Tuesday.\n\n"
                   "https://example.com/the-report\n\n"
                   "Critics said the numbers were incomplete.")
        data = self._add(article)
        self.assertNotIn("batch", data)
        self.assertEqual(len(self._names()), 1)
        saved = (self.w.raw / data["filename"]).read_text(encoding="utf-8")
        self.assertIn("Critics said", saved, "the paste lost text")
        self.assertIn("https://example.com/the-report", saved)

    def test_a_single_url_is_still_the_single_url_path(self):
        data = self._add("https://example.com/only")
        self.assertNotIn("batch", data)
        self.assertEqual(len(self._names()), 1)

    def test_an_explicit_filename_opts_out_of_batching(self):
        """Naming one file and handing over several URLs is ambiguous, and the
        unambiguous reading is 'this is one item'."""
        data = self._add("https://example.com/a\nhttps://example.com/b",
                         filename="my-list.txt")
        self.assertNotIn("batch", data)
        self.assertEqual(self._names(), ["my-list.txt"])


TEMPLATE = (Path(__file__).resolve().parent.parent / "tools" / "templates" / "inbox.html")


class TemplateContractTest(unittest.TestCase):
    def setUp(self):
        import re
        src = TEMPLATE.read_text(encoding="utf-8")
        self.save = re.search(r"async function saveToInbox\(\)[\s\S]*?\n\}", src).group(0)

    def test_it_inserts_every_row_from_the_reply(self):
        self.assertIn("insertItems(data.items)", self.save)


class BatchSummaryTest(unittest.TestCase):
    """RUN rather than grepped.

    The first version of this asserted that the string "Not saved:" appears in
    saveToInbox. It does — and a mutation that replaced the condition around it with
    `if (false)` left the string untouched, so the test passed over a batch that had
    stopped naming its failures at all. `mutate.py` reported MISSED and was right.
    `batchSummary` is a function so this can call it.
    """

    @classmethod
    def setUpClass(cls):
        import shutil
        if not shutil.which("node"):
            raise unittest.SkipTest("node is not installed")
        import re
        src = TEMPLATE.read_text(encoding="utf-8")
        m = re.search(r"function batchSummary\(data\)[\s\S]*?\n\}", src)
        if not m:
            raise AssertionError("batchSummary is gone; every assertion here is vacuous")
        cls.fn = m.group(0)

    def _summary(self, data):
        import json, subprocess
        js = (self.fn + "\n" +
              "process.stdout.write(batchSummary(" + json.dumps(data) + "));")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        return out.stdout

    def test_a_clean_batch_says_what_landed(self):
        self.assertEqual(
            self._summary({"added": 3, "duplicates": [], "failed": []}), "3 saved.")

    def test_duplicates_are_counted_separately(self):
        got = self._summary({"added": 2, "duplicates": [{"url": "u"}], "failed": []})
        self.assertIn("2 saved", got)
        self.assertIn("1 already here", got)

    def test_a_failure_is_counted_AND_named(self):
        """A count alone cannot be acted on: which link, and why."""
        got = self._summary({"added": 1, "duplicates": [],
                             "failed": [{"url": "https://example.com/bad",
                                         "error": "timed out"}]})
        self.assertIn("1 failed", got)
        self.assertIn("https://example.com/bad", got)
        self.assertIn("timed out", got)

    def test_a_clean_batch_does_not_mention_failures(self):
        """The not-crying-wolf half: a "Not saved" heading over an empty list reads as
        damage that did not happen."""
        self.assertNotIn("Not saved", self._summary(
            {"added": 5, "duplicates": [], "failed": []}))


if __name__ == "__main__":
    unittest.main()
