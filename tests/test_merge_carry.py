"""`merge_page(carry=True)` — placing the merged-away page's remainder for you.

Asked for directly: *"can you make merges easier? editing deltas by hand is a real pain"*,
over a `find_duplicate_pages.py` run showing **35 groups, 73 pages**. Everything about a
merge was already mechanical except one step: the merge refuses while the loser still says
anything the survivor does not, prints those lines, and leaves you to move them. Across 35
groups that one step IS the cost of the cleanup.

`carry=True` appends each outstanding line to the survivor's section of the same name,
creating the section where the survivor has none.

**Three things that had to be right, and each is a test here:**

  * **A summary is never appended to.** Appending a sentence to an `## Overview` is
    exactly the accretion `_accreted_dated_sentences` refuses and `overview_drift.py`
    reports on 11,000 pages. A convenience that manufactured the wiki's worst existing
    defect one merge at a time would be a bad trade at any price. Summary deltas come
    back with the `update_section` call that resolves them — usually one or two sentences,
    so the hand work becomes "rewrite one paragraph" rather than "move every delta".
  * **A section of the same name is joined; a differently-named one is created and
    REPORTED.** This module's first version asserted that `## Political Stances` would
    land in the survivor's `## Positions`, and the code's docstring claimed it. It cannot:
    deciding those headings name one subject is a judgement, not a string comparison. So
    the carry does what `append_section` does — creates it and says so — because creating
    a section is legitimate, a refusal would have no escape hatch, and only the operator
    can tell whether the two should be one.
  * **The delta is sentences, not the paragraph holding them.** A section's prose is one
    line per paragraph, so an Overview repeating the survivor's first sentence and adding
    one clause came back as a whole paragraph and had to be diffed by eye. That was the
    complaint, restated.

**And one bug that only an end-to-end run could find.** Every unit test passed while the
carry was being computed, reported as done, and silently dropped — because a summary delta
refuses the merge and `return`s before the survivor write at the bottom of the function.
Running the real CLI against an AIPAC-shaped fixture showed the two carried sections
missing from the file. `CarriedTextReachesDiskTest` is that case.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

# Paragraphs are ONE LINE each, as the wiki actually writes them — `_claims` compares line
# by line, so a fixture that hard-wraps its prose splits differently on the two pages and
# manufactures outstanding deltas that no real page would have. Eight tests failed that
# way before the fixture was fixed, and not one of them was about wrapping.
SURV = """## Overview

The American Israel Public Affairs Committee is a lobbying group in Washington that advocates pro-Israel policies to the United States Congress.

## Background

Founded in 1951 by Isaiah Kenen as the American Zionist Committee for Public Affairs.
"""

LOSER = """## Overview

AIPAC is a lobbying group in Washington that advocates pro-Israel policies to the United States Congress.

## Background

Founded in 1951 by Isaiah Kenen as the American Zionist Committee for Public Affairs.

## Positions

