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


class RepeatedRefusalTest(TempWikiTestCase):
    """What the first live ingest did, from the production log.

    The agent met this refusal on pete-ricketts.md and **resent the identical call twice
    more** — three refusals for one sentence, at ~60s of pacing each. Four named moves
    were not enough on their own: nothing in the reply CHANGED between attempts, and a
    model reading a refusal as "that didn't go through" tries again.

    The second and later refusals for the same section say outright that this exact call
    has already been refused, and drop the menu for the one move that always works.
    """

    OV = ("Pete Ricketts is a Republican United States Senator from Nebraska, appointed "
          "in 2023 and previously Governor of the state.")
    NEWS = (" In the 2026 election, he is facing a competitive challenge from "
            "independent candidate Dan Osborn.")

    def setUp(self):
        super().setUp()
        self.w.page("entities/pete-ricketts.md", title="Pete Ricketts", type="entity",
                    body=f"## Overview\n\n{self.OV}\n\n## Legislative Focus\n\nData.\n")
        agent.init_session()
        agent._read_file("wiki/entities/pete-ricketts.md")
        self.p = "wiki/entities/pete-ricketts.md"

    def _resend(self):
        return agent.TOOL_FNS["update_section"](
            {"path": self.p, "section": "Overview", "content": self.OV + self.NEWS})

    def test_the_first_refusal_already_says_not_to_resend(self):
        self.assertIn("Do NOT resend this call", self._resend())

    def test_the_second_refusal_is_different_from_the_first(self):
        first = self._resend()
        second = self._resend()
        self.assertNotEqual(first, second,
                            "an identical reply gives the model no reason to change")

    def test_the_second_refusal_says_it_has_already_been_refused(self):
        self._resend()
        self.assertIn("refused AGAIN", self._resend())

    def test_the_second_refusal_counts_the_attempts(self):
        self._resend()
        self.assertIn("refusal 2", self._resend())
        self.assertIn("refusal 3", self._resend())

    def test_the_second_refusal_gives_one_move_not_a_menu(self):
        """A menu is what it already failed to act on. The escalation names the single
        call that always succeeds."""
        self._resend()
        second = self._resend()
        self.assertIn("append_section(path=", second)
        for gone in ("add_timeline_entry", "create_file"):
            self.assertNotIn(gone, second)

    def test_the_escalation_names_the_real_path(self):
        self._resend()
        self.assertIn(f"path='{self.p}'", self._resend())

    def test_the_count_is_per_section_not_per_session(self):
        """Another section on the same page is a fresh problem, not a repeat."""
        self.w.page("entities/other.md", title="Other", type="entity",
                    body=f"## Overview\n\n{self.OV}\n")
        agent._read_file("wiki/entities/other.md")
        self._resend()
        self._resend()
        other = agent.TOOL_FNS["update_section"](
            {"path": "wiki/entities/other.md", "section": "Overview",
             "content": self.OV + self.NEWS})
        self.assertNotIn("refused AGAIN", other)

    def test_a_new_session_starts_the_count_again(self):
        self._resend()
        self._resend()
        agent.init_session()
        agent._read_file("wiki/entities/pete-ricketts.md")
        self.assertNotIn("refused AGAIN", self._resend())

    def test_the_move_it_names_succeeds(self):
        """Where a refusal names a call, following it must work."""
        self._resend()
        self._resend()
        r = agent.TOOL_FNS["append_section"](
            {"path": self.p, "section": "Senate Campaign",
             "text": "He faces Dan Osborn in the 2026 election."})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn("Dan Osborn", self.w.disk("entities/pete-ricketts.md"))


