"""A page's Timeline is one list, in one format, with each event stated once.

The observed failure, on a page created after add_timeline_entry shipped: the Timeline
carried eight bullets for four events. `create_file` writes a whole page in one call, so
the model wrote the timeline by hand — in a shape the tool did not recognise (`- 2026-08:
...` rather than `- **2026-08** — ...`). The tool filed those as prose, kept them above
its own list, and then wrote every one of the same facts again underneath.

Two separate holes, and both had to be closed:

  - The bullet parser only accepted the exact shape the renderer emits. Recognising a
    bullet and writing one are different jobs; reading has to be generous or an
    unrecognised entry goes not just unsorted but undeduplicated.
  - Duplicates were compared as exact strings, so two sources wording one event
    differently produced two entries. The page had the same 2026-09-12 death three times.

LOBOTOMY.md does say the Timeline is never maintained by hand. A rule the model has no
way to obey at create_file time is not worth spending a refusal on, so the hand-written
form is absorbed instead — on every write path, and in heal_pages for pages that already
carry the damage.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class LooseBulletParsingTest(unittest.TestCase):
    """Every shape a hand-written timeline actually arrives in."""

    def _parse(self, line):
        _prose, entries = agent._parse_timeline(line)
        return entries[0][:2] if entries else None

    def test_the_shapes_seen_in_the_wild_all_parse(self):
        for line in (
            "- 2026-08: Two infants die.",
            "- **2026-08** — Two infants die.",
            "- **2026-08**: Two infants die.",
            "- 2026-08 — Two infants die.",
            "- 2026-08 - Two infants die.",
            "- 2026-08 – Two infants die.",
            "* 2026-08: Two infants die.",
            "- __2026-08__ — Two infants die.",
        ):
            self.assertEqual(self._parse(line), ("2026-08", "Two infants die."), line)

    def test_a_full_date_is_not_split_on_its_own_hyphens(self):
        self.assertEqual(self._parse("- 2026-09-12 - A woman dies."),
                         ("2026-09-12", "A woman dies."))

    def test_a_bullet_with_no_text_stays_prose(self):
        prose, entries = agent._parse_timeline("- **2026-09-12** —")
        self.assertEqual(entries, [])
        self.assertEqual(len(prose), 1, "a line the parser rejected was also dropped")

    def test_prose_is_kept_not_discarded(self):
        prose, entries = agent._parse_timeline(
            "Events as reported by state health officials.\n- 2026-08: A thing.\n")
        self.assertEqual(prose, ["Events as reported by state health officials."])
        self.assertEqual(len(entries), 1)

    def test_an_undated_bullet_is_not_forced_into_an_entry(self):
        _prose, entries = agent._parse_timeline("- Officials confirmed the count.")
        self.assertEqual(entries, [])


class RestatementTest(unittest.TestCase):
    """Same date, and one entry's meaningful words all present in the other."""

    def _dedupe(self, *texts):
        e = [("2026-09-12", t, i) for i, t in enumerate(texts)]
        return [t for _d, t, _s in agent._tl_dedupe(e)]

    def test_the_observed_triple_collapses_to_the_fullest_wording(self):
        long = ("A 40-year-old Jefferson County woman dies from measles-related "
                "complications, marking the state's third death related to the outbreak.")
        kept = self._dedupe(
            "A 40-year-old woman from Jefferson County dies of measles complications.",
            "A 40-year-old woman from Jefferson County dies of complications related to "
            "the measles outbreak.",
            long)
        self.assertEqual(kept, [long])

    def test_the_longer_wording_wins_regardless_of_order(self):
        long = "A woman dies of measles complications in Jefferson County."
        self.assertEqual(self._dedupe(long, "A woman dies."), [long])
        self.assertEqual(self._dedupe("A woman dies.", long), [long])

    def test_two_events_on_one_day_both_survive(self):
        kept = self._dedupe("A woman dies in Jefferson County.",
                            "The governor declares an emergency.")
        self.assertEqual(len(kept), 2, "a distinct same-day event was swallowed")

    def test_a_different_date_is_never_a_restatement(self):
        e = [("2026-09-12", "A woman dies.", 0), ("2026-09-13", "A woman dies.", 1)]
        self.assertEqual(len(agent._tl_dedupe(e)), 2)

    def test_links_do_not_defeat_the_comparison(self):
        # The autolinker will have rewritten whatever is already on the page.
        long = "A woman dies in [Jefferson County](../entities/jefferson-county.md) today."
        self.assertEqual(self._dedupe(long, "A woman dies in Jefferson County."), [long])

    def test_an_entry_of_only_stopwords_is_not_absorbed_into_everything(self):
        kept = self._dedupe("It was the one.", "A woman dies in Jefferson County.")
        self.assertEqual(len(kept), 2, "an entry with no content words matched anything")


