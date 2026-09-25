"""A short title linked inside a longer one gets upgraded when the longer page appears.

Observed on a source page's ## Entities list:

    - [New York University](../entities/new-york-university.md) Langone Health
    - [University of Michigan](../entities/university-of-michigan.md) Medical School

Both longer pages existed — university-of-michigan-medical-school.md was created in that
very ingest — and neither list row ever recovered.

The title map is already sorted longest-first, so when both pages exist the long title
wins and this never happens. The failure needs a sequence: the short page exists, the list
is linked, and the long page is created afterwards. That is the normal shape of an ingest,
where the source page is written early and entity pages are created over the next twenty
rounds.

Why re-linking could not fix it: group 1 of the combined regex wins at every position,
which is the invariant that keeps the autolinker out of existing links. An upgrade is
therefore only reachable when the phrase has bare words BEFORE the linked part — in
"CASA of [Monterey County](url)" group 2 starts matching at "CASA", ahead of the "[", so
it wins. When the linked span starts the phrase, group 1 matches there first and returns
it unchanged, and group 2 is never tried. Permanently.

So the already-linked forms get their own pass ahead of the combined regex. They are safe
to run unprotected because every alternative contains the title's literal words AND
markdown link syntax, so they cannot match arbitrary prose and cannot match inside an
unrelated link's display text.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class UpgradeTest(TempWikiTestCase):

    def _short(self):
        self.w.page("entities/new-york-university.md", title="New York University",
                    type="entity", body="# New York University\n\nA university.\n")

    def _long(self):
        self.w.page("entities/nyu-langone-health.md",
                    title="New York University Langone Health", type="entity",
                    body="# NYU Langone Health\n\n## Overview\n\nA hospital.\n")

    def _src(self, body):
        self.w.page("sources/s.md", title="S", type="source", body=body)
        agent._autolink({"path": "wiki/sources/s.md"})
        return self.w.disk("sources/s.md")

    def _row(self):
        return next(l for l in self.w.disk("sources/s.md").splitlines()
                    if l.startswith("- "))

    def test_the_observed_case_heals_on_the_next_autolink(self):
        self._short()
        self._src("# S\n\n## Entities\n\n- New York University Langone Health\n")
        self.assertIn("](../entities/new-york-university.md) Langone Health", self._row())
        self._long()
        agent._autolink({"path": "wiki/sources/s.md"})
        self.assertEqual(
            self._row(),
            "- [New York University Langone Health](../entities/nyu-langone-health.md)")

    def test_when_both_pages_exist_the_longer_wins_outright(self):
        # The map is sorted longest-first, so no upgrade is needed in the first place.
        self._short()
        self._long()
        self._src("# S\n\n## Entities\n\n- New York University Langone Health\n")
        self.assertIn("nyu-langone-health.md", self._row())
        self.assertNotIn("new-york-university.md", self._row())

    def test_a_linked_span_in_the_middle_still_upgrades(self):
        # The case that already worked, kept so the two stay symmetrical.
        self.w.page("entities/monterey-county.md", title="Monterey County",
                    type="entity", body="# Monterey County\n\nA county.\n")
        self.w.page("entities/casa-of-monterey-county.md", title="CASA of Monterey County",
                    type="entity", body="# CASA\n\nA charity.\n")
        out = self._src("# S\n\n## Summary\n\nCASA of Monterey County filed suit.\n")
        self.assertIn("[CASA of Monterey County](../entities/casa-of-monterey-county.md)",
                      out, out)

    def test_it_converges(self):
        self._short()
        self._src("# S\n\n## Entities\n\n- New York University Langone Health\n")
        self._long()
        agent._autolink({"path": "wiki/sources/s.md"})
        once = self.w.disk("sources/s.md")
        for _ in range(3):
            agent._autolink({"path": "wiki/sources/s.md"})
        self.assertEqual(self.w.disk("sources/s.md"), once, "autolink stopped converging")

    def test_the_upgraded_link_is_not_then_unlinked_as_a_repeat(self):
        """The regression this shipped with, caught by the golden corpus.

        The upgrade marked the section's mention as spent, so the combined pass that runs
        over the same line next read the link the upgrade had just written as a repeat and
        removed it. Three golden cases went from a correct link to no link at all. The
        upgrade now leaves `_seen` alone and lets the combined pass account for it.
        """
        self._short()
        self._long()
        out = self._src("# S\n\n## Summary\n\nNew York University Langone Health is a "
                        "hospital.\n")
        self.assertIn("](../entities/nyu-langone-health.md)", out, out)

    def test_a_one_word_title_needs_no_upgrade_pass(self):
        self.assertIsNone(agent._title_upgrade_re("Measles"))
        self.assertIsNotNone(agent._title_upgrade_re("New York University Langone Health"))

    def test_bare_prose_is_left_to_the_combined_pass(self):
        """The pattern now matches bare text too, and the replacer declines it.

        Generalising the pattern to handle SEVERAL linked sub-spans — "[Planned
        Parenthood](…) of [California](…)" — meant making every piece of link syntax
        optional, so it matches the bare form as well. Upgrading there would skip the
        once-per-section accounting the combined pass does, so the replacer returns the
        match untouched and lets group 2 handle it. Asserted as behaviour, because that
        is where the guarantee now lives.
        """
        self._short()
        self._long()
        out = self._src("# S\n\n## Summary\n\nNew York University Langone Health runs "
                        "it, and New York University Langone Health again.\n")
        # Linked once for the section, not twice, and pointing at the long page.
        self.assertEqual(out.count("](../entities/nyu-langone-health.md)"), 1, out)

    def test_it_never_starts_inside_another_links_text(self):
        """The guard that stops this pass creating the damage it repairs.

        "Monterey County" matches the tail of "[CASA of Monterey County](…)", and
        rewriting that produced "[CASA of [Monterey County](…)](…)" — the malformed
        [[a](b)](c) shape that took one page to twenty-eight layers. Every "](" consumed
        must have its own "[" inside the match.
        """
        self.w.page("entities/monterey-county.md", title="Monterey County",
                    type="entity", body="# Monterey County\n\nA county.\n")
        self.w.page("entities/casa-of-monterey-county.md", title="CASA of Monterey County",
                    type="entity", body="# CASA\n\nA charity.\n")
        out = self._src("# S\n\n## Summary\n\nCASA of Monterey County filed in "
                        "Monterey County court.\n")
        self.assertIn("[CASA of Monterey County](../entities/casa-of-monterey-county.md)",
                      out, out)
        self.assertNotIn("[CASA of [", out, "the upgrade created a malformed link")

    def test_an_unrelated_link_is_not_touched(self):
        self._short()
        self._long()
        out = self._src("# S\n\n## Summary\n\nSee [the report](https://example.com/nyu) "
                        "for more.\n")
        self.assertIn("[the report](https://example.com/nyu)", out, out)

    def test_a_list_row_upgrade_does_not_spend_the_prose_mention(self):
        self._short()
        self._src("# S\n\n## Entities\n\n- New York University Langone Health\n\n"
                  "New York University Langone Health runs the hospital.\n")
        self._long()
        agent._autolink({"path": "wiki/sources/s.md"})
        out = self.w.disk("sources/s.md")
        self.assertEqual(out.count("](../entities/nyu-langone-health.md)"), 2, out)


if __name__ == "__main__":
    unittest.main()
