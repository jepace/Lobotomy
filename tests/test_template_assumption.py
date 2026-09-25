"""A page that outgrew the template must not be edited as though it hadn't.

The model reaches `update_section(path, "Overview")` without reading anything, because
LOBOTOMY.md's entity template guarantees an `## Overview` and guessing that name is right
nearly always. Fine for the name. Not fine as a belief about the page: one that has been
through a few years of ingests grows headings the template never mentions.

The failure was an asymmetry between a guess that misses and one that hits.

  * MISSES — no `## Overview` at all. `_read_section`'s not-found reply already lists
    every section with sizes and previews and says "pick the one this material belongs
    to". Self-correcting, no quality lost.
  * HITS — `## Overview` exists. The refusal handed back that section and nothing else, so
    material that belonged under "Sanctions and the Oil Sector" was merged into Overview
    with nothing on the page to notice. And the hit is the common case, because Overview
    usually does exist, so the quiet failure was the common one.

`append_section` had the same root cause with a worse result: asked for the template's
"Positions" on a page whose equivalent is "Political Stances", it created a second section
for the same subject and reported success. `_heading_dupes` cannot see that, because the
names differ.

Both now name the page's other sections. Reported rather than refused on the
append_section side: creating a section is legitimate, and a refusal there would have no
escape hatch — no "yes, really" argument exists, so the model would loop or give up.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

# A page that has been through some shit: no template headings left except by accident.
EVOLVED = ("# Venezuela\n\n## Overview\n\nA South American state.\n\n"
           "## Sanctions and the Oil Sector\n\nUS sanctions since 2019.\n\n"
           "## Migration Crisis\n\nSeven million have left.\n\n"
           "## Political Stances\n\nNon-aligned.\n")


class UpdateSectionSeesTheWholePageTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.w.page("entities/venezuela.md", title="Venezuela", type="entity", body=EVOLVED)
        agent.init_session()

    def _guess_overview(self):
        return agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/venezuela.md", "section": "Overview",
            "content": "New oil sanctions were imposed.\n"})

    def test_the_refusal_names_the_pages_other_sections(self):
        r = self._guess_overview()
        for name in ("Sanctions and the Oil Sector", "Migration Crisis", "Political Stances"):
            self.assertIn(name, r, f"{name!r} was not offered as an alternative home")

    def test_it_does_not_list_the_section_being_written(self):
        self.assertNotIn("other sections: Overview", self._guess_overview())

    def test_it_still_hands_back_the_section_body(self):
        # The list is an addition, not a replacement — the whole point of the refusal is
        # that the retry needs no extra read.
        r = self._guess_overview()
        self.assertIn("A South American state.", r)
        self.assertIn("do NOT call read_section first", r)

    def test_the_named_alternative_is_a_call_that_works(self):
        # Principle 4: follow the refusal's advice and the edit goes through.
        self._guess_overview()
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/venezuela.md", "section": "Sanctions and the Oil Sector",
            "content": "US sanctions since 2019, tightened again in 2026.\n"})
        # That section has not been read either, so it refuses once and hands it over.
        self.assertIn("US sanctions since 2019", r)
        r2 = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/venezuela.md", "section": "Sanctions and the Oil Sector",
            "content": "US sanctions since 2019, tightened again in 2026.\n"})
        self.assertFalse(r2.startswith("Error:"), r2)
        self.assertIn("tightened again in 2026", self.w.disk("entities/venezuela.md"))

    def test_a_page_with_only_that_section_says_nothing_extra(self):
        self.w.page("entities/small.md", title="Small Corp", type="entity",
                    body="# Small Corp\n\n## Overview\n\nA firm.\n")
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/small.md", "section": "Overview", "content": "A firm.\n"})
        self.assertNotIn("other sections", r)

    def test_a_guess_that_misses_still_gets_the_full_list(self):
        # The half that already worked. Kept here so the two stay symmetrical.
        self.w.page("entities/v2.md", title="V2", type="entity",
                    body=EVOLVED.replace("## Overview", "## Country Profile"))
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/v2.md", "section": "Overview", "content": "X.\n"})
        self.assertIn("has no section 'Overview'", r)
        self.assertIn("Country Profile", r)
        self.assertIn("Sanctions and the Oil Sector", r)


class AppendSectionReportsWhatItCreatedBesideTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.w.page("entities/venezuela.md", title="Venezuela", type="entity", body=EVOLVED)
        agent.init_session()

    def _append_template_name(self):
        # "Positions" is the template's name; this page calls it "Political Stances".
        return agent.TOOL_FNS["append_section"]({
            "path": "wiki/entities/venezuela.md", "section": "Positions",
            "text": "- Opposes US sanctions.\n"})

    def test_creating_a_section_still_succeeds(self):
        r = self._append_template_name()
        self.assertFalse(r.startswith("Error:"), r)
        self.assertIn("## Positions", self.w.disk("entities/venezuela.md"))

    def test_it_reports_the_sections_that_were_already_there(self):
        r = self._append_template_name()
        self.assertIn("Political Stances", r,
                      "the near-duplicate it was created beside went unmentioned")
        self.assertIn("duplicate", r)

    def test_appending_to_an_existing_section_says_nothing_extra(self):
        r = agent.TOOL_FNS["append_section"]({
            "path": "wiki/entities/venezuela.md", "section": "Political Stances",
            "text": "- Opposes US sanctions.\n"})
        self.assertNotIn("already had these sections", r)

    def test_a_first_section_on_an_empty_page_says_nothing_extra(self):
        self.w.page("entities/bare.md", title="Bare Corp", type="entity",
                    body="# Bare Corp\n\nA firm with no sections yet.\n")
        r = agent.TOOL_FNS["append_section"]({
            "path": "wiki/entities/bare.md", "section": "Overview", "text": "A firm.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        self.assertNotIn("already had these sections", r)


if __name__ == "__main__":
    unittest.main()