class NormalizeTimelineTest(unittest.TestCase):

    def test_the_observed_page_collapses_to_one_bullet_per_event(self):
        body = (
            "# Pennsylvania Measles Outbreak\n\n## Timeline\n\n"
            "- 2026-08: Two infants die in Lancaster County.\n"
            "- 2026-09-12: A 40-year-old woman from Jefferson County dies of measles "
            "complications.\n"
            "- 2026-09-15: Mifflin County coroner reports the death of an 18-year-old.\n\n"
            "- **2026-08** — Two infants die in Lancaster County.\n"
            "- **2026-09-12** — A 40-year-old woman from Jefferson County dies of "
            "complications related to the measles outbreak.\n"
            "- **2026-09-12** — A 40-year-old Jefferson County woman dies from "
            "measles-related complications, marking the state's third death related to "
            "the outbreak.\n"
            "- **2026-09-15** — The Mifflin County coroner reports the death of an "
            "18-year-old resident, a neurological complication.\n\n"
            "## Background\n\nText.\n")
        out = agent.normalize_timeline(body)
        bullets = [l for l in out.splitlines() if l.startswith("- ")]
        self.assertEqual(len(bullets), 3, "\n".join(bullets))
        self.assertTrue(all(l.startswith("- **") for l in bullets), bullets)
        self.assertEqual([l.split("**")[1] for l in bullets],
                         ["2026-08", "2026-09-12", "2026-09-15"])
        self.assertIn("## Background\n\nText.\n", out, "the next section was damaged")

    def test_it_sorts_a_timeline_written_out_of_order(self):
        out = agent.normalize_timeline(
            "## Timeline\n\n- 2026-09-15: Later.\n- 2026-08: Earlier.\n")
        self.assertLess(out.index("Earlier."), out.index("Later."))

    def test_a_page_with_no_timeline_is_returned_untouched(self):
        body = "# A Corp\n\n## Overview\n\nX.\n"
        self.assertIs(agent.normalize_timeline(body), body)

    def test_an_already_canonical_timeline_is_a_no_op(self):
        body = ("---\ntitle: X\n---\n\n# X\n\n## Timeline\n\n"
                "- **2026-08** — A thing.\n- **2026-09** — Another.\n\n## Sources\n\n- a\n")
        self.assertEqual(agent.normalize_timeline(body), body,
                         "normalizing twice would rewrite the page on every single write")

    def test_frontmatter_survives(self):
        body = ("---\ntitle: X\ntype: entity\n---\n\n# X\n\n## Timeline\n\n"
                "- 2026-08: A thing.\n")
        out = agent.normalize_timeline(body)
        self.assertTrue(out.startswith("---\ntitle: X\ntype: entity\n---\n"), out[:60])

    def test_a_timeline_of_pure_prose_is_left_alone(self):
        body = "## Timeline\n\nNothing dated has been recorded yet.\n"
        self.assertIs(agent.normalize_timeline(body), body)


