"""Page history records what caused each change, not just that one happened.

A revision file holds the content as it was BEFORE a write, stamped with the time of that
write. So "what changed this page at 14:03" was always answerable in principle and never
recorded: an ingest, a hand edit, a relink sweep and a revert produced identical-looking
entries, and the only way to guess was to read the diff.

The reason is stamped into the filename after the timestamp — `<ts>__ingest.md` — because
the timestamp is fixed-width, so lexical order stays chronological. Both the history view
and the pruning depend on that order, and an earlier scheme that broke it put new
revisions in the middle of the list.

The other half is scoping. The reason lives on the thread doing the writing, so one call
covers everything a request or a job does; a reason that leaked past its pass would
mislabel every write after it, which is worse than no label at all.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class WriteReasonTest(TempWikiTestCase):

    def _revs(self, rel="entities/a.md"):
        return sorted(x.name for x in (agent.HISTORY_DIR / rel).glob("*.md"))

    def _page(self):
        self.w.page("entities/a.md", title="A Corp", type="entity",
                    body="# A Corp\n\n## Overview\n\nOne.\n")
        return self.w.wiki / "entities" / "a.md"

    def test_the_reason_is_stamped_into_the_revision_filename(self):
        p = self._page()
        with agent.write_reason("user edit"):
            agent._atomic_write(p, "# A Corp\n\n## Overview\n\nTwo.\n")
        self.assertTrue(any("__user-edit.md" in n for n in self._revs()), self._revs())

    def test_no_reason_still_produces_a_plain_revision(self):
        # Old revisions have no suffix, and a write from an unlabelled path must not fail.
        p = self._page()
        agent.set_write_reason("")
        agent._atomic_write(p, "# A Corp\n\n## Overview\n\nTwo.\n")
        self.assertTrue(any(n.count("__") == 0 for n in self._revs()), self._revs())

    def test_lexical_order_stays_chronological(self):
        # The history view and the pruning both sort by name.
        p = self._page()
        for i, why in enumerate(("ingest", "relink", "", "user edit", "revert")):
            agent.begin_write_scope()
            with agent.write_reason(why):
                agent._atomic_write(p, f"# A Corp\n\n## Overview\n\nRevision {i}.\n")
        revs = self._revs()
        self.assertEqual(len(revs), 5)
        self.assertEqual(revs, sorted(revs))
        stamps = [n.split("__")[0].replace(".md", "") for n in revs]
        self.assertEqual(stamps, sorted(stamps), "the timestamp prefix stopped sorting")

    def test_the_reason_is_sanitized(self):
        # It becomes part of a filename, so it cannot carry a slash, a dot or a space.
        p = self._page()
        with agent.write_reason("../../etc/passwd   AND SPACES"):
            agent._atomic_write(p, "# A Corp\n\n## Overview\n\nTwo.\n")
        name = [n for n in self._revs() if "__" in n][0]
        for bad in ("/", "\\", " ", ".."):
            self.assertNotIn(bad, name.split("__", 1)[1], f"{bad!r} reached the filename")

    def test_a_pass_restores_the_previous_reason(self):
        # The bug this guards: heal_pages runs at startup and after every ingest. A reason
        # that leaked from it would label the rest of the job's writes "heal".
        self._page()
        agent.set_write_reason("ingest")
        agent.heal_pages()
        self.assertEqual(agent.get_write_reason(), "ingest")
        agent.relink_all()
        self.assertEqual(agent.get_write_reason(), "ingest")

    def test_each_pass_labels_its_own_writes(self):
        self.w.page("entities/b.md", title="B Corp", type="entity",
                    body="# B Corp\n\n## Overview\n\nA firm that does things here.\n")
        self.w.page("entities/c.md", title="C Corp", type="entity",
                    body="# C Corp\n\nA firm with a lead paragraph and no opener heading.\n"
                         "\n## Background\n\nB.\n")
        agent.promote_lead_to_opener()
        self.assertTrue(any("__promote-opener.md" in n for n in self._revs("entities/c.md")),
                        self._revs("entities/c.md"))

    def test_init_session_labels_an_ingest_and_clears_otherwise(self):
        agent.init_session(inbox_path="raw/x.md")
        self.assertEqual(agent.get_write_reason(), "ingest")
        agent.init_session()
        self.assertEqual(agent.get_write_reason(), "",
                         "a reason survived into the next job and would mislabel it")

    def test_nesting_restores_the_outer_reason(self):
        agent.set_write_reason("ingest")
        with agent.write_reason("relink"):
            self.assertEqual(agent.get_write_reason(), "relink")
        self.assertEqual(agent.get_write_reason(), "ingest")

    def test_the_reason_survives_an_exception_inside_the_scope(self):
        agent.set_write_reason("ingest")
        with self.assertRaises(ValueError):
            with agent.write_reason("merge"):
                raise ValueError("boom")
        self.assertEqual(agent.get_write_reason(), "ingest")


class PageHistoryTest(TempWikiTestCase):
    """What the history view renders, computed where a test can reach it."""

    def _three_changes(self):
        """One page, two writes. Full file contents both times — a real write always
        carries the frontmatter, and an earlier version of this test passed bare bodies,
        so the diff counted seven frontmatter lines being deleted and the numbers meant
        nothing."""
        p = self.w.page("entities/a.md", title="A Corp", type="entity",
                        body="# A Corp\n\n## Overview\n\nOne.\n")
        fm = p.read_text(encoding="utf-8").split("---\n")[1]
        head = f"---\n{fm}---\n\n# A Corp\n\n## Overview\n\n"
        agent.begin_write_scope()
        with agent.write_reason("ingest"):
            agent._atomic_write(p, head + "One.\nTwo.\nThree.\n")
        agent.begin_write_scope()
        with agent.write_reason("user edit"):
            agent._atomic_write(p, head + "One.\n")
        return p

    def test_newest_first(self):
        p = self._three_changes()
        h = agent.page_history(p)
        self.assertEqual([r["why"] for r in h], ["user-edit", "ingest"])

    def test_the_stats_describe_the_change_that_row_records(self):
        # Revision 1 (the "ingest" row) held the one-line body and was replaced by the
        # three-line one: +2. Revision 2 (the "user edit" row) held three lines and was
        # replaced by the current one: -2.
        p = self._three_changes()
        h = agent.page_history(p)
        self.assertEqual((h[1]["added"], h[1]["removed"]), (2, 0), h)
        self.assertEqual((h[0]["added"], h[0]["removed"]), (0, 2), h)

    def test_a_page_with_no_history_returns_nothing(self):
        p = self.w.page("entities/new.md", title="New Corp", type="entity",
                        body="# New Corp\n\n## Overview\n\nX.\n")
        self.assertEqual(agent.page_history(p), [])

    def test_unstamped_revisions_still_appear(self):
        # Every revision written before reasons existed has no suffix.
        p = self.w.page("entities/a.md", title="A Corp", type="entity",
                        body="# A Corp\n\n## Overview\n\nOne.\n")
        agent.set_write_reason("")
        agent._atomic_write(p, "# A Corp\n\n## Overview\n\nTwo.\n")
        h = agent.page_history(p)
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["why"], "")

    def test_a_stray_file_in_the_history_dir_is_ignored(self):
        p = self._three_changes()
        (agent.HISTORY_DIR / "entities" / "a.md" / "notes.md").write_text("hand-written")
        self.assertEqual(len(agent.page_history(p)), 2, "a non-revision file was listed")


if __name__ == "__main__":
    unittest.main()