AIPAC supports continued United States military assistance to Israel at current levels.
"""


def _pair(w, surv_body=SURV, loser_body=LOSER, surv_title=None, loser_title=None):
    w.page("entities/survivor.md",
           title=surv_title or "American Israel Public Affairs Committee",
           body=surv_body)
    w.page("entities/loser.md",
           title=loser_title or "American Israel Public Affairs Committee (AIPAC)",
           aliases=["AIPAC"], body=loser_body)
    return w.wiki / "entities" / "survivor.md"


class CarryTest(unittest.TestCase):
    def test_a_section_the_survivor_lacks_is_created_with_the_carried_line(self):
        with TempWiki() as w:
            surv = _pair(w)
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            text = surv.read_text()
            self.assertIn("## Positions", text)
            self.assertIn("military assistance to Israel", text)

    def test_a_line_lands_in_the_survivors_section_of_the_same_name(self):
        """Matched on `_norm_heading`, so "Positions:" and "Positions" are one section and
        the carried line joins the body already there rather than starting a rival."""
        with TempWiki() as w:
            surv = _pair(w, surv_body=SURV + "\n## Positions:\n\nIt lobbies Congress "
                                             "directly on appropriations bills.\n")
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            text = surv.read_text()
            self.assertEqual(text.count("## Positions"), 1)
            self.assertIn("lobbies Congress directly", text)
            self.assertIn("military assistance to Israel", text)

    def test_a_differently_named_section_is_created_and_reported(self):
        """**The honest limit of the carry, and it was over-claimed first.** This module's
        first version asserted that `## Political Stances` would land in the survivor's
        `## Positions`, and said so in its docstring. It cannot: deciding those two
        headings name one subject is a judgement, not a string comparison, and a tool that
        guessed would silently merge a section about policy positions into one about job
        appointments.

        So the section is created and the creation is REPORTED, which is exactly what
        `append_section` does when asked for a template heading on a page that calls it
        something else — report rather than refuse, because creating a section is
        legitimate and a refusal would have no escape hatch. The report names the
        survivor's existing sections so the overlap is visible."""
        loser = LOSER + ("\n## Political Stances\n\n"
                         "The group opposes any conditioning of that assistance on "
                         "policy changes by the recipient government.\n")
        with TempWiki() as w:
            surv = _pair(w, surv_body=SURV + "\n## Positions\n\nIt lobbies Congress "
                                             "directly on appropriations bills.\n",
                         loser_body=loser)
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            self.assertIn("Political Stances", r["new_sections"],
                          "a created section must be reported, not slipped in")
            self.assertNotIn("Positions", r["new_sections"],
                             "a section the survivor already has is not a new one")
            self.assertIn("conditioning of that assistance", surv.read_text())

    def test_what_was_carried_is_reported(self):
        with TempWiki() as w:
            _pair(w)
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertEqual(list(r["carried"]), ["Positions"])
            self.assertEqual(len(r["carried"]["Positions"]), 1)

    def test_a_dry_run_carries_nothing_to_disk(self):
        with TempWiki() as w:
            surv = _pair(w)
            before = surv.read_text()
            r = agent.merge_page("entities/loser.md", "entities/survivor.md",
                                 carry=True, dry_run=True)
            self.assertEqual(list(r["carried"]), ["Positions"])
            self.assertEqual(surv.read_text(), before)
            self.assertTrue((w.wiki / "entities" / "loser.md").exists())

    def test_the_carry_goes_above_the_generated_sources_section(self):
        """`## Sources` is rendered from frontmatter and is always last. A new section
        appended after it would be rewritten or orphaned by the next write."""
        with TempWiki() as w:
            surv = _pair(w, surv_body=SURV + "\n## Sources\n\n- [A](../sources/a.md)\n")
            agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            text = surv.read_text()
            self.assertLess(text.index("## Positions"), text.index("## Sources"))

    def test_a_merge_with_nothing_outstanding_carries_nothing(self):
        with TempWiki() as w:
            _pair(w, loser_body=SURV)
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"])
            self.assertFalse(r.get("carried"))

    def test_carrying_still_repoints_links_and_carries_names_and_sources(self):
        """The carry is an addition to the merge, not a replacement for it. A convenience
        flag that quietly skipped the link repointing would lose more than it saved."""
        with TempWiki() as w:
            w.page("sources/b-2026-y.md", title="B", type="source", body="## Summary\n\nx\n")
            w.page("entities/survivor.md",
                   title="American Israel Public Affairs Committee", body=SURV)
            w.page("entities/loser.md", title="Loser Name", aliases=["AIPAC", "LN"],
                   body=LOSER, sources=["sources/b-2026-y.md"])
            w.page("entities/other.md", title="Other",
                   body="## Overview\n\nSee [LN](../entities/loser.md).\n")
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            self.assertIn("entities/other.md", r["repointed"])
            self.assertIn("Loser Name", r["aliases"])
            self.assertEqual(r["sources_added"], ["sources/b-2026-y.md"])
            # Relativized from the referring page's own directory, so a sibling in
            # entities/ gets a bare `survivor.md` rather than `../entities/survivor.md`.
            other = (w.wiki / "entities" / "other.md").read_text()
            self.assertIn("](survivor.md)", other)
            self.assertNotIn("loser.md", other)
            self.assertFalse((w.wiki / "entities" / "loser.md").exists())


