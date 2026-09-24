"""A link to a page that no longer exists is unwrapped, not left to rot.

repair_links' path pass answers a narrow question — "is this the wrong route to a page
that still exists?" — and when the page is simply gone it gives up. So one deleted page
leaves a dead link on every page that ever mentioned it. The case this was written for:
135 broken links to a single entities/united.md, a page the autolinker had been matching
inside "United Nations", "American Airlines" and the plain English word "united".

Unwrapping to the display text is the fix, and repointing is not: those 135 span three
different subjects, so any single target would be wrong for most of them. The text is
ordinary prose and reads correctly on its own — the same reasoning rename_page.py uses
when it strips links whose display text was the old title.

The precedence matters and is tested here: a link whose target exists somewhere else is
repointed, never unwrapped. Unwrapping is only for a target that exists nowhere.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent
import repair_links


class DanglingLinkTest(TempWikiTestCase):

    def _page(self, rel, body):
        return self.w.page(rel, title=rel.split("/")[-1][:-3].title(),
                           type="concept" if rel.startswith("concepts") else "entity",
                           body=body)

    def test_a_link_to_a_deleted_page_becomes_plain_text(self):
        self._page("concepts/fao.md",
                   "# Fao\n\n## Definition\n\nAn agency of the "
                   "[United](../entities/united.md) Nations.\n")
        repair_links.repair_links()
        self.assertIn("An agency of the United Nations.", self.w.disk("concepts/fao.md"))

    def test_it_reports_what_it_unwrapped(self):
        self._page("concepts/fao.md",
                   "# Fao\n\n## Definition\n\nThe [United](../entities/united.md) Nations.\n")
        r = repair_links.repair_links(dry_run=True)
        self.assertEqual(r["fixed_links"], 1)
        self.assertIn("1 dangling", r["detail"][0])

    def test_dry_run_changes_nothing(self):
        self._page("concepts/fao.md",
                   "# Fao\n\n## Definition\n\nThe [United](../entities/united.md) Nations.\n")
        before = self.w.disk("concepts/fao.md")
        repair_links.repair_links(dry_run=True)
        self.assertEqual(self.w.disk("concepts/fao.md"), before)

    def test_a_page_that_exists_elsewhere_is_repointed_not_unwrapped(self):
        # The whole point of the precedence: unwrapping is the last resort.
        self._page("entities/acme.md", "# Acme\n\n## Overview\n\nA firm.\n")
        self._page("concepts/widgets.md",
                   "# Widgets\n\n## Definition\n\nMade by "
                   "[Acme](../../entities/acme.md).\n")   # one too many ../
        repair_links.repair_links()
        out = self.w.disk("concepts/widgets.md")
        self.assertIn("[Acme](../entities/acme.md)", out,
                      "a recoverable link was unwrapped instead of repaired")

    def test_a_working_link_is_untouched(self):
        self._page("entities/acme.md", "# Acme\n\n## Overview\n\nA firm.\n")
        self._page("concepts/widgets.md",
                   "# Widgets\n\n## Definition\n\nMade by [Acme](../entities/acme.md).\n")
        before = self.w.disk("concepts/widgets.md")
        repair_links.repair_links()
        self.assertEqual(self.w.disk("concepts/widgets.md"), before)

    def test_an_external_link_is_never_unwrapped(self):
        self._page("concepts/x.md",
                   "# X\n\n## Definition\n\nSee [the report](https://example.com/a.md) "
                   "and [mail](mailto:a@b.com) and [top](#overview).\n")
        before = self.w.disk("concepts/x.md")
        repair_links.repair_links()
        self.assertEqual(self.w.disk("concepts/x.md"), before,
                         "an off-wiki link was treated as a dangling wiki page")

    def test_a_non_markdown_target_is_left_alone(self):
        # An image or attachment under raw/assets/ is not this pass's business.
        self._page("concepts/x.md",
                   "# X\n\n## Definition\n\nA [chart](../../raw/assets/chart.png).\n")
        before = self.w.disk("concepts/x.md")
        repair_links.repair_links()
        self.assertEqual(self.w.disk("concepts/x.md"), before)

    def test_a_link_with_no_display_text_is_left_alone(self):
        # Unwrapping would delete it outright, which is a repair nobody asked for.
        self._page("concepts/x.md", "# X\n\n## Definition\n\nSee [](../entities/gone.md).\n")
        before = self.w.disk("concepts/x.md")
        repair_links.repair_links()
        self.assertEqual(self.w.disk("concepts/x.md"), before)

    def test_every_occurrence_on_a_page_is_unwrapped(self):
        self._page("entities/aufscs.md",
                   "# Aufscs\n\n## Overview\n\n[United](../entities/united.md) for "
                   "separation, and again [United](../entities/united.md).\n")
        repair_links.repair_links()
        out = self.w.disk("entities/aufscs.md")
        self.assertNotIn("../entities/united.md", out)
        self.assertEqual(out.count("United"), 2, out)   # both, unwrapped, still present

    def test_the_repair_is_revertable(self):
        # Every write here goes through _atomic_write, so a bad repair is undoable from
        # the page's History view.
        p = self._page("concepts/fao.md",
                       "# Fao\n\n## Definition\n\nThe [United](../entities/united.md) "
                       "Nations.\n")
        repair_links.repair_links()
        revs = list((agent.HISTORY_DIR / "concepts" / "fao.md").glob("*.md"))
        self.assertTrue(revs, "the repair left no history entry to revert to")
        self.assertIn("[United](../entities/united.md)",
                      revs[0].read_text(encoding="utf-8"))

    def test_the_pages_leftover_history_is_not_mistaken_for_the_page(self):
        """The bug that made this pass a no-op on the real wiki.

        Deleting wiki/entities/united.md leaves wiki/.history/entities/united.md/ behind —
        a DIRECTORY with the page's name. The path pass looked for the filename with
        rglob over all of wiki/, rglob matches directories, and that leftover was the
        single match. So every dead link was "repaired" to ../.history/entities/united.md,
        which is not a page, is not servable, and buries the evidence that the page is
        gone. Silently, on all 135.
        """
        hist = agent.HISTORY_DIR / "entities" / "united.md"
        hist.mkdir(parents=True)
        (hist / "20260101T000000__ingest.md").write_text("the deleted page", encoding="utf-8")
        self._page("concepts/fao.md",
                   "# Fao\n\n## Definition\n\nAn agency of the "
                   "[United](../entities/united.md) Nations.\n")
        repair_links.repair_links()
        out = self.w.disk("concepts/fao.md")
        self.assertNotIn(".history", out, "a link was repointed into the history store")
        self.assertIn("An agency of the United Nations.", out)

    def test_a_stored_revision_is_never_a_repair_target(self):
        # Same root cause, one level down: the revision FILES are also named *.md and
        # would be offered as the destination for a link to a page of that name.
        hist = agent.HISTORY_DIR / "entities" / "acme.md"
        hist.mkdir(parents=True)
        (hist / "gone.md").write_text("not a page", encoding="utf-8")
        self._page("concepts/x.md",
                   "# X\n\n## Definition\n\nSee [Gone](../entities/gone.md).\n")
        repair_links.repair_links()
        out = self.w.disk("concepts/x.md")
        self.assertNotIn(".history", out)
        self.assertIn("See Gone.", out)

    def test_a_nested_link_with_no_recoverable_target_is_left_alone(self):
        """The old fallback invented a link rather than admitting it could not repair one.

        It truncated the mangled target at the first '[' — "[Backgammon](../sources/
        [backgammon].md)" became "[Backgammon](../sources)". That points at a DIRECTORY,
        which resolves, so lint fell silent while the real target was gone for good. A
        visible break that keeps getting reported beats an invented link that hides it,
        and agent._MANGLED_URL_RE heals this shape on the next autolink anyway.
        """
        self._page("concepts/x.md",
                   "# X\n\n## Definition\n\nSee "
                   "[Backgammon](../sources/[backgammon].md) here.\n")
        repair_links.repair_links()
        out = self.w.disk("concepts/x.md")
        self.assertNotIn("../sources)", out,
                         "a directory was invented as the link target")
        # What happens instead is the right thing: the dangling pass sees a target that
        # exists nowhere and unwraps it, so the words survive as prose and the autolinker
        # re-links them from the title map on the next run. Self-healing, and nothing is
        # silently pointed at a directory.
        self.assertIn("See Backgammon here.", out, out)

    def test_a_nested_link_whose_target_is_recoverable_is_still_repaired(self):
        self._page("entities/acme.md", "# Acme\n\n## Overview\n\nA firm.\n")
        self._page("concepts/y.md",
                   "# Y\n\n## Definition\n\nSee "
                   "[Acme](../entities/[acme](../entities/acme.md)) here.\n")
        repair_links.repair_links()
        self.assertIn("[Acme](../entities/acme.md)", self.w.disk("concepts/y.md"))

    def test_a_history_file_is_never_rewritten(self):
        self._page("concepts/fao.md",
                   "# Fao\n\n## Definition\n\nThe [United](../entities/united.md) Nations.\n")
        repair_links.repair_links()
        repair_links.repair_links()
        rev = sorted((agent.HISTORY_DIR / "concepts" / "fao.md").glob("*.md"))[0]
        self.assertIn("[United](../entities/united.md)", rev.read_text(encoding="utf-8"),
                      "a stored revision was 'repaired', destroying the record")


if __name__ == "__main__":
    unittest.main()
