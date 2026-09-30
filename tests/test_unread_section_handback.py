"""A short page is handed back whole; a long one is handed back a section.

The unread-section refusal used to return the guessed section plus the NAMES of the other
sections, whatever the page's size. That made it stingier than the tool it stands in for:
under `_WIKI_READ_LIMIT` a plain `read_file` returns the entire page, so the same session
got one answer from read_file and a narrower one from the refusal.

Two reasons that is wrong, and the second is the expensive one:

  * It adds no context the agent could not already have. A diligent model calls read_file,
    receives the whole page, and only then calls update_section — the same bytes, plus a
    round. Handing the page back makes the correct path free rather than the guess cheap.
  * It is what stops the SAME page being refused once per section. An observed 58-page
    ingest paid one refusal per page — guess Overview, be refused, resend Overview — and a
    page needing three sections paid three. Crediting full read coverage on the handback
    collapses those to one.

The threshold is `_WIKI_READ_LIMIT` rather than a new constant, so there is one rule
instead of two that can drift: over the limit read_file gives an outline and read_section
gives one section, and this refusal matches that.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


def _long_body(nchars, nsections=12):
    out = ["# Big\n"]
    i = 0
    while sum(len(x) for x in out) < nchars:
        out.append(f"\n## Sec {i}\n\n" + ("Prose here. " * 60) + "\n")
        i += 1
    return "".join(out)


class SmallPageTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.w.page("entities/newsom.md", title="Gavin Newsom", type="entity",
                    sources=["sources/s1.md"],
                    body="# Gavin Newsom\n\n## Overview\n\nA governor.\n\n"
                         "## Political Career\n\nElected in 2018.\n\n"
                         "## Presidential Ambitions\n\nSpeculation persists.\n\n"
                         "## Sources\n\n- [S1](../sources/s1.md)\n")

    def _refuse(self, section="Overview"):
        return agent._update_section({"path": "wiki/entities/newsom.md",
                                      "section": section, "content": "replacement"})

    def test_the_whole_page_comes_back(self):
        r = self._refuse()
        self.assertIn("WHOLE PAGE", r)
        for s in ("## Overview", "## Political Career", "## Presidential Ambitions"):
            self.assertIn(s, r, f"{s} missing from the handback")
        self.assertIn("Elected in 2018", r, "other sections' CONTENT, not just their names")

    def test_it_is_wrapped_as_a_file_not_a_section(self):
        r = self._refuse()
        self.assertIn('<file path="wiki/entities/newsom.md">', r)
        self.assertNotIn("<section", r)

    def test_the_detour_is_ruled_out(self):
        """A refusal that leaves the wasteful route open is one the model will take."""
        r = self._refuse()
        self.assertIn("do NOT call read_file or read_section first", r)

    def test_the_auto_generated_sources_section_is_dropped(self):
        """It is rendered from the sources: frontmatter that is in the same payload, and on
        a small page it can be half the bytes."""
        r = self._refuse()
        self.assertNotIn("## Sources", r)
        self.assertIn("sources:", r, "the frontmatter sources: list is still there")

    def test_the_page_is_not_written(self):
        before = self.w.disk("entities/newsom.md")
        self._refuse()
        self.assertEqual(self.w.disk("entities/newsom.md"), before)

    # -- the expensive half: no repeat refusals on the same page ----------------

    def test_a_second_section_on_the_same_page_is_not_refused(self):
        self._refuse()
        out = agent._update_section({"path": "wiki/entities/newsom.md",
                                     "section": "Presidential Ambitions",
                                     "content": "Speculation persists. He may run in 2028."})
        self.assertNotIn("Error", out, out[:200])
        self.assertIn("He may run in 2028", self.w.disk("entities/newsom.md"))

    def test_every_section_is_credited_not_just_the_guessed_one(self):
        self._refuse()
        rel = "entities/newsom.md"
        read = agent._ctx()._session_read_sections
        for name in ("overview", "political career", "presidential ambitions"):
            self.assertIn((rel, name), read, name)

    def test_full_read_coverage_is_credited(self):
        self._refuse()
        rel = "entities/newsom.md"
        full = len(agent._strip_system_fm_fields(self.w.disk(rel)))
        self.assertGreaterEqual(agent._ctx()._session_read_coverage.get(rel, 0), full)

    def test_update_file_becomes_reachable(self):
        """The agent really has seen the whole page, so the whole-page tool is honest now."""
        self._refuse()
        out = agent._update_file(
            "wiki/entities/newsom.md",
            '---\ntitle: "Gavin Newsom"\ntype: entity\ntags: []\nupdated: 2026-09-30\n'
            'sources: []\n---\n\n# Gavin Newsom\n\n## Overview\n\nA governor of California, '
            'reorganized.\n\n## Political Career\n\nElected in 2018, and re-elected.\n\n'
            '## Presidential Ambitions\n\nSpeculation persists about 2028.\n')
        self.assertNotIn("Error", out, out[:300])


class LongPageTest(TempWikiTestCase):
    """Over the limit nothing changes: read_file gives an outline, read_section gives one
    section, and this refusal gives one section. That is the consistency the threshold buys."""

    def setUp(self):
        super().setUp()
        self.w.page("entities/big.md", title="Big", type="entity",
                    body=_long_body(agent._WIKI_READ_LIMIT + 8000))
        self.assertGreater(len(self.w.disk("entities/big.md")), agent._WIKI_READ_LIMIT)

    def _refuse(self, section="Sec 0"):
        return agent._update_section({"path": "wiki/entities/big.md",
                                      "section": section, "content": "replacement"})

    def test_only_the_section_comes_back(self):
        r = self._refuse()
        self.assertIn("<section", r)
        self.assertNotIn("WHOLE PAGE", r)

    def test_the_other_sections_are_still_named(self):
        self.assertIn("other sections", self._refuse())

    def test_the_detour_is_still_ruled_out(self):
        """The long-page branch has its own copy of this clause, and it lost its test when
        the module that covered it moved to a short fixture — mutate.py reported the removal
        MISSED. Handing the content back is half the job; without this an observed ingest
        called read_section for text it was already holding."""
        r = self._refuse()
        self.assertRegex(r, r"do NOT call read_\w+( or read_\w+)? first")

    def test_the_payload_is_delimited(self):
        r = self._refuse()
        self.assertIn('<section path="wiki/entities/big.md"', r)
        self.assertIn("</section>", r)

    def test_a_second_section_is_still_refused(self):
        self._refuse("Sec 0")
        self.assertIn("had not read", self._refuse("Sec 1"))

    def test_no_whole_page_coverage_is_credited(self):
        self._refuse()
        rel = "entities/big.md"
        full = len(agent._strip_system_fm_fields(self.w.disk(rel)))
        self.assertLess(agent._ctx()._session_read_coverage.get(rel, 0), full)


class ThresholdTest(TempWikiTestCase):
    """The boundary itself, so the rule cannot quietly become two rules."""

    def _page_of(self, nchars):
        self.w.page("entities/p.md", title="P", type="entity", body=_long_body(nchars))
        return len(agent._strip_system_fm_fields(self.w.disk("entities/p.md")))

    def _refuse(self):
        return agent._update_section({"path": "wiki/entities/p.md",
                                      "section": "Sec 0", "content": "x"})

    def test_just_under_the_limit_hands_back_the_page(self):
        n = self._page_of(agent._WIKI_READ_LIMIT - 4000)
        self.assertLessEqual(n, agent._WIKI_READ_LIMIT)
        self.assertIn("WHOLE PAGE", self._refuse())

    def test_just_over_the_limit_hands_back_the_section(self):
        n = self._page_of(agent._WIKI_READ_LIMIT + 4000)
        self.assertGreater(n, agent._WIKI_READ_LIMIT)
        self.assertIn("<section", self._refuse())

    def test_it_agrees_with_read_file(self):
        """The invariant: the refusal is never less informative than read_file would be."""
        self._page_of(agent._WIKI_READ_LIMIT - 4000)
        agent.init_session()
        self.assertNotIn("[OUTLINE", agent._read_file("wiki/entities/p.md"))
        agent.init_session()
        self.assertIn("WHOLE PAGE", self._refuse())


if __name__ == "__main__":
    unittest.main()
