"""The bottom row of the history view is one of two different things.

Reported on a page one day old, with a single ingest behind it. The view showed:

    Current version — 2026-10-05   ingest  created  from Russia accused of covering up…
    Earliest kept version                                   origin unknown   1696 bytes

and the complaint was exact: *"why doesn't it know the original story's origin? 'Earliest
kept version' sounds fishy, when I know that was the creation."* It was the creation. The
row said nothing records what produced it, which is only true once pruning has thrown older
revisions away — and this page had one revision, on a store that keeps fifty.

Two things had to be separated:

  * **Pruned** — the oldest surviving revision, with whatever made it genuinely gone.
    `origin unknown` is honest here.
  * **Original** — the content the page was created with, because nothing was ever pruned.
    It has a date (`created:`, read from THAT revision's own text so a later edit cannot
    date the row) and usually an origin.

The origin looks like a guess and is not. A brand-new page leaves no revision for its own
creation — `_snapshot_version` copies the content being replaced and there is none — so the
first revision on disk is the snapshot taken by the autolink pass that runs inside the same
`create_file` call, stamped with it. When that revision's tool is `create_file`, the write
that destroyed the original content was part of the call that wrote it, so its source and
reason describe the creation too.

`pruned` is deliberately the conservative half: `_HISTORY_KEEP` could have been raised after
a page was pruned at a lower cap, and nothing on disk can tell. The claim is only made where
the count is unambiguous.

**A second hole fell out of looking at this.** A page whose autolink pass finds nothing to
link has NO revisions at all — one version, ever — and that row carried no date and no
origin whatsoever. It can at least give the page's own `created:` date.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

SOURCE = "sources/russia-accused-of-covering-up-plague-leak.md"
SLUG = "russia-accused-of-covering-up-plague-leak"


class OriginalVersionTest(TempWikiTestCase):
    """The reported page: created by one ingest, one revision on disk."""

    def setUp(self):
        super().setUp()
        # A page for the autolinker to link to, so create_file's own autolink pass
        # actually rewrites the page and leaves the one revision.
        self.w.page("entities/irkutsk.md", title="Irkutsk", type="entity",
                    body="## Overview\n\nA city in Siberia.\n")
        agent.init_session()
        agent.set_write_reason("ingest")
        agent._ctx()._current_source_page = SOURCE
        with agent.write_tool("create_file"):
            r = agent.TOOL_FNS["create_file"]({
                "path": "wiki/entities/irkutsk-plague-outbreak.md",
                "title": "Irkutsk Plague Outbreak", "type": "entity",
                "body": "## Overview\n\nAn outbreak in Irkutsk, a city in Siberia.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        self.p = self.w.wiki / "entities" / "irkutsk-plague-outbreak.md"
        self.rows = agent.page_history(self.p)

    def test_the_page_has_exactly_two_versions(self):
        """One revision — what create_file wrote — plus the autolinked page."""
        self.assertEqual(len(self.rows), 2)

    def test_the_bottom_row_is_not_called_earliest_kept(self):
        self.assertFalse(self.rows[-1]["earliest"])

    def test_the_bottom_row_is_the_original(self):
        self.assertTrue(self.rows[-1]["original"])

    def test_the_original_carries_the_pages_created_date(self):
        fm = self.p.read_text(encoding="utf-8")
        created = [l.split(":", 1)[1].strip() for l in fm.splitlines()
                   if l.startswith("created:")][0]
        self.assertEqual(self.rows[-1]["when"], created)

    def test_the_original_names_the_source_it_came_from(self):
        """The complaint in one assertion."""
        self.assertEqual(self.rows[-1]["source"], SLUG)

    def test_the_original_names_the_reason_and_the_tool(self):
        self.assertEqual(self.rows[-1]["why"], "ingest")
        self.assertEqual(self.rows[-1]["tool"], agent._TOOL_LABELS["create_file"])

    def test_the_original_still_carries_no_counts(self):
        """There is nothing below it to diff against. Showing +0/-0 as though the page
        were born empty would be a worse answer than showing none."""
        self.assertEqual((self.rows[-1]["added"], self.rows[-1]["removed"]), (0, 0))
        self.assertEqual(self.rows[-1]["sections"], [])

    def test_the_current_row_is_unaffected(self):
        self.assertTrue(self.rows[0]["current"])
        self.assertEqual(self.rows[0]["source"], SLUG)
        self.assertNotEqual(self.rows[0]["when"], "")


class NotCreatedByCreateFileTest(TempWikiTestCase):
    """When the oldest revision was NOT written by create_file's own pass, the content
    below it came from somewhere unrecorded — so the row gets its date and nothing else."""

    def setUp(self):
        super().setUp()
        self.p = self.w.page("entities/mark-carney.md", title="Mark Carney",
                             type="entity", body="## Overview\n\nA banker.\n")
        self.fm = self.p.read_text(encoding="utf-8").split("---\n")[1]
        agent.init_session()
        agent._ctx()._current_source_page = SOURCE
        with agent.write_reason("ingest"), agent.write_tool("update_section"):
            agent._atomic_write(self.p,
                                f"---\n{self.fm}---\n\n## Overview\n\nA politician.\n")
        self.rows = agent.page_history(self.p)

    def test_it_is_still_the_original(self):
        self.assertTrue(self.rows[-1]["original"])

    def test_it_has_a_date(self):
        self.assertNotEqual(self.rows[-1]["when"], "")

    def test_it_claims_no_origin(self):
        """A section edit says nothing about what wrote the page before it."""
        self.assertEqual(self.rows[-1]["source"], "")
        self.assertEqual(self.rows[-1]["why"], "")
        self.assertEqual(self.rows[-1]["tool"], "")


class PrunedTest(TempWikiTestCase):
    """Past the cap, the oldest surviving revision really is of unknown origin."""

    def setUp(self):
        super().setUp()
        self.p = self.w.page("entities/o.md", title="O", type="entity",
                             body="## Overview\n\nv0.\n")
        # One snapshot per page per SESSION, so each write needs its own.
        for i in range(agent._HISTORY_KEEP + 3):
            agent.init_session()
            agent._atomic_write(self.p, self.p.read_text(encoding="utf-8") + f"\nv{i}\n")
        self.rows = agent.page_history(self.p)

    def test_the_store_pruned_to_the_cap(self):
        self.assertEqual(len(self.rows), agent._HISTORY_KEEP + 1)

    def test_the_bottom_row_is_earliest_kept_not_original(self):
        self.assertTrue(self.rows[-1]["earliest"])
        self.assertFalse(self.rows[-1]["original"])

    def test_it_claims_no_date(self):
        """The page's created: date is still in that revision's frontmatter, and using it
        would date a row whose content is NOT the original."""
        self.assertEqual(self.rows[-1]["when"], "")


class SingleVersionTest(TempWikiTestCase):
    """A created page whose autolink found nothing to link has no revisions at all."""

    def setUp(self):
        super().setUp()
        agent.init_session()
        with agent.write_reason("ingest"), agent.write_tool("create_file"):
            r = agent.TOOL_FNS["create_file"]({
                "path": "wiki/entities/o.md", "title": "O", "type": "entity",
                "body": "## Overview\n\nNothing here links anywhere.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        self.p = self.w.wiki / "entities" / "o.md"
        self.rows = agent.page_history(self.p)

    def test_there_is_one_row(self):
        self.assertEqual(len(self.rows), 1)

    def test_it_is_both_current_and_original(self):
        self.assertTrue(self.rows[0]["current"])
        self.assertTrue(self.rows[0]["original"])

    def test_it_no_longer_shows_a_blank_date(self):
        """It used to carry nothing at all: no date, no origin, on the only row there is."""
        self.assertNotEqual(self.rows[0]["when"], "")


class MissingCreatedFieldTest(TempWikiTestCase):
    """No created: to read — claim nothing rather than invent a date."""

    def setUp(self):
        super().setUp()
        self.p = self.w.wiki / "entities" / "bare.md"
        self.p.parent.mkdir(parents=True, exist_ok=True)
        self.p.write_text("---\ntitle: Bare\ntype: entity\n---\n\n## Overview\n\nv0.\n",
                          encoding="utf-8")
        agent.init_session()
        agent._atomic_write(self.p, self.p.read_text(encoding="utf-8") + "\nmore\n")
        self.rows = agent.page_history(self.p)

    def test_the_bottom_row_falls_back_to_earliest_kept(self):
        self.assertFalse(self.rows[-1]["original"])
        self.assertTrue(self.rows[-1]["earliest"])

    def test_a_date_backfilled_later_does_not_date_the_original(self):
        """Why the date is read from THAT revision's own text and not from the live page.

        `created:` is system-managed and normally identical in every version — but
        `update_file` and `heal_pages` both BACKFILL a missing one from the file's mtime.
        So a page can have an oldest revision with no `created:` and a current page with
        one that was invented afterwards. Reading the live page would stamp the original
        row with a date that is not its own, which is the guessed timestamp this function
        refuses to produce.
        """
        p = self.w.wiki / "entities" / "backfilled.md"
        p.write_text("---\ntitle: B\ntype: entity\n---\n\n## Overview\n\nv0.\n",
                     encoding="utf-8")
        agent.init_session()
        agent._atomic_write(
            p, "---\ntitle: B\ntype: entity\ncreated: 2026-09-30\n---\n\n"
               "## Overview\n\nv1.\n")
        bottom = agent.page_history(p)[-1]
        self.assertEqual(bottom["when"], "")
        self.assertFalse(bottom["original"])
        self.assertTrue(bottom["earliest"])


class TemplateTest(unittest.TestCase):
    """The view has to tell the two apart, and say what each means."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parent.parent / "tools" / "templates"
                   / "wiki-history.html").read_text(encoding="utf-8")

    def test_the_original_row_is_labelled(self):
        self.assertIn("Original version", self.src)

    def test_the_original_is_checked_before_earliest(self):
        """Both flags can never be true at once, but the order is what keeps a future
        change from quietly showing the vaguer label on a row that knows better."""
        self.assertLess(self.src.index("r.original"), self.src.index("r.earliest"))

    def test_origin_unknown_survives_for_the_pruned_case(self):
        self.assertIn("origin unknown", self.src)

    def test_the_original_row_says_why_it_has_no_counts(self):
        self.assertIn("page created", self.src)

    def test_the_note_no_longer_claims_every_bottom_row_is_pruned(self):
        self.assertNotIn("whatever produced it has been\n      pruned", self.src)
        self.assertIn("unless older versions have been", self.src)


if __name__ == "__main__":
    unittest.main()
