"""A saved article appears at once, and says so while it is saving.

Reported as *"what can we do to make the reading list update immediately? When I add an
article, it doesn't show up in the list for a while. Sometimes I can't tell if the server
is frozen or if the add didn't work or what."*

Two complaints, two different defects:

- **The row needed a SECOND round trip.** `/inbox/add` answered `{"ok": true}` and the
  browser then asked `/inbox/list` to find out what it had just saved — a request that
  re-reads every file in `raw/` to describe the one file the save already had in its hand.
  Measured at 222 captures / 7.2 MB the listing is only ~25ms on a warm cache, so this is
  not about that number: it is that the row waits on a second request at all, behind
  whatever else the single-worker server is doing. `/inbox/add` now returns the row.
- **There was no busy state at all.** A save behind a running ingest looked exactly like
  a save that had done nothing — the same complaint, in the same words, as the search
  spinner, on the one control that never got one.

`_new_inbox_item` and `list_inbox` share `_inbox_item`, so the row the browser inserts
cannot differ from the row the next poll would draw. That is what the first class checks,
and it is the whole reason the builder was hoisted rather than copied.
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harness
import serve

TEMPLATE = Path(__file__).resolve().parent.parent / "tools" / "templates" / "inbox.html"


class AddReturnsTheRowTest(unittest.TestCase):
    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        self.addCleanup(lambda: setattr(serve, "RAW_DIR", self._saved[0]))
        self.addCleanup(lambda: setattr(serve, "WIKI_DIR", self._saved[1]))
        serve._src_index_cache = None
        self.c = serve.app.test_client()
        with self.c.session_transaction() as s:
            s["logged_in"] = True

    def _add(self, content, **kw):
        payload = {"content": content}
        payload.update(kw)
        r = self.c.post("/inbox/add", json=payload)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        return r.get_json()

    def test_pasted_text_comes_back_with_its_row(self):
        data = self._add("The Senate voted on Tuesday.\n\nIt was close.")
        self.assertTrue(data["ok"])
        self.assertIsNotNone(data.get("item"), "the save did not return a row to draw")
        self.assertEqual(data["item"]["name"], data["filename"])

    def test_the_row_matches_what_the_listing_would_draw(self):
        """The whole argument for hoisting `_inbox_item` instead of writing a second
        builder: two builders drift, and the drift shows as a row that changes under the
        reader when the first poll arrives."""
        data = self._add("Berger Montague filed suit on Monday against the agency.")
        listed = [i for i in serve.list_inbox() if i["name"] == data["filename"]]
        self.assertEqual(len(listed), 1)
        self.assertEqual(data["item"], listed[0])

    def test_a_url_capture_comes_back_with_its_row(self):
        import unittest.mock as mock
        with mock.patch.object(serve, "_fetch_and_patch"):
            data = self._add("https://example.com/a-story")
        self.assertIsNotNone(data.get("item"))
        self.assertEqual(data["item"]["name"], data["filename"])

    def test_a_brand_new_row_is_never_wikified_or_in_flight(self):
        """`_new_inbox_item` consults neither the queue nor the source-page map, and says
        so by passing an empty dict and a raising callable. If a fresh capture could ever
        be either, that assertion is the thing that has to change — loudly."""
        data = self._add("A fresh note.")
        self.assertFalse(data["item"]["wikified"])
        self.assertEqual(data["item"]["ingest"], "")
        self.assertEqual(data["item"]["wiki_path"], "")

    def test_a_duplicate_returns_no_row(self):
        """Nothing was written, so there is nothing to insert — and inserting a row for
        an article already on screen would duplicate it. The reply says `duplicate` and
        the client falls back to a poll."""
        self._add("https://example.com/same", filename="same.md")
        import unittest.mock as mock
        with mock.patch.object(serve, "_fetch_and_patch"):
            again = self._add("https://example.com/same")
        self.assertTrue(again.get("duplicate"))
        self.assertIsNone(again.get("item"))

    def test_a_row_that_cannot_be_built_does_not_fail_the_save(self):
        """The article is on disk and the poll will draw it. Losing the save to a
        rendering error would be trading the thing that matters for the thing that does
        not."""
        import unittest.mock as mock
        with mock.patch.object(serve, "_inbox_item", side_effect=RuntimeError("boom")):
            data = self._add("Still has to be saved.")
        self.assertTrue(data["ok"])
        self.assertIsNone(data["item"])
        self.assertTrue((self.w.raw / data["filename"]).exists())


class TemplateContractTest(unittest.TestCase):
    """The browser half, asserted structurally. The behaviour it pins is the report."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")
        cls.save = re.search(r"async function saveToInbox\(\)[\s\S]*?\n\}", cls.src).group(0)

    def test_saving_draws_the_row_from_its_own_reply(self):
        self.assertIn("insertItems([data.item])", self.save)

    def test_saving_does_not_ask_the_listing_for_what_it_just_saved(self):
        """The round trip that was the reported delay. `pollSoon()` remains as the
        fallback for the cases with no row to prepend against, which is why this asserts
        on the unconditional call rather than on the word."""
        self.assertNotIn("pollInbox()", self.save)

    def test_there_is_a_busy_state_while_it_saves(self):
        self.assertIn("Saving", self.save)
        self.assertIn("btn.disabled = true", self.save)

    def test_the_busy_state_is_cleared_however_it_ends(self):
        """In a `finally`: a throw that left the button reading "Saving…" for ever is
        the frozen-server appearance this is supposed to remove, not create."""
        self.assertRegex(self.save, r"finally \{[^}]*btn\.disabled = false")

    def test_a_failed_save_says_so(self):
        self.assertIn("Save failed: ", self.save)

    def test_one_inserter_serves_both_callers(self):
        """A third row renderer is how the poll's path and the save's path come to
        disagree — the two-renderers bug this file already paid for."""
        self.assertEqual(len(re.findall(r"function itemRowHtml\(", self.src)), 1)
        # Row inserts specifically. A blunt count of `insertBefore` also catches
        # markRowWikified putting a View-page button into an existing row, which is a
        # different thing and would make this fail for a reason it is not about.
        self.assertEqual(len(re.findall(r"itemRowHtml\(", self.src)), 2,
                         "itemRowHtml should be defined once and called from one place")
        self.assertEqual(len(re.findall(r"function insertItems\(", self.src)), 1)


