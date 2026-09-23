"""merge_page: fold one page into another, and deprecated: retire one in place.

find_duplicate_pages.py finds one subject under two slugs and is report-only, because
deciding two pages are the same thing needs judgment. Everything after that decision is
mechanical — and doing it by hand means grepping for every link and getting the ../ count
right from each referring directory.

The refusal is the interesting half. Bodies are NOT merged: concatenating two pages is how
you recreate the duplication the wiki exists to avoid, so this stops while the merged-away
page still says anything the survivor does not.

deprecated: true was documented as the way to retire a page and honoured nowhere, so a
retired page stayed in the title map — the autolinker kept linking to it and lookup_titles
kept telling the agent to update it. It is honoured now.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

SHARED = ("The total monetary value of all finished goods and services produced "
          "within a country in a given period.")
ONLY_ON_LOSER = ("It omits unpaid household labour and counts remediation of harm "
                 "as growth.")


class MergePageTest(TempWikiTestCase):

    def _pair(self, loser_extra=""):
        self.w.page("concepts/gross-domestic-product.md", title="Gross Domestic Product",
                    type="concept",
                    body=f"# Gross Domestic Product\n\n## Definition\n\n{SHARED}\n",
                    sources=["sources/imf-2026-gdp.md"])
        self.w.page("concepts/gdp.md", title="GDP", type="concept",
                    body=f"# GDP\n\n## Definition\n\n{SHARED}\n{loser_extra}",
                    sources=["sources/worldbank-2026-accounts.md"])
        self.w.page("entities/mark-carney.md", title="Mark Carney", type="entity",
                    body="# Mark Carney\n\n## Overview\n\nHe has commented on "
                         "[GDP](../concepts/gdp.md) growth.\n")

    def _merge(self, **kw):
        return agent.merge_page("concepts/gdp.md", "concepts/gross-domestic-product.md", **kw)

    def test_links_are_repointed_and_relativized(self):
        self._pair()
        r = self._merge(extra_aliases=["GDP"])
        self.assertIsNone(r["error"], r["error"])
        body = self.w.disk("entities/mark-carney.md")
        self.assertIn("../concepts/gross-domestic-product.md", body)
        self.assertNotIn("concepts/gdp.md", body)
        self.assertIn("[GDP]", body, "the link text was rewritten — only the target moves")

    def test_the_old_title_becomes_an_alias(self):
        # Without this, prose saying "GDP" stops resolving the moment the page goes.
        self._pair()
        self._merge()
        fm = self.w.disk("concepts/gross-domestic-product.md").split("---")[1]
        self.assertIn("GDP", fm)
        self.assertIn("aliases:", fm)

    def test_sources_are_unioned_not_replaced(self):
        self._pair()
        self._merge()
        fm = self.w.disk("concepts/gross-domestic-product.md").split("---")[1]
        self.assertIn("imf-2026-gdp.md", fm, "the survivor's own provenance was dropped")
        self.assertIn("worldbank-2026-accounts.md", fm, "the loser's provenance was dropped")

    def test_the_loser_is_deleted(self):
        self._pair()
        self._merge()
        self.assertFalse(self.w.exists("concepts/gdp.md"))

    def test_unmerged_content_is_refused(self):
        self._pair(loser_extra=f"\n## Criticism\n\n{ONLY_ON_LOSER}\n")
        r = self._merge()
        self.assertIsNotNone(r["error"])
        self.assertTrue(self.w.exists("concepts/gdp.md"),
                        "a refused merge deleted the page anyway")
        self.assertTrue(any("unpaid household labour" in o for o in r["outstanding"]),
                        r["outstanding"])

    def test_refusal_leaves_links_untouched(self):
        self._pair(loser_extra=f"\n## Criticism\n\n{ONLY_ON_LOSER}\n")
        before = self.w.disk("entities/mark-carney.md")
        self._merge()
        self.assertEqual(self.w.disk("entities/mark-carney.md"), before)

    def test_force_merges_anyway(self):
        self._pair(loser_extra=f"\n## Criticism\n\n{ONLY_ON_LOSER}\n")
        r = self._merge(force=True)
        self.assertIsNone(r["error"], r["error"])
        self.assertFalse(self.w.exists("concepts/gdp.md"))

    def test_dry_run_changes_nothing(self):
        self._pair()
        before = self.w.disk("entities/mark-carney.md")
        r = self._merge(dry_run=True)
        self.assertTrue(self.w.exists("concepts/gdp.md"))
        self.assertEqual(self.w.disk("entities/mark-carney.md"), before)
        self.assertEqual(r["repointed"], ["entities/mark-carney.md"],
                         "a dry run must still report what it would do")

    def test_merging_a_page_into_itself_is_refused(self):
        self._pair()
        r = agent.merge_page("concepts/gdp.md", "concepts/gdp.md")
        self.assertIsNotNone(r["error"])
        self.assertTrue(self.w.exists("concepts/gdp.md"))

    def test_source_pages_are_never_merged(self):
        # A source page records one source. Two of them are not duplicates.
        self.w.page("sources/a-2026-x.md", title="A 2026 X", type="source",
                    body="# A 2026 X\n\n## Summary\n\nS.\n")
        self.w.page("sources/b-2026-y.md", title="B 2026 Y", type="source",
                    body="# B 2026 Y\n\n## Summary\n\nS.\n")
        r = agent.merge_page("sources/a-2026-x.md", "sources/b-2026-y.md", force=True)
        self.assertIsNotNone(r["error"])
        self.assertTrue(self.w.exists("sources/a-2026-x.md"))

    def test_the_deleted_pages_history_survives(self):
        self._pair()
        self.w.read("wiki/concepts/gdp.md")
        agent._update_file("wiki/concepts/gdp.md",
                           f"# GDP\n\n## Definition\n\n{SHARED} Revised.\n")
        self._merge(force=True)
        hist = agent.HISTORY_DIR / "concepts" / "gdp.md"
        self.assertTrue(any(hist.rglob("*.md")),
                        "the only remaining copy of the merged-away page was destroyed")

    def test_the_survivor_is_reachable_by_the_old_name(self):
        self._pair()
        self._merge()
        r = agent.TOOL_FNS["lookup_titles"]({"names": ["GDP"]})
        self.assertIn("gross-domestic-product.md", r)
        self.assertNotIn("CREATE", r.split("This answer is exact")[0].split("UPDATE")[-1]
                         if "UPDATE" in r else "CREATE")


class DeprecatedFlagTest(TempWikiTestCase):

    def test_a_deprecated_page_leaves_the_title_map(self):
        self.w.page("concepts/gdp.md", title="GDP", type="concept",
                    body="# GDP\n\n## Definition\n\nX.\n")
        self.assertIn("GDP", [t for t, _ in agent._build_title_map()])
        p = self.w.wiki / "concepts" / "gdp.md"
        agent._atomic_write(p, p.read_text(encoding="utf-8").replace(
            "type: concept", "type: concept\ndeprecated: true"))
        self.assertNotIn("GDP", [t for t, _ in agent._build_title_map()],
                         "deprecated: true is documented as retiring a page and did nothing")

    def test_a_deprecated_page_is_not_offered_by_lookup_titles(self):
        self.w.page("concepts/gdp.md", title="GDP", type="concept",
                    body="# GDP\n\n## Definition\n\nX.\ndeprecated: true\n")
        p = self.w.wiki / "concepts" / "gdp.md"
        agent._atomic_write(p, p.read_text(encoding="utf-8").replace(
            "type: concept", "type: concept\ndeprecated: true"))
        r = agent.TOOL_FNS["lookup_titles"]({"names": ["GDP"]})
        self.assertIn("RETIRED", r, "a retired page was still offered as the page to update")
        self.assertNotIn("UPDATE (", r)
        # And NOT as a create: create_file refuses on a path that exists, so telling the
        # agent to create it is the four-round bounce _resolve_page was written to stop.
        self.assertNotIn("CREATE (", r)

    def test_a_live_page_is_unaffected(self):
        self.w.page("concepts/gdp.md", title="GDP", type="concept",
                    body="# GDP\n\n## Definition\n\nX.\n")
        self.assertIn("GDP", [t for t, _ in agent._build_title_map()])


if __name__ == "__main__":
    unittest.main()
