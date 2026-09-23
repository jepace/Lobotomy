"""Undo everything one named pass wrote, without undoing what came after it.

Reverting one page from its History view is the answer to one bad repair. It is not the
answer to "that pass touched 900 pages and I want them back" — which is the position a
repair_links run left the real wiki in.

Every write already records its reason in the revision filename, so a pass is addressable
after the fact. The care is all in deciding which pages are safe: a revision holds the
content as it was BEFORE the write that produced it, so the NEWEST revision naming this
pass is what "the pass's write is still the current content" means. If an ingest ran
afterwards, reverting would throw that ingest away — so those pages are reported and left
alone. An undo that costs you the work that came after it is not an undo.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent
import undo_pass


class UndoPassTest(TempWikiTestCase):

    def _page(self, rel="entities/a.md", body="A firm."):
        return self.w.page(rel, title="A Corp", type="entity",
                           body=f"# A Corp\n\n## Overview\n\n{body}\n")

    def _write(self, p, text, why):
        agent.begin_write_scope()
        fm = p.read_text(encoding="utf-8").split("---\n")[1]
        with agent.write_reason(why):
            agent._atomic_write(p, f"---\n{fm}---\n\n# A Corp\n\n## Overview\n\n{text}\n")

    def test_it_restores_the_content_the_pass_wrote_over(self):
        p = self._page(body="The original text.")
        self._write(p, "The repaired text.", "repair-links")
        revertable, _skipped = undo_pass.plan("repair-links")
        self.assertEqual(len(revertable), 1)
        undo_pass.plan("repair-links")
        page, rev, _why = revertable[0]
        self.assertIn("The original text.", rev.read_text(encoding="utf-8"))

    def test_a_page_written_again_since_is_left_alone(self):
        # The case that makes this safe: undoing the repair must not cost the ingest.
        p = self._page(body="The original text.")
        self._write(p, "The repaired text.", "repair-links")
        self._write(p, "Text an ingest wrote afterwards.", "ingest")
        revertable, skipped = undo_pass.plan("repair-links")
        self.assertEqual(revertable, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("written again since", skipped[0][2])
        self.assertIn("ingest", skipped[0][2])

    def test_several_writes_by_one_pass_are_undone_as_a_whole(self):
        p = self._page(body="The original text.")
        self._write(p, "First repair.", "repair-links")
        self._write(p, "Second repair.", "repair-links")
        revertable, _ = undo_pass.plan("repair-links")
        self.assertEqual(len(revertable), 1)
        self.assertIn("The original text.", revertable[0][1].read_text(encoding="utf-8"),
                      "it restored a midpoint of the pass rather than the state before it")

    def test_another_passs_writes_are_not_touched(self):
        p = self._page(body="Original.")
        self._write(p, "Healed.", "heal")
        revertable, skipped = undo_pass.plan("repair-links")
        self.assertEqual((revertable, skipped), ([], []))

    def test_a_page_deleted_since_is_reported_not_crashed_on(self):
        p = self._page(body="Original.")
        self._write(p, "Repaired.", "repair-links")
        p.unlink()
        revertable, skipped = undo_pass.plan("repair-links")
        self.assertEqual(revertable, [])
        self.assertIn("no longer exists", skipped[0][2])

    def test_a_page_with_no_history_at_all_is_ignored(self):
        self._page(body="Never written again.")
        revertable, skipped = undo_pass.plan("repair-links")
        self.assertEqual((revertable, skipped), ([], []))

    def test_the_undo_is_itself_recorded(self):
        # So a wrong undo is undoable, like every other write in this directory.
        p = self._page(body="The original text.")
        self._write(p, "The repaired text.", "repair-links")
        revertable, _ = undo_pass.plan("repair-links")
        page, rev, _ = revertable[0]
        agent.begin_write_scope()          # as undo_pass.main does, per page
        with agent.write_reason("undo-repair-links"):
            agent._atomic_write(page, rev.read_text(encoding="utf-8"))
        names = [f.name for f in (agent.HISTORY_DIR / "entities" / "a.md").glob("*.md")]
        self.assertTrue(any("__undo-repair-links.md" in n for n in names), names)
        self.assertIn("The original text.", self.w.disk("entities/a.md"))

    def test_it_scopes_to_the_pass_you_name(self):
        a = self._page("entities/a.md", "Original A.")
        b = self._page("entities/b.md", "Original B.")
        self._write(a, "Repaired A.", "repair-links")
        self._write(b, "Relinked B.", "relink")
        revertable, _ = undo_pass.plan("repair-links")
        self.assertEqual([p.name for p, _r, _w in revertable], ["a.md"])


if __name__ == "__main__":
    unittest.main()
