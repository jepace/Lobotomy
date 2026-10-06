"""A summary section is rewritten, never appended to.

Reported by showing a live page's `## Overview` and asking *"does this read like an
Overview to you?"*:

    Florida is a U.S. state located in the southeastern region. In August 2026, housing
    market data showed typical home values at $375,470… In 2026, amid nationwide
    redistricting battles… Within the Democratic Party in the state, the 2026 primary
    season saw… As of October 2026, the state is also battling a significant dengue
    outbreak… In October 2026, state officials announced that Florida would discontinue
    the use of Flock Safety…

Measured: one paragraph, seven sentences, 1,723 characters, 18 links — and only the FIRST
sentence is about Florida. The other six are six different articles, each appended by the
ingest that read it, four of them opening with a date.

**Nothing was malformed, and no guard fired.** Every individual write was a small, legal
addition to a section that permits additions. The existing size guard is the mirror image
of this one — it refuses a section that SHRINKS, because that is an ingest condensing a
page it was meant to add to — and its own comment already names this failure as "the pile
the wiki is not supposed to become". Growing by a sentence a time was never checked.

Two conditions, and both are needed, or the check is either useless or a nuisance:

  * **The old text survives verbatim.** That is the accretion signature. A genuine
    revision rewrites the summary to account for the new material, so the old text does
    not come through unchanged.
  * **The added text opens a sentence with a date.** That is what distinguishes a news
    item from a summary line.

Either alone is ordinary editing, and the check deliberately does not fire on a sentence
that merely CONTAINS a year ("The 2026 primary season saw…"). The whole value of a check
like this is in not crying wolf — the bleeding_titles lesson.

Both write paths go through one helper, because guarding `update_section` alone just moves
the damage to `append_section`.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

OPENER = "Florida is a U.S. state located in the southeastern region."
NEWS = ("In October 2026, state officials announced that Florida would discontinue the "
        "use of Flock Safety automated license plate reader technology.")


class DetectorTest(unittest.TestCase):
    """`_accreted_dated_sentences` — shared by both write paths."""

    def _fires(self, section, old, new):
        return bool(agent._accreted_dated_sentences(section, old, new))

    def test_the_reported_shape_is_caught(self):
        self.assertTrue(self._fires("Overview", OPENER, OPENER + " " + NEWS))

    def test_a_concept_pages_definition_is_the_same_job(self):
        self.assertTrue(self._fires("Definition", OPENER, OPENER + " " + NEWS))

    def test_the_heading_name_is_normalized(self):
        """'overview', 'Overview:' and '## Overview' all name the same section."""
        for spelling in ("overview", "OVERVIEW", "Overview:"):
            with self.subTest(spelling=spelling):
                self.assertTrue(self._fires(spelling, OPENER, OPENER + " " + NEWS))

    def test_an_ordinary_section_is_not_policed(self):
        """A dated sentence in Background is exactly where a dated sentence belongs."""
        self.assertFalse(self._fires("Background", OPENER, OPENER + " " + NEWS))

    def test_a_genuine_rewrite_passes(self):
        """The escape hatch, and the behaviour the rule is asking for: integrate the fact
        and resend the summary. The old text no longer survives verbatim, so nothing
        fires."""
        self.assertFalse(self._fires(
            "Overview", OPENER,
            "Florida is a U.S. state in the southeastern region which ended its Flock "
            "Safety contract in October 2026."))

    def test_an_undated_addition_passes(self):
        """Growing a summary with a standing fact is legitimate."""
        self.assertFalse(self._fires("Overview", OPENER,
                                     OPENER + " It is the third most populous state."))

    def test_a_sentence_merely_containing_a_year_passes(self):
        """Conservative on purpose. 'The 2026 primary season saw…' does not OPEN with a
        date qualifier, and a check that cries wolf gets ignored."""
        self.assertFalse(self._fires(
            "Overview", OPENER,
            OPENER + " The 2026 primary season saw progressive gains."))

    def test_a_brand_new_summary_is_not_accretion(self):
        """There is nothing to have appended to."""
        self.assertFalse(self._fires("Overview", "", OPENER + " " + NEWS))

    def test_an_unchanged_section_does_not_fire(self):
        self.assertFalse(self._fires("Overview", OPENER, OPENER))

    def test_a_shorter_rewrite_does_not_fire(self):
        """That is the other guard's job, and two guards claiming one edit would give the
        model contradictory instructions."""
        self.assertFalse(self._fires("Overview", OPENER + " " + NEWS, OPENER))

    def test_links_on_disk_do_not_defeat_the_comparison(self):
        """The page is autolinked and the agent writes plain text, so a byte comparison
        says 'changed' for a sentence nobody touched. This is the same reason the size
        guard measures unlinked length."""
        linked = "[Florida](../entities/florida.md) is a U.S. state located in the southeastern region."
        self.assertTrue(self._fires("Overview", linked, OPENER + " " + NEWS))

    def test_whitespace_differences_do_not_defeat_it(self):
        self.assertTrue(self._fires("Overview", OPENER,
                                    OPENER.replace(" ", "  ") + "\n\n" + NEWS))

    def test_a_historical_date_is_caught_too(self):
        """Same mechanism, same answer — option 4 of the refusal covers it in one round."""
        self.assertTrue(self._fires(
            "Overview", OPENER, OPENER + " In 1845 Florida was admitted to the Union."))

    def test_every_dated_sentence_is_returned_not_just_the_first(self):
        """The refusal quotes them, and naming one of four understates the problem."""
        added = (" In August 2026, home values fell. In 2026, redistricting battles "
                 "began. As of October 2026, a dengue outbreak is under way.")
        self.assertEqual(
            len(agent._accreted_dated_sentences("Overview", OPENER, OPENER + added)), 3)


class SentenceSplitTest(unittest.TestCase):
    """The splitter has to survive the abbreviations these pages are full of."""

    def test_us_senate_does_not_split(self):
        s = agent._sentences("She was nominated for the U.S. Senate in the primary.")
        self.assertEqual(len(s), 1, s)

    def test_real_boundaries_still_split(self):
        self.assertEqual(len(agent._sentences("One thing. Another thing. A third.")), 3)

    def test_links_are_flattened_before_splitting(self):
        s = agent._sentences("See [Florida](../entities/florida.md) today. Then stop.")
        self.assertEqual(len(s), 2, s)


class UpdateSectionTest(TempWikiTestCase):

    def _page(self, overview=OPENER):
        self.w.page("entities/florida.md", title="Florida", type="entity",
                    body=f"## Overview\n\n{overview}\n\n## Background\n\nSettled early.\n")
        agent.init_session()
        agent._read_file("wiki/entities/florida.md")
        return "wiki/entities/florida.md"

    def _update(self, content, section="Overview"):
        return agent.TOOL_FNS["update_section"](
            {"path": self._page(), "section": section, "content": content})

    def test_bolting_a_news_sentence_on_is_refused(self):
        r = self._update(OPENER + " " + NEWS)
        self.assertTrue(r.startswith("Error:"), r)

    def test_the_refusal_quotes_the_offending_sentence(self):
        """A refusal that names the text it is talking about is one the model can act on."""
        r = self._update(OPENER + " " + NEWS)
        self.assertIn("Flock Safety", r)

    def test_the_refusal_names_every_move_that_works(self):
        """Principle 4. Which one fits depends on something only the model knows, so all
        four are named."""
        r = self._update(OPENER + " " + NEWS)
        for call in ("append_section", "add_timeline_entry", "create_file"):
            self.assertIn(call, r)
        self.assertIn("REWRITTEN", r)

    def test_the_refusal_says_the_text_is_not_wasted(self):
        """A guard that names no destination does not prevent the damage, it redirects it
        somewhere quieter."""
        self.assertIn("not wasted", self._update(OPENER + " " + NEWS))

    def test_the_refusal_names_the_pages_other_sections(self):
        self.assertIn("Background", self._update(OPENER + " " + NEWS))

    def test_nothing_reaches_disk(self):
        self._update(OPENER + " " + NEWS)
        self.assertNotIn("Flock Safety",
                         self.w.disk("entities/florida.md"))

    def test_the_rewrite_the_refusal_asks_for_succeeds(self):
        """Where a refusal names a call, following its instructions must work — that is the
        only version of this assertion that proves anything."""
        r = self._update("Florida is a U.S. state in the southeastern region which ended "
                         "its Flock Safety contract in October 2026.")
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn("Flock Safety", self.w.disk("entities/florida.md"))

    def test_an_ordinary_section_is_unaffected(self):
        r = self._update("Settled early. In 1845 it joined the Union.", section="Background")
        self.assertFalse(r.startswith("Error:"), r)

    def test_an_already_piled_summary_stays_editable(self):
        """Rule 5 is about what the edit ADDS. Refusing every write to a damaged page would
        block the write that repairs it — the deadlock an earlier heading guard caused on
        176 real pages."""
        pile = OPENER + " " + NEWS + " In August 2026, home values fell 1.5%."
        r = agent.TOOL_FNS["update_section"]({
            "path": self._page(overview=pile), "section": "Overview",
            "content": "Florida is a U.S. state in the southeast. Its housing market "
                       "cooled and its surveillance contracts ended during 2026.",
            "allow_shrink": "true"})
        self.assertFalse(r.startswith("Error:"), r)


class AppendSectionTest(TempWikiTestCase):
    """Guarding one write path just moves the damage to the other."""

    def _page(self):
        self.w.page("entities/florida.md", title="Florida", type="entity",
                    body=f"## Overview\n\n{OPENER}\n\n## Background\n\nSettled early.\n")
        agent.init_session()
        return "wiki/entities/florida.md"

    def test_appending_a_news_sentence_to_overview_is_refused(self):
        r = agent.TOOL_FNS["append_section"](
            {"path": self._page(), "section": "Overview", "text": NEWS})
        self.assertTrue(r.startswith("Error:"), r)
        self.assertNotIn("Flock Safety", self.w.disk("entities/florida.md"))

    def test_the_refusal_names_the_moves(self):
        r = agent.TOOL_FNS["append_section"](
            {"path": self._page(), "section": "Overview", "text": NEWS})
        self.assertIn("append_section", r)
        self.assertIn("not wasted", r)

    def test_the_move_it_recommends_succeeds(self):
        """Option 1: give the material its own section."""
        r = agent.TOOL_FNS["append_section"](
            {"path": self._page(), "section": "Surveillance and Privacy", "text": NEWS})
        self.assertFalse(r.startswith("Error:"), r)
        disk = self.w.disk("entities/florida.md")
        self.assertIn("## Surveillance and Privacy", disk)
        self.assertIn("Flock Safety", disk)

    def test_appending_an_undated_fact_to_overview_still_works(self):
        r = agent.TOOL_FNS["append_section"](
            {"path": self._page(), "section": "Overview",
             "text": "It is the third most populous state."})
        self.assertFalse(r.startswith("Error:"), r)

    def test_appending_to_an_ordinary_section_is_unaffected(self):
        r = agent.TOOL_FNS["append_section"](
            {"path": self._page(), "section": "Background", "text": NEWS})
        self.assertFalse(r.startswith("Error:"), r)


class SchemaTest(unittest.TestCase):
    """A guard added in code needs a matching line in LOBOTOMY.md — discovering a rule by
    refusal costs a round, reading it in the schema costs nothing."""

    @classmethod
    def setUpClass(cls):
        cls.src = (Path(__file__).resolve().parent.parent
                   / "LOBOTOMY.md").read_text(encoding="utf-8")

    def test_the_rule_is_stated(self):
        self.assertIn("A summary section is rewritten, never appended to", self.src)

    def test_the_rule_count_was_updated(self):
        """It said "Four of these rules are enforced" and there are now five."""
        self.assertIn("Five of these rules are enforced", self.src)
        self.assertNotIn("Four of these rules are enforced", self.src)

    def test_the_schema_names_the_alternative(self):
        self.assertIn("the answer is a new section, not Overview", self.src)

    def test_the_schema_asks_for_paragraphs(self):
        self.assertIn("more than one paragraph", self.src)

    def test_the_schema_says_the_rule_is_about_what_the_edit_adds(self):
        """Or a model reading it would conclude a damaged page cannot be repaired."""
        self.assertIn("stays editable", self.src)


if __name__ == "__main__":
    unittest.main()