class WritePathTest(TempWikiTestCase):
    """Every path that writes a page normalizes it — a hand-written timeline cannot
    survive the call that created it."""

    _MIXED = ("## Overview\n\nAn outbreak.\n\n## Timeline\n\n"
              "- 2026-08: Two infants die in Lancaster County.\n"
              "- 2026-09-12: A woman dies in Jefferson County.\n")

    def test_create_file_canonicalizes_a_hand_written_timeline(self):
        agent.init_session()
        r = agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/pa-outbreak.md", "title": "PA Outbreak",
            "type": "entity", "body": self._MIXED})
        self.assertFalse(r.startswith("Error:"), r)
        disk = self.w.disk("entities/pa-outbreak.md")
        self.assertIn("- **2026-08** — Two infants die", disk)
        self.assertNotIn("- 2026-08:", disk)

    def test_the_tool_then_finds_its_own_facts_already_there(self):
        # The whole point: before this, create_file's bullets were invisible to
        # add_timeline_entry, so the first ingest wrote all of them again.
        agent.init_session()
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/pa-outbreak.md", "title": "PA Outbreak",
            "type": "entity", "body": self._MIXED})
        r = agent.TOOL_FNS["add_timeline_entry"]({
            "path": "wiki/entities/pa-outbreak.md", "date": "2026-08",
            "text": "Two infants die in Lancaster County."})
        self.assertIn("already on", r)
        self.assertEqual(self.w.disk("entities/pa-outbreak.md").count("Two infants die"), 1)

    def test_a_fuller_restatement_replaces_the_shorter_entry(self):
        agent.init_session()
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/pa-outbreak.md", "title": "PA Outbreak",
            "type": "entity", "body": self._MIXED})
        r = agent.TOOL_FNS["add_timeline_entry"]({
            "path": "wiki/entities/pa-outbreak.md", "date": "2026-09-12",
            "text": "A 40-year-old woman dies in Jefferson County of measles."})
        self.assertFalse(r.startswith("Error:"), r)
        disk = self.w.disk("entities/pa-outbreak.md")
        self.assertIn("40-year-old", disk)
        self.assertEqual(disk.count("2026-09-12"), 1, disk)
        self.assertIn("position 2 of 2", r, "the reported position assumed it was appended")

    def test_a_genuinely_new_entry_is_still_added(self):
        agent.init_session()
        agent.TOOL_FNS["create_file"]({
            "path": "wiki/entities/pa-outbreak.md", "title": "PA Outbreak",
            "type": "entity", "body": self._MIXED})
        agent.TOOL_FNS["add_timeline_entry"]({
            "path": "wiki/entities/pa-outbreak.md", "date": "2026-09-20",
            "text": "The governor declares a state of emergency."})
        disk = self.w.disk("entities/pa-outbreak.md")
        self.assertEqual(len([l for l in disk.splitlines() if l.startswith("- **")]), 3, disk)

    def test_update_file_canonicalizes_too(self):
        agent.init_session()
        self.w.page("entities/pa.md", title="PA", type="entity",
                    body="# PA\n\n## Overview\n\nX.\n")
        agent.TOOL_FNS["read_file"]({"path": "wiki/entities/pa.md"})
        r = agent.TOOL_FNS["update_file"]({
            "path": "wiki/entities/pa.md",
            "content": "# PA\n\n## Overview\n\nX.\n\n## Timeline\n\n- 2026-08: A thing.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn("- **2026-08** — A thing.", self.w.disk("entities/pa.md"))

    def test_append_section_canonicalizes_too(self):
        agent.init_session()
        self.w.page("entities/pa.md", title="PA", type="entity",
                    body="# PA\n\n## Overview\n\nX.\n")
        r = agent.TOOL_FNS["append_section"]({
            "path": "wiki/entities/pa.md", "section": "Timeline",
            "text": "- 2026-08: A thing.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn("- **2026-08** — A thing.", self.w.disk("entities/pa.md"))

    def test_heal_pages_repairs_a_page_that_already_carries_the_damage(self):
        self.w.page("entities/old.md", title="Old Outbreak", type="entity",
                    body="# Old Outbreak\n\n## Timeline\n\n"
                         "- 2026-08: Two infants die in Lancaster County.\n\n"
                         "- **2026-08** — Two infants die in Lancaster County.\n")
        agent.heal_pages()
        disk = self.w.disk("entities/old.md")
        self.assertEqual(disk.count("Two infants die"), 1, disk)

    def test_heal_pages_leaves_a_clean_page_alone(self):
        # heal_pages runs at startup and after every ingest; a normalizer that rewrote on
        # every pass would fill page history with empty revisions forever.
        self.w.page("entities/ok.md", title="OK", type="entity",
                    body="# OK\n\n## Timeline\n\n- **2026-08** — A thing.\n")
        before = self.w.disk("entities/ok.md")
        agent.heal_pages()
        self.assertEqual(self.w.disk("entities/ok.md"), before)


class EchoedBulletTest(TempWikiTestCase):
    """A model handed a bullet format tends to send the whole bullet as `text`."""

    def _page(self):
        self.w.page("entities/e.md", title="E", type="entity",
                    body="# E\n\n## Overview\n\nX.\n")

    def test_an_echoed_bullet_does_not_render_the_date_twice(self):
        agent.init_session()
        self._page()
        agent.TOOL_FNS["add_timeline_entry"]({
            "path": "wiki/entities/e.md", "date": "2026-08",
            "text": "- **2026-08** — A thing happened."})
        self.assertIn("- **2026-08** — A thing happened.", self.w.disk("entities/e.md"))

    def test_text_that_merely_opens_with_a_year_is_not_beheaded(self):
        # The parser is loose on purpose, so the echo strip has to check the date matches
        # the one supplied — otherwise this entry loses its first half.
        agent.init_session()
        self._page()
        agent.TOOL_FNS["add_timeline_entry"]({
            "path": "wiki/entities/e.md", "date": "2026-08",
            "text": "1999 - 2001 saw a comparable decline in coverage."})
        self.assertIn("1999 - 2001 saw a comparable decline", self.w.disk("entities/e.md"))


if __name__ == "__main__":
    unittest.main()
