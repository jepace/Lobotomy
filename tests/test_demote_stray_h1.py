"""A section written as `# Name` is unaddressable, so heal_pages demotes it to `## Name`.

Observed on artificial-intelligence.md, which carried `# Applications` and `# Current
Debates & Challenges` at level 1. `_page_section_names` skips level-1 headings, so neither
section could be read or edited by ANY section tool — read_section does not find them,
update_section cannot address them, and append_section asked for that name would create a
second section beside them.

That is very likely what made a whole-page rewrite look like the only way to fix the page,
and that rewrite cost it half its sourced detail. A defect that leaves the cheap tools
unable to act pushes the expensive, lossy one. One mechanically-correct answer — add a `#` —
so it is absorbed rather than reported.

Two things it must not touch: the page's own opening H1, and a `#` inside a fenced code
block, where it is a shell comment or a preprocessor line and not a heading at all.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class UnitTest(unittest.TestCase):

    def test_a_stray_h1_is_demoted(self):
        body, names = agent.demote_stray_h1s(
            "# Artificial Intelligence\n\n## Definition\n\nx\n\n# Applications\n\n- a\n",
            "Artificial Intelligence")
        self.assertEqual(names, ["Applications"])
        self.assertIn("## Applications", body)
        self.assertNotIn("\n# Applications", body)

    def test_the_pages_own_h1_is_untouched(self):
        body, names = agent.demote_stray_h1s(
            "# Artificial Intelligence\n\n## Definition\n\nx\n", "Artificial Intelligence")
        self.assertEqual(names, [])
        self.assertTrue(body.startswith("# Artificial Intelligence\n"))

    def test_several_are_demoted_in_order(self):
        _body, names = agent.demote_stray_h1s(
            "# AI\n\n# Applications\n\n- a\n\n# Current Debates & Challenges\n\n- b\n", "AI")
        self.assertEqual(names, ["Applications", "Current Debates & Challenges"])

    def test_deeper_headings_are_left_alone(self):
        body, names = agent.demote_stray_h1s(
            "# AI\n\n## Definition\n\n### Detail\n\nx\n", "AI")
        self.assertEqual(names, [])
        self.assertIn("### Detail", body)

    def test_a_hash_inside_a_fenced_block_is_not_a_heading(self):
        """The one that would corrupt every page quoting a script."""
        src = ("# AI\n\n## Usage\n\n```sh\n# install the thing\npip install x\n```\n\n"
               "more prose\n")
        body, names = agent.demote_stray_h1s(src, "AI")
        self.assertEqual(names, [])
        self.assertIn("\n# install the thing\n", body)

    def test_a_tilde_fence_counts_too(self):
        src = "# AI\n\n~~~\n# not a heading\n~~~\n"
        body, names = agent.demote_stray_h1s(src, "AI")
        self.assertEqual(names, [])
        self.assertIn("# not a heading", body)

    def test_a_stray_h1_after_a_fence_is_still_demoted(self):
        src = "# AI\n\n```\n# comment\n```\n\n# Applications\n\n- a\n"
        body, names = agent.demote_stray_h1s(src, "AI")
        self.assertEqual(names, ["Applications"])
        self.assertIn("# comment", body)

    def test_an_h1_repeating_the_page_title_is_left_for_a_human(self):
        """Demoting it would manufacture a `## <page title>` section, which the heading
        rules refuse — one violation traded for another (principle 4)."""
        body, names = agent.demote_stray_h1s(
            "# AI\n\n## Definition\n\nx\n\n# AI\n\nduplicate\n", "AI")
        self.assertEqual(names, [])
        self.assertIn("\n# AI\n\nduplicate", body)

    def test_it_is_idempotent(self):
        src = "# AI\n\n# Applications\n\n- a\n"
        once, _ = agent.demote_stray_h1s(src, "AI")
        twice, names = agent.demote_stray_h1s(once, "AI")
        self.assertEqual(twice, once)
        self.assertEqual(names, [])

    def test_trailing_newline_shape_is_preserved(self):
        body, _ = agent.demote_stray_h1s("# AI\n\n# Applications", "AI")
        self.assertTrue(body.endswith("## Applications"))


class HealTest(TempWikiTestCase):

    def _page(self, body):
        self.w.page("concepts/ai.md", title="Artificial Intelligence", type="concept",
                    body=body)
        return self.w.wiki / "concepts" / "ai.md"

    def test_heal_pages_makes_the_section_visible_in_the_outline(self):
        """The assertion that matters, and it is about VISIBILITY, not reachability.

        Measured on unmodified code: read_section finds an H1 section and update_section
        rewrites it. What fails is every listing — _page_section_names skips level 1, so the
        section is missing from the read_file outline an agent plans a reorganization from.
        Asked to restructure the real page, the model was handed a map naming neither of its
        two largest sections.
        """
        p = self._page("# Artificial Intelligence\n\n## Definition\n\nx\n\n"
                       "# Applications\n\n- Something specific.\n")
        outline_before = agent._page_outline(self.w.disk("concepts/ai.md"),
                                             "Artificial Intelligence")
        self.assertNotIn("Applications", outline_before, outline_before)

        agent.heal_pages()
        self.assertIn("## Applications", p.read_text())

        outline_after = agent._page_outline(self.w.disk("concepts/ai.md"),
                                            "Artificial Intelligence")
        self.assertIn("Applications", outline_after, outline_after)
        # And it was addressable all along — the repair must not have broken that.
        after = agent._read_section({"path": "wiki/concepts/ai.md",
                                     "section": "Applications"})
        self.assertIn("Something specific", after, after[:300])

    def test_heal_pages_is_idempotent_on_it(self):
        p = self._page("# Artificial Intelligence\n\n## Definition\n\nx\n\n"
                       "# Applications\n\n- a\n")
        agent.heal_pages()
        once = p.read_text()
        agent.heal_pages()
        self.assertEqual(p.read_text(), once)

    def test_a_clean_page_is_not_rewritten(self):
        p = self._page("# Artificial Intelligence\n\n## Definition\n\nx\n\n"
                       "## Applications\n\n- a\n")
        before = p.read_text()
        agent.heal_pages()
        self.assertEqual(p.read_text(), before)

    def test_the_demoted_section_appears_in_the_page_outline(self):
        self._page("# Artificial Intelligence\n\n## Definition\n\nx\n\n"
                   "# Applications\n\n- a\n")
        agent.heal_pages()
        names = [n for _, n in agent._page_section_names(
            self.w.disk("concepts/ai.md"), "Artificial Intelligence")]
        self.assertIn("Applications", names)


if __name__ == "__main__":
    unittest.main()
