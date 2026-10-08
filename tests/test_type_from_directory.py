"""The directory decides a page's `type:`, and `Timeline` is on-template everywhere.

Asked for after working out what the entity/concept split actually costs: *"at this point,
I can't trust that the distinction between the 2 types has been honored, so let's do the
best we can."* Three changes, and the reasoning behind all three is the same — **stop
depending on a field the wiki is known to have got wrong, where a better signal exists.**

The evidence it is wrong: the live wiki has `concepts/tim-wu.md` and
`concepts/jesse-watters.md` (people), `concepts/environmental-protection-agency.md` (an
organisation), and `concepts/ebola.md` (a proper noun). And `rename_page.py` moved pages
between directories for months **without carrying `type:` along**, so a page could be
correctly filed and still claim the other type.

**The directory is the ground truth and the field is a claim about it.** Every link to a
page encodes the directory, `_resolve_page` searches by directory, and the page's own
relative links are computed from it. The field is read by `_OPENER`,
`section_inventory.py` and `bleeding_titles.py`, and none of them can move a file. So
where the two disagree, the one that cannot be wrong without the page being unreachable
wins — and the repair has exactly one answer, which is why `heal_pages` absorbs it rather
than reporting it (principle 1).

**What is deliberately NOT healed is the opener.** Renaming `## Definition` to
`## Overview` is a content edit, `promote_openers.py` is the tool for it, and doing it
inside a startup sweep over 13,000 pages would bury a real change in a metadata pass. The
pages needing it are counted and listed instead.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


def _type_of(w, rel):
    line = next((l for l in (w.wiki / rel).read_text().split("\n")
                 if l.startswith("type:")), "")
    return line.split(":", 1)[1].strip() if line else ""


class HealTypeFromDirectoryTest(unittest.TestCase):
    def test_a_concept_in_entities_is_healed_to_entity(self):
        with TempWiki() as w:
            w.page("entities/tim-wu.md", title="Tim Wu", type="concept",
                   body="## Overview\n\nA legal scholar.\n")
            r = agent.heal_pages()
            self.assertEqual(_type_of(w, "entities/tim-wu.md"), "entity")
            self.assertEqual(r["types_healed"], 1)

    def test_an_entity_in_concepts_is_healed_to_concept(self):
        with TempWiki() as w:
            w.page("concepts/ebola.md", title="Ebola", type="entity",
                   body="## Definition\n\nA viral haemorrhagic fever.\n")
            agent.heal_pages()
            self.assertEqual(_type_of(w, "concepts/ebola.md"), "concept")

    def test_every_directory_has_an_implied_type(self):
        with TempWiki() as w:
            w.page("entities/a.md", title="A", type="concept", body="## Overview\n\nx\n")
            w.page("concepts/b.md", title="B", type="entity", body="## Definition\n\nx\n")
            w.page("synthesis/c.md", title="C", type="entity",
                   body="## Question / Thesis\n\nx\n")
            w.page("sources/d-2026-x.md", title="D", type="entity",
                   body="## Summary\n\nx\n")
            agent.heal_pages()
            self.assertEqual(_type_of(w, "entities/a.md"), "entity")
            self.assertEqual(_type_of(w, "concepts/b.md"), "concept")
            self.assertEqual(_type_of(w, "synthesis/c.md"), "synthesis")
            self.assertEqual(_type_of(w, "sources/d-2026-x.md"), "source")

    def test_a_page_whose_type_already_agrees_is_not_rewritten(self):
        """`heal_pages` runs at startup and after every ingest, so a pass that rewrote
        every page would fill the history store with empty revisions forever — the same
        idempotence requirement `normalize_timeline` has."""
        with TempWiki() as w:
            # The H1 is in the fixture because `ensure_h1` would otherwise add it and
            # this test would be measuring that heal instead of this one.
            p = w.page("entities/ok.md", title="OK", type="entity",
                       body="# OK\n\n## Overview\n\nFine.\n")
            before = p.read_text()
            r = agent.heal_pages()
            self.assertEqual(r["types_healed"], 0)
            self.assertEqual(p.read_text(), before)

    def test_healing_is_idempotent(self):
        with TempWiki() as w:
            w.page("concepts/ebola.md", title="Ebola", type="entity",
                   body="## Definition\n\nA fever.\n")
            agent.heal_pages()
            after_first = (w.wiki / "concepts" / "ebola.md").read_text()
            r2 = agent.heal_pages()
            self.assertEqual(r2["types_healed"], 0)
            self.assertEqual((w.wiki / "concepts" / "ebola.md").read_text(), after_first)

    def test_a_corrupt_type_is_still_repaired_first(self):
        """The pre-existing repair for `type: concept}EX_HEAT_CP` has to keep working and
        run FIRST — the leading identifier is recoverable without guessing, and only then
        is it worth asking whether it agrees with the directory.

        This caught the directory check being an `elif` on that repair, so a page needing
        both fixes healed in two passes. `heal_pages` is idempotent and would have got
        there on the next startup, but a sweep that half-finishes is one whose output you
        cannot read."""
        with TempWiki() as w:
            p = w.page("concepts/x.md", title="X", type="entity",
                       body="## Definition\n\nx\n")
            p.write_text(p.read_text().replace("type: entity",
                                               "type: entity}EX_HEAT_CP"))
            agent.heal_pages()
            self.assertEqual(_type_of(w, "concepts/x.md"), "concept")

    def test_a_page_with_no_type_is_reported_not_guessed(self):
        """A missing type is already reported as not-auto-fillable. The directory could
        supply one — and deliberately does not here, because the existing branch returns
        early for a page missing `title` or `type` and changing that is a different
        decision from reconciling two values that both exist."""
        with TempWiki() as w:
            p = w.page("concepts/x.md", title="X", type="entity",
                       body="## Definition\n\nx\n")
            p.write_text(p.read_text().replace("type: entity\n", ""))
            r = agent.heal_pages()
            self.assertTrue(any("missing" in m and "type" in m for m in r["manual"]),
                            r["manual"])


class StaleOpenerIsReportedNotRewrittenTest(unittest.TestCase):
    def test_a_healed_page_carrying_the_other_openers_name_is_listed(self):
        with TempWiki() as w:
            w.page("entities/ebola.md", title="Ebola", type="concept",
                   body="## Definition\n\nA viral haemorrhagic fever.\n")
            r = agent.heal_pages()
            self.assertIn("entities/ebola.md", r["stale_openers"])

    def test_the_heading_itself_is_untouched(self):
        """A metadata sweep that quietly rewrote headings would make `heal_pages` a thing
        nobody could run without reading a diff."""
        with TempWiki() as w:
            w.page("entities/ebola.md", title="Ebola", type="concept",
                   body="## Definition\n\nA viral haemorrhagic fever.\n")
            agent.heal_pages()
            text = (w.wiki / "entities" / "ebola.md").read_text()
            self.assertIn("## Definition", text)
            self.assertNotIn("## Overview", text)

    def test_a_page_with_the_right_opener_is_not_listed(self):
        with TempWiki() as w:
            w.page("entities/ebola.md", title="Ebola", type="concept",
                   body="## Overview\n\nA viral haemorrhagic fever.\n")
            r = agent.heal_pages()
            self.assertEqual(r["types_healed"], 1)
            self.assertEqual(r["stale_openers"], [])


class TimelineIsOnTemplateEverywhereTest(unittest.TestCase):
    """`Timeline` was on-template for `entity` only, and three things already disagreed
    with that: `add_timeline_entry` has never checked a page's type, the stub-event smell
    recognises an event page by the SECTION rather than the type, and the split the
    restriction rests on is the one that cannot be trusted. A disease page under
    `concepts/` that tracks outbreaks was reporting as drift for carrying exactly the
    section the tool maintaining it writes."""

    def test_timeline_is_on_template_for_every_page_type(self):
        import section_inventory
        for t in ("entity", "concept", "synthesis", "source"):
            with self.subTest(type=t):
                self.assertIn("Timeline", section_inventory.TEMPLATE[t])

    def test_each_types_own_headings_are_still_on_template(self):
        """Adding one heading everywhere must not have replaced the per-type lists."""
        import section_inventory
        self.assertIn("Overview", section_inventory.TEMPLATE["entity"])
        self.assertIn("Definition", section_inventory.TEMPLATE["concept"])
        self.assertIn("Question / Thesis", section_inventory.TEMPLATE["synthesis"])
        self.assertIn("Claims", section_inventory.TEMPLATE["source"])

    def test_a_concept_pages_timeline_is_not_reported_as_off_template(self):
        import re as _re
        import section_inventory
        allowed = {agent._norm_heading(h)
                   for h in section_inventory.TEMPLATE["concept"]}
        self.assertIn(agent._norm_heading("Timeline"), allowed)

    def test_add_timeline_entry_works_on_a_concept_page(self):
        """The behaviour the template now agrees with. It always worked; nothing about the
        tool changed, which is why the restriction was reporting a lie."""
        with TempWiki() as w:
            w.page("concepts/ebola.md", title="Ebola", type="concept",
                   body="## Definition\n\nA fever.\n\n## Timeline\n\n"
                        "- **2026-01-01** - First case reported.\n")
            agent.init_session()
            out = agent._add_timeline_entry({"path": "wiki/concepts/ebola.md",
                                             "date": "2026-02-01",
                                             "text": "A second cluster is confirmed."})
            self.assertNotIn("Error", out, out)
            self.assertIn("second cluster",
                          (w.wiki / "concepts" / "ebola.md").read_text())


if __name__ == "__main__":
    unittest.main()