class SummaryIsNeverAppendedTest(unittest.TestCase):
    EXTRA = ("\n\nIt does not operate as a political action committee despite the name "
             "it carries, which is a common and persistent misunderstanding.")

    def _loser_with_summary_delta(self):
        return LOSER.replace("States Congress.", "States Congress." + self.EXTRA)

    def test_a_summary_delta_refuses_the_merge_rather_than_appending(self):
        with TempWiki() as w:
            surv = _pair(w, loser_body=self._loser_with_summary_delta())
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNotNone(r["error"])
            self.assertNotIn("political action committee", surv.read_text(),
                             "appending to a summary is the accretion the guards refuse")
            self.assertTrue((w.wiki / "entities" / "loser.md").exists(),
                            "a refused merge must not delete the page it refused")

    def test_the_refusal_names_the_call_that_resolves_it(self):
        """Principle 4. A refusal that names no move is one the operator cannot act on,
        and this one has a single right answer to point at."""
        with TempWiki() as w:
            _pair(w, loser_body=self._loser_with_summary_delta())
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIn("update_section(", r["error"])
            self.assertIn("section='Overview'", r["error"])
            self.assertIn("entities/survivor.md", r["error"])

    def test_the_non_summary_half_is_carried_even_though_the_merge_refuses(self):
        """The point of the design: the refusal is about the summary alone, so everything
        placeable has already been placed and re-running after one rewrite completes."""
        with TempWiki() as w:
            surv = _pair(w, loser_body=self._loser_with_summary_delta())
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNotNone(r["error"])
            self.assertEqual(list(r["carried"]), ["Positions"])
            self.assertIn("military assistance to Israel", surv.read_text())

    def test_a_concept_pages_definition_is_a_summary_too(self):
        with TempWiki() as w:
            w.page("concepts/survivor.md", title="Gross Domestic Product", type="concept",
                   body="## Definition\n\nThe total market value of goods produced.\n")
            w.page("concepts/loser.md", title="GDP", type="concept",
                   body="## Definition\n\nThe total market value of goods produced. It "
                        "is measured quarterly by the statistical agency of each "
                        "country and revised afterwards.\n")
            r = agent.merge_page("concepts/loser.md", "concepts/survivor.md", carry=True)
            self.assertIsNotNone(r["error"])
            self.assertIn("section='Definition'", r["error"])

    def test_force_still_overrides_everything(self):
        """The one mode that can lose text, and it has to keep working — the whole reason
        `--carry` can afford to be strict about summaries is that `--force` exists."""
        with TempWiki() as w:
            _pair(w, loser_body=self._loser_with_summary_delta())
            r = agent.merge_page("entities/loser.md", "entities/survivor.md",
                                 carry=True, force=True)
            self.assertIsNone(r["error"], r["error"])
            self.assertFalse((w.wiki / "entities" / "loser.md").exists())


