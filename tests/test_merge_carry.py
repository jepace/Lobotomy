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


class RefusalNamesTheSurvivorsSectionTest(unittest.TestCase):
    """**Reported from a live run, a day after the flag shipped.** Merging
    `concepts/war-in-iran.md` and `concepts/iran-war.md` into `entities/us-iran-war.md`,
    the refusal said:

        update_section(path='wiki/entities/us-iran-war.md', section='Definition', ...)

    `Definition` is the LOSERS' heading — they are concept pages. The survivor is an
    entity, whose summary is `## Overview`, so following that instruction is either refused
    for a section that does not exist or, through `append_section`, puts a `## Definition`
    onto an entity page. Principle 4's documented worst case: a refusal that renames one
    violation into another.

    Nineteen tests passed over this, because every one of them merged an entity into an
    entity, so the loser's heading and the survivor's were the same word.
    """

    LOSER = ("## Definition\n\nThe war in Iran refers to the military conflict involving "
             "the United States and Iran that escalated sharply in February 2026, "
             "characterised by airstrikes and naval confrontations.\n")

    def _concept_into_entity(self, w, surv_body):
        w.page("entities/us-iran-war.md", title="US-Iran War", type="entity",
               body=surv_body)
        w.page("concepts/war-in-iran.md", title="War in Iran", type="concept",
               body=self.LOSER)
        return agent.merge_page("concepts/war-in-iran.md", "entities/us-iran-war.md",
                                carry=True)

    def test_the_refusal_names_the_survivors_summary_not_the_losers(self):
        with TempWiki() as w:
            r = self._concept_into_entity(
                w, "## Overview\n\nA conflict between two states that began in 2026.\n")
            self.assertIn("section='Overview'", r["error"])
            self.assertNotIn("section='Definition'", r["error"])
            self.assertIn("update_section(", r["error"])

    def test_the_losers_heading_is_still_quoted_as_the_source_of_the_delta(self):
        """Both names are needed and they are different things: where the text is now, and
        where it has to go."""
        with TempWiki() as w:
            r = self._concept_into_entity(
                w, "## Overview\n\nA conflict between two states that began in 2026.\n")
            self.assertIn("## Definition", r["error"])

    def test_a_survivor_with_no_summary_section_is_told_to_append_one(self):
        """`update_section` on a section that is not there is a wasted round. The name
        comes from `_OPENER`, so an entity is told Overview and a concept Definition."""
        with TempWiki() as w:
            r = self._concept_into_entity(
                w, "## Background\n\nIt began after a long period of sanctions.\n")
            self.assertIn("append_section(", r["error"])
            self.assertIn("section='Overview'", r["error"])

    def test_the_survivors_own_heading_wins_over_the_template(self):
        """A page calling its summary something unusual must not be told to grow a second
        one beside it."""
        with TempWiki() as w:
            w.page("entities/us-iran-war.md", title="US-Iran War", type="entity",
                   body="## Definition\n\nA conflict that began in 2026 and continues.\n")
            w.page("concepts/war-in-iran.md", title="War in Iran", type="concept",
                   body=self.LOSER)
            r = agent.merge_page("concepts/war-in-iran.md", "entities/us-iran-war.md",
                                 carry=True)
            self.assertIn("section='Definition'", r["error"])
            self.assertIn("update_section(", r["error"])

    def test_a_concept_survivor_with_no_summary_is_told_Definition(self):
        with TempWiki() as w:
            w.page("concepts/survivor.md", title="Warfare", type="concept",
                   body="## Origins & History\n\nIt has been studied since antiquity.\n")
            w.page("concepts/loser.md", title="War", type="concept",
                   body="## Definition\n\nAn organised armed conflict between states or "
                        "factions, pursued for political ends.\n")
            r = agent.merge_page("concepts/loser.md", "concepts/survivor.md", carry=True)
            self.assertIn("section='Definition'", r["error"])
            self.assertIn("append_section(", r["error"])