class PollWakesUpTest(unittest.TestCase):
    """An article added from the phone reaches a tab that is already open only when that
    tab asks. Returning from the iOS share sheet, or a back-navigation, restores the page
    from the bfcache — the page is resumed rather than loaded, and `pageshow` is the
    event that says so. With only `visibilitychange` the list you come back to is as
    stale as the moment you left, up to the 30s idle interval."""

    @classmethod
    def setUpClass(cls):
        cls.src = TEMPLATE.read_text(encoding="utf-8")

    def test_a_bfcache_restore_polls_at_once(self):
        # Anchored on the registration, not on the word: the comment directly above it
        # says "`pageshow` (persisted)", and a looser pattern would match the comment
        # explaining the bug — the trap this project has recorded three times.
        self.assertRegex(self.src,
                         r"addEventListener\('pageshow'[\s\S]{0,60}persisted[\s\S]{0,40}schedule\(0\)")

    def test_regaining_focus_polls_at_once(self):
        self.assertIn("addEventListener('focus', () => schedule(0))", self.src)

    def test_becoming_visible_still_polls_at_once(self):
        """The one that was already there — the new triggers add to it rather than
        replacing it."""
        self.assertRegex(self.src, r"visibilitychange[\s\S]{0,120}schedule\(0\)")

    def test_the_idle_interval_is_not_quietly_shortened(self):
        """Polling faster is the wrong fix and an expensive one: this project has already
        filled a disk with its own poll volume. The triggers are events, not a shorter
        timer."""
        self.assertIn("IDLE_MS = 30000", self.src)


if __name__ == "__main__":
    unittest.main()