class SentenceLevelDeltaTest(unittest.TestCase):
    def test_the_delta_is_the_new_sentences_not_the_paragraph_holding_them(self):
        """The complaint, restated: a section's prose is one line per paragraph, so an
        Overview that repeats the survivor's first sentence and adds one clause came back
        as a whole paragraph for the operator to diff by eye."""
        extra = ("It does not operate as a political action committee despite the name "
                 "it carries, which is a persistent misunderstanding.")
        with TempWiki() as w:
            _pair(w, loser_body=LOSER.replace("States Congress.",
                                              "States Congress. " + extra))
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            d = r["outstanding_detail"]
            self.assertEqual(len(d), 1)
            self.assertIn("political action committee", d[0]["new"])
            self.assertNotIn("lobbying group in Washington", d[0]["new"],
                             "the sentence the survivor already has must be dropped")

    def test_only_the_new_sentence_is_carried_out_of_a_mixed_paragraph(self):
        with TempWiki() as w:
            surv = _pair(
                w,
                surv_body=SURV + "\n## Positions\n\nIt lobbies Congress on "
                                 "appropriations bills every single year.\n",
                loser_body=SURV + "\n## Positions\n\nIt lobbies Congress on "
                                  "appropriations bills every single year. It also "
                                  "opposes any conditioning of military aid on policy "
                                  "changes by the recipient government.\n")
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            text = surv.read_text()
            self.assertIn("conditioning of military aid", text)
            self.assertEqual(text.count("lobbies Congress on appropriations"), 1,
                             "the half the survivor already had must not be duplicated")

    def test_a_line_with_no_droppable_sentence_is_carried_whole(self):
        """The normal case for a bullet, and the fallback that stops the sentence filter
        from carrying an empty string when it cannot find a split."""
        with TempWiki() as w:
            surv = _pair(w)
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            self.assertIn("AIPAC supports continued United States military assistance",
                          surv.read_text())


class CarriedTextReachesDiskTest(unittest.TestCase):
    """The regression that passed every unit test.

    `_merge_page_impl` writes the survivor once, near the bottom, after the aliases and
    sources are folded in. A summary delta refuses and `return`s before that point — so a
    carry computed above it was reported in `result["carried"]` and never written. The
    tests above all asserted on the result dict or on a merge that succeeded, so none of
    them looked at the file in the one case that was broken. The real CLI on an
    AIPAC-shaped fixture showed two sections missing.
    """

    def test_a_carry_that_ends_in_a_refusal_is_still_on_disk(self):
        extra = ("\n\nIt does not operate as a political action committee despite the "
                 "name, which is a persistent misunderstanding among observers.")
        with TempWiki() as w:
            surv = _pair(w, loser_body=LOSER.replace("States Congress.",
                                                     "States Congress." + extra))
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNotNone(r["error"])
            self.assertTrue(r.get("carried"))
            self.assertIn("military assistance to Israel", surv.read_text(),
                          "reported as carried, so it has to be in the file")

    def test_a_carry_with_no_other_change_is_still_on_disk(self):
        """The other half of the same bug. The write at the bottom fires on
        `new_s != s_on_disk`; comparing against the CARRIED text instead means a merge
        whose only change is the carry computes it and writes nothing."""
        with TempWiki() as w:
            # Same title and no new alias, so nothing but the carry changes the
            # survivor. The loser's Overview is the survivor's verbatim, since a
            # differently-worded summary would refuse before the write is reached.
            surv = _pair(w, surv_title="Same Name", loser_title="Same Name",
                         loser_body=SURV + "\n## Positions\n\nAIPAC supports continued "
                                           "United States military assistance to Israel "
                                           "at current levels.\n")
            (w.wiki / "entities" / "loser.md").write_text(
                (w.wiki / "entities" / "loser.md").read_text().replace(
                    'aliases: ["AIPAC"]\n', ""), encoding="utf-8")
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            self.assertEqual(r["aliases"], [])
            self.assertEqual(r["sources_added"], [])
            self.assertIn("military assistance to Israel", surv.read_text())


class WithoutCarryTest(unittest.TestCase):
    def test_the_default_still_refuses_and_names_carry(self):
        """Carrying is opt-in: placing text is a decision, and the refusal that names the
        flag is how anyone finds out it exists."""
        with TempWiki() as w:
            surv = _pair(w)
            r = agent.merge_page("entities/loser.md", "entities/survivor.md")
            self.assertIsNotNone(r["error"])
            self.assertIn("--carry", r["error"])
            self.assertNotIn("## Positions", surv.read_text())
            self.assertTrue((w.wiki / "entities" / "loser.md").exists())


if __name__ == "__main__":
    unittest.main()