class DatedHeadingTrapTest(TempWikiTestCase):
    """The refusal must not steer the model into a different guard.

    From the same log: told to name a section for the subject, the agent chose
    '2026 Senate Campaign' — refused by the date-in-heading rule — and spent another
    round. That is principle 4's documented worst case, renaming one violation into
    another, and here it is near-certain rather than unlucky: the material is dated by
    definition, so the obvious name carries its year.
    """

    OV = "Pete Ricketts is a Republican Senator from Nebraska."
    NEWS = " In the 2026 election, he faces independent candidate Dan Osborn."

    def setUp(self):
        super().setUp()
        self.w.page("entities/pete-ricketts.md", title="Pete Ricketts", type="entity",
                    body=f"## Overview\n\n{self.OV}\n")
        agent.init_session()
        agent._read_file("wiki/entities/pete-ricketts.md")
        self.p = "wiki/entities/pete-ricketts.md"

    def _refusal(self):
        return agent.TOOL_FNS["update_section"](
            {"path": self.p, "section": "Overview", "content": self.OV + self.NEWS})

    def test_the_dated_heading_really_is_refused(self):
        """The trap is real: this is what the agent actually tried."""
        r = agent.TOOL_FNS["append_section"](
            {"path": self.p, "section": "2026 Senate Campaign", "text": "Osborn runs."})
        self.assertTrue(r.startswith("Error:"), r)

    def test_the_first_refusal_warns_about_it(self):
        r = self._refusal()
        self.assertIn("NO year", r)
        self.assertIn("2026 Senate Campaign", r)

    def test_the_escalated_refusal_warns_about_it_too(self):
        self._refusal()
        r = self._refusal()
        self.assertIn("no year and no date", r)

    def test_the_undated_name_it_suggests_is_accepted(self):
        self._refusal()
        r = agent.TOOL_FNS["append_section"](
            {"path": self.p, "section": "Senate Campaign", "text": "Osborn runs."})
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

    def test_the_event_itself_is_named_as_an_entity(self):
        """The gap behind the thin city sections: the list named the participants and not
        the story, so the story had nowhere to live."""
        self.assertIn("The EVENT is an entity", self.src)

    def test_the_schema_does_not_limit_events_to_slow_burning_ones(self):
        """Every example used to be a slow process — outbreak, election, trial,
        investigation — so a single remark read as not qualifying."""
        self.assertIn("A single incident", self.src)
        self.assertRegex(self.src, r"a remark, a raid, a resignation")

    def test_the_schema_says_a_page_title_may_carry_a_date(self):
        """A model that has just been refused for a dated HEADING will otherwise
        over-generalize and decline to name the event at all."""
        self.assertIn("a page title may carry\n  a date", self.src)

    def test_the_schema_gives_the_linking_reason(self):
        """Editorial taste is arguable; this is mechanical. Only a page title is matched
        by the autolinker, so only a page is found everywhere it is mentioned."""
        self.assertIn("the only thing this wiki can link", self.src)


class EventDeservesAPageTest(TempWikiTestCase):
    """A president saying on the record that enemies should destroy two American cities
    produced a 224-character paragraph on los-angeles.md, a 222-character paragraph on
    san-diego.md, and no page for the remark itself.

    The guard was right that it did not belong in Overview. Option 3 was the right move and
    the model did not take it, because every example the refusal and the schema gave was a
    slow-burning event — an outbreak, an election, a trial — so a single day's remark read
    as not qualifying.
    """

    def _refusal(self):
        self.w.page("entities/los-angeles.md", title="Los Angeles", type="entity",
                    body="## Overview\n\nLos Angeles is a city in California.\n")
        agent.init_session()
        agent._read_file("wiki/entities/los-angeles.md")
        return agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/los-angeles.md", "section": "Overview",
            "content": ("Los Angeles is a city in California. On October 5, 2026, "
                        "President Trump said enemies should be allowed to destroy it.")})

    def test_option_three_is_not_limited_to_slow_burning_events(self):
        r = self._refusal()
        self.assertIn("Not only slow-burning events", r)
        self.assertIn("a remark", r)

    def test_option_three_names_the_participant_test(self):
        """The discriminator the model needed: this page is a participant in what
        happened, not the subject of it."""
        self.assertIn("PARTICIPANT", self._refusal())

    def test_option_three_gives_the_linking_reason(self):
        self.assertIn("autolinker", self._refusal())

    def test_a_page_for_the_event_is_linked_from_a_later_unrelated_page(self):
        """What a page buys that a section cannot: every future mention, anywhere, linked
        without anyone deciding to. This is the whole argument for giving the event a page
        rather than a paragraph on each participant."""
        self.w.page("entities/let-em-take-out-los-angeles-remarks.md",
                    title="Let Em Take Out Los Angeles Remarks", type="entity",
                    body="## Overview\n\nRemarks made at an October 2026 rally.\n")
        p = self.w.wiki / "entities" / "unrelated.md"
        p.write_text("---\ntitle: Unrelated\ntype: entity\n---\n\n# Unrelated\n\n"
                     "## Overview\n\nCritics cited the Let Em Take Out Los Angeles "
                     "Remarks months afterwards.\n", encoding="utf-8")
        agent._autolink_now(p)
        self.assertIn("[Let Em Take Out Los Angeles Remarks]"
                      "(../entities/let-em-take-out-los-angeles-remarks.md)",
                      p.read_text(encoding="utf-8"))

    def test_a_dated_page_title_is_accepted(self):
        """The heading rule refuses '## 2026 Outbreak'. A model that over-generalizes from
        it will not name the event at all, so the page-title case has to work."""
        agent.init_session()
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/2026-irkutsk-plague-outbreak.md",
            "title": "2026 Irkutsk Plague Outbreak", "type": "entity",
            "body": "## Overview\n\nAn outbreak in Siberia.\n"})
        self.assertFalse(r.startswith("Error:"), r)


if __name__ == "__main__":
    unittest.main()
