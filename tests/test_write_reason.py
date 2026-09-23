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
    """A row is a VERSION, labelled with what created it.

    The trap this guards: revision file R_i holds the content as it was BEFORE the write
    at T_i, so R_i's reason describes the write that DESTROYED that content, not the one
    that made it. Attaching it to R_i's own row labels a version with the cause of its own
    deletion, and puts the newest write's label one row below where it belongs — which is
    exactly what shipped first and what the screenshot caught.
    """

    def _page_with_writes(self, reasons):
        p = self.w.page("concepts/climate-change.md", title="Climate Change",
                        type="concept",
                        body="# Climate Change\n\n## Definition\n\n" + "line\n" * 10)
        fm = p.read_text(encoding="utf-8").split("---\n")[1]
        head = f"---\n{fm}---\n\n# Climate Change\n\n## Definition\n\n"
        for n, why in enumerate(reasons, start=11):
            agent.begin_write_scope()
            with agent.write_reason(why):
                agent._atomic_write(p, head + "line\n" * n)
        return p

    def test_the_newest_reason_lands_on_the_current_version(self):
        p = self._page_with_writes(["user edit", "ingest"])
        rows = agent.page_history(p)
        self.assertTrue(rows[0]["current"])
        self.assertEqual(rows[0]["why"], "ingest",
                         "the last write's label is not on the page it produced")

    def test_a_version_is_not_labelled_with_its_own_deletion(self):
        p = self._page_with_writes(["user edit", "ingest"])
        rows = agent.page_history(p)
        # The version below current was produced by the "user edit"; the "ingest" is what
        # replaced it, and must not appear here.
        # Stored sanitized — serve.py maps "user-edit" to "your edit" for display.
        self.assertEqual(rows[1]["why"], "user-edit")

    def test_each_row_reports_its_own_size(self):
        p = self._page_with_writes(["a", "b"])
        rows = agent.page_history(p)
        self.assertEqual(rows[0]["size"], len(p.read_text(encoding="utf-8").encode("utf-8")),
                         "the current row did not report the current page's size")
        sizes = [r["size"] for r in rows]
        self.assertEqual(sizes, sorted(sizes, reverse=True),
                         f"versions should grow toward the current one: {sizes}")

    def test_the_delta_is_what_produced_that_version(self):
        # Each write adds exactly one line to the body.
        p = self._page_with_writes(["a", "b", "c"])
        rows = agent.page_history(p)
        for r in rows[:-1]:
            self.assertEqual((r["added"], r["removed"]), (1, 0), rows)

    def test_the_oldest_row_claims_nothing_it_cannot_know(self):
        p = self._page_with_writes(["a", "b"])
        oldest = agent.page_history(p)[-1]
        self.assertTrue(oldest["earliest"])
        self.assertEqual(oldest["when"], "", "a creation time was invented for it")
        self.assertEqual(oldest["why"], "")
        self.assertEqual((oldest["added"], oldest["removed"]), (0, 0))

    def test_every_stored_revision_is_still_reachable(self):
        p = self._page_with_writes(["a", "b", "c"])
        rows = agent.page_history(p)
        ids = [r["id"] for r in rows if r["id"]]
        stored = sorted(f.stem for f in
                        (agent.HISTORY_DIR / "concepts" / "climate-change.md").glob("*.md"))
        self.assertEqual(sorted(ids), stored, "a revision dropped out of the list")
        self.assertIsNone(rows[0]["id"], "the current page is not a stored revision")

    def test_a_page_with_no_history_shows_only_the_current_version(self):
        p = self.w.page("entities/new.md", title="New Corp", type="entity",
                        body="# New Corp\n\n## Overview\n\nX.\n")
        rows = agent.page_history(p)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["current"])
        self.assertEqual(rows[0]["when"], "")

    def test_unstamped_revisions_still_appear(self):
        # Every revision written before reasons existed has no suffix.
        p = self.w.page("entities/a.md", title="A Corp", type="entity",
                        body="# A Corp\n\n## Overview\n\nOne.\n")
        agent.set_write_reason("")
        agent._atomic_write(p, "# A Corp\n\n## Overview\n\nTwo.\n")
        rows = agent.page_history(p)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["why"], "")

    def test_a_stray_file_in_the_history_dir_is_ignored(self):
        p = self._page_with_writes(["a", "b"])
        (agent.HISTORY_DIR / "concepts" / "climate-change.md" / "notes.md").write_text("x")
        rows = agent.page_history(p)
        self.assertEqual(len([r for r in rows if r["id"]]), 2,
                         "a non-revision file was listed as a version")


if __name__ == "__main__":
    unittest.main()
