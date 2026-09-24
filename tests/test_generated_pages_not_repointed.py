"""Link-rewriting passes never edit the generated or append-only pages.

A real merge dry-run reported this and nothing else:

    [dry-run] repointed links in entities/index.md
    [dry-run] repointed links in index.md
    [dry-run] repointed links in log.md

Three "repointed" pages, not one of them content. Two different problems, and the log's is
the serious one.

index.md (root and per-subdirectory) is generated — merge_page.py rebuilds it seconds
later itself, so patching it is work that is undone before anyone sees it.

log.md is the audit trail, and repointing a link there rewrites what the log SAYS
happened: the entry recording that an ingest created
new-york-university-langone-health.md would come to claim it created
nyu-langone-health.md, which it never did. A record edited to agree with the present is
not a record. The dead link in the log is the true statement, and lint already skips
log.md so it costs nothing to leave correct.

Neither can be reverted either — _snapshot_version skips both by name — which is the same
argument that took them out of repair_links. The list now lives in agent as
_GENERATED_PAGES so the three passes cannot drift apart.

The catch worth remembering: once a pass stops repointing index.md it MUST rebuild it, or
the rename leaves the dead link the repoint used to fix. merge_page.py already rebuilt;
rename_page.py did not, and had to start.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent
import repair_links

LINK = "[New York University Langone Health](entities/new-york-university-langone-health.md)"


class GeneratedPagesTest(TempWikiTestCase):

    def _build(self):
        self.w.page("entities/nyu-langone-health.md", title="NYU Langone Health",
                    type="entity",
                    body="# NYU Langone Health\n\n## Overview\n\nAn academic medical center.\n")
        self.w.page("entities/new-york-university-langone-health.md",
                    title="New York University Langone Health", type="entity",
                    body="# New York University Langone Health\n\n## Overview\n\n"
                         "An academic medical center.\n")
        self.w.page("entities/babak-orandi.md", title="Babak Orandi", type="entity",
                    body="# Babak Orandi\n\n## Overview\n\nWorks at [New York University "
                         "Langone Health](new-york-university-langone-health.md).\n")
        # Shaped like the real one: a short prose preamble, then the generated blocks.
        # _rebuild_index preserves everything above the first "## " and replaces the rest,
        # so the entry has to sit under ## Entities to be the thing a rebuild clears.
        (agent.WIKI_DIR / "index.md").write_text(
            f"# Wiki Index\n\n_Last updated: 2026-09-24_\n\n---\n\n"
            f"## Entities\n\n- {LINK}\n", encoding="utf-8")
        (agent.WIKI_DIR / "log.md").write_text(f"# Log\n\n- 2026-09-24 created {LINK}\n",
                                               encoding="utf-8")
        (agent.WIKI_DIR / "entities" / "index.md").write_text(
            "# Entities\n\n- [NYULH](new-york-university-langone-health.md)\n",
            encoding="utf-8")

    def _merge(self, **kw):
        return agent.merge_page("wiki/entities/new-york-university-langone-health.md",
                                "wiki/entities/nyu-langone-health.md", **kw)

    def test_the_merge_repoints_only_the_content_page(self):
        self._build()
        self.assertEqual(self._merge()["repointed"], ["entities/babak-orandi.md"])

    def test_the_content_page_really_is_repointed(self):
        self._build()
        self._merge()
        self.assertIn("(nyu-langone-health.md)", self.w.disk("entities/babak-orandi.md"))

    def test_the_log_still_says_what_actually_happened(self):
        self._build()
        self._merge()
        self.assertIn(LINK, (agent.WIKI_DIR / "log.md").read_text(encoding="utf-8"),
                      "the audit trail was edited to agree with the present")

    def test_neither_index_is_patched(self):
        self._build()
        before_root = (agent.WIKI_DIR / "index.md").read_text(encoding="utf-8")
        before_sub = (agent.WIKI_DIR / "entities" / "index.md").read_text(encoding="utf-8")
        self._merge()
        self.assertEqual((agent.WIKI_DIR / "index.md").read_text(encoding="utf-8"),
                         before_root)
        self.assertEqual((agent.WIKI_DIR / "entities" / "index.md").read_text(encoding="utf-8"),
                         before_sub)

    def test_a_rebuild_is_what_clears_the_index(self):
        # The other half of the contract: not patching is only safe because the generator
        # runs. merge_page.py calls _rebuild_index itself right after the merge.
        self._build()
        self._merge()
        agent._rebuild_index({})
        idx = (agent.WIKI_DIR / "index.md").read_text(encoding="utf-8")
        self.assertNotIn("new-york-university-langone-health.md", idx, idx)
        self.assertIn("nyu-langone-health.md", idx)

    def test_a_dead_link_in_the_prose_preamble_is_nobodys_job(self):
        """Known gap, asserted so it is a decision rather than a surprise.

        _rebuild_index preserves everything above the first "## " verbatim, and the
        repointing passes now skip index.md — so a link written by hand into that preamble
        is fixed by nothing. The preamble is a few hand-written lines, so this is cheap to
        live with, but it should not be discovered by someone wondering why their edit
        keeps coming back.
        """
        self._build()
        p = agent.WIKI_DIR / "index.md"
        p.write_text(f"# Wiki Index\n\nSee {LINK} for the hospital.\n\n---\n\n"
                     f"## Entities\n\n- {LINK}\n", encoding="utf-8")
        self._merge()
        agent._rebuild_index({})
        idx = p.read_text(encoding="utf-8")
        self.assertIn(f"See {LINK} for the hospital.", idx,
                      "the preamble stopped being preserved")
        self.assertNotIn("- " + LINK, idx, "the generated block was not rebuilt")


    def test_the_three_passes_share_one_list(self):
        # repair_links had its own copy. Two lists is one drift away from the log being
        # edited again by whichever pass was forgotten.
        self.assertIs(repair_links._GENERATED, agent._GENERATED_PAGES)

    def test_is_generated_page_covers_subdirectory_indexes(self):
        for rel, expected in (("index.md", True), ("entities/index.md", True),
                              ("log.md", True), ("entities/acme.md", False),
                              ("reading-list.md", False)):
            with self.subTest(rel):
                self.assertEqual(agent.is_generated_page(agent.WIKI_DIR / rel), expected)


if __name__ == "__main__":
    unittest.main()