class CarriedListStaysAListTest(unittest.TestCase):
    """**Also reported from that run.** `"\\n\\n".join` looked obviously right and broke
    every list it touched. The losers' `## Contradictions` is a claim followed by its
    status, and carrying them with a blank line between made three one-item lists out of
    one structure, orphaning each status from the claim it belongs to:

        - **Claim**: The administration characterised the agreement as a step toward stability.

        - **Claim**: Critics characterise the deal as a cynical failure.

        - **Status**: unresolved as of 2026-07-10.
    """

    CONTRADICTIONS = (
        "## Contradictions\n\n"
        "- **Claim**: The administration has characterised the agreement as a step "
        "toward regional stability and a win.\n"
        "  Status: unresolved as of 2026-07-10\n"
        "- **Claim**: Critics including columnist Thomas L. Friedman characterise the "
        "deal as a cynical and failed bargain.\n"
        "  Status: unresolved as of 2026-07-10\n")

    def test_consecutive_list_items_are_not_separated_by_blank_lines(self):
        with TempWiki() as w:
            w.page("entities/survivor.md", title="US-Iran War",
                   body="## Overview\n\nA conflict between two states, begun in 2026.\n")
            w.page("entities/loser.md", title="US-Iran War Conflict",
                   body="## Overview\n\nA conflict between two states, begun in 2026.\n"
                        "\n" + self.CONTRADICTIONS)
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            text = (w.wiki / "entities" / "survivor.md").read_text()
            body = text.split("## Contradictions", 1)[1]
            self.assertNotIn("\n\n- **Claim**", body.lstrip(),
                             "a blank line between items makes one list into several")
            self.assertNotIn("stability and a win.\n\n", body,
                             "a Status line must stay attached to its Claim")

    def test_a_status_line_stays_with_the_claim_above_it(self):
        with TempWiki() as w:
            w.page("entities/survivor.md", title="US-Iran War",
                   body="## Overview\n\nA conflict between two states, begun in 2026.\n")
            w.page("entities/loser.md", title="US-Iran War Conflict",
                   body="## Overview\n\nA conflict between two states, begun in 2026.\n"
                        "\n" + self.CONTRADICTIONS)
            agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            text = (w.wiki / "entities" / "survivor.md").read_text()
            for line in text.split("\n"):
                if line.strip().startswith("Status:"):
                    idx = text.split("\n").index(line)
                    prev = text.split("\n")[idx - 1].strip()
                    self.assertTrue(prev.startswith("- **Claim**"),
                                    f"Status orphaned; line above was {prev!r}")

    def test_an_indented_continuation_keeps_its_indentation(self):
        """The third layer of the same bug. Fixing the join was not enough, because the
        carry uses the sentence-filtered text and every sentence had been `.strip()`ed —
        so a `  Status:` line arrived with no leading spaces and stopped being a
        continuation of the `- **Claim**:` above it whatever it was joined with. A line
        with nothing dropped is now carried VERBATIM."""
        with TempWiki() as w:
            w.page("entities/survivor.md", title="US-Iran War",
                   body="## Overview\n\nA conflict between two states, begun in 2026.\n")
            w.page("entities/loser.md", title="US-Iran War Conflict",
                   body="## Overview\n\nA conflict between two states, begun in 2026.\n"
                        "\n" + self.CONTRADICTIONS)
            agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            text = (w.wiki / "entities" / "survivor.md").read_text()
            self.assertIn("\n  Status: unresolved as of 2026-07-10\n", text,
                          "the two-space indent is what makes it a continuation")

    def test_a_partial_carry_keeps_the_lines_own_list_marker(self):
        """When sentences ARE dropped the line has to be rebuilt, and rebuilding it bare
        turns a bullet into a paragraph in the middle of a list."""
        with TempWiki() as w:
            shared = ("The agreement was signed in June 2026 after months of talks.")
            w.page("entities/survivor.md", title="Survivor",
                   body=f"## Overview\n\nA conflict begun in 2026 between two states.\n"
                        f"\n## Terms\n\n- {shared}\n")
            w.page("entities/loser.md", title="Survivor Alt",
                   body=f"## Overview\n\nA conflict begun in 2026 between two states.\n"
                        f"\n## Terms\n\n- {shared} It also set a sixty-day window for "
                        f"commercial vessels to pass through the strait safely.\n")
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            text = (w.wiki / "entities" / "survivor.md").read_text()
            self.assertIn("- It also set a sixty-day window", text,
                          "a carried fragment of a bullet is still a bullet")

    def test_separate_paragraphs_still_get_a_blank_line(self):
        """Two paragraphs run together are one paragraph, so the list fix must not apply
        to prose."""
        with TempWiki() as w:
            w.page("entities/survivor.md", title="US-Iran War",
                   body="## Overview\n\nA conflict between two states, begun in 2026.\n")
            w.page("entities/loser.md", title="US-Iran War Conflict",
                   body="## Overview\n\nA conflict between two states, begun in 2026.\n\n"
                        "## Background\n\nThe first paragraph describes how the long "
                        "sanctions regime preceded the fighting.\n\nThe second paragraph "
                        "describes the naval confrontations that followed it.\n")
            agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            text = (w.wiki / "entities" / "survivor.md").read_text()
            self.assertIn("preceded the fighting.\n\nThe second paragraph", text)


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
