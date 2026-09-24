"""Merging two pages ignores which of the subject's names each sentence happens to use.

The third place one naming gap bit, and the one that hurt most, because it blocked the
cleanup the other two had made necessary.

An ingest created entities/nyu-langone-health.md, was then told the listed name "New York
University Langone Health" had no page, and created a second page for the same hospital.
Merging them back was refused:

    Refused: …/new-york-university-langone-health.md still says 2 thing(s)
             …/nyu-langone-health.md does not.
      - new york university langone health is an academic medical center and health…
      - clinicians and researchers at new york university langone health, such as…

Both "things" were sentences the survivor already had, word for word, with the other name
in them. `_claims` compares with substring containment, and "<long name> is an academic
medical center" does not contain "<short name> is an academic medical center".

Running merge_page is itself the judgement that these two pages are one subject, so within
that call a sentence differing only in which name it uses is not a different sentence.
Every name either page has — titles and aliases, longest first — folds to one placeholder
before comparing.

What must NOT change: a genuinely new fact still refuses the merge, still names itself, and
still has to be moved by hand. Concatenating bodies is the thing this tool exists not to do.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

SURVIVOR = (
    "# NYU Langone Health\n\n## Overview\n\nNYU Langone Health is an academic medical "
    "center and health system associated with New York University.\n\n"
    "## Background\n\nClinicians and researchers at NYU Langone Health, such as obesity "
    "medicine specialist and transplant surgeon Babak Orandi, study GLP-1 drugs.\n")

LOSER = (
    "# New York University Langone Health\n\n## Overview\n\nNew York University Langone "
    "Health is an academic medical center and health system associated with New York "
    "University.\n\n"
    "## Background\n\nClinicians and researchers at New York University Langone Health, "
    "such as obesity medicine specialist and transplant surgeon Babak Orandi, study "
    "GLP-1 drugs.\n")

LOSER_REL = "wiki/entities/new-york-university-langone-health.md"
SURV_REL = "wiki/entities/nyu-langone-health.md"


class MergeNameFoldingTest(TempWikiTestCase):

    def _build(self, loser_body=LOSER, surv_body=SURVIVOR, loser_aliases=None):
        self.w.page("entities/nyu-langone-health.md", title="NYU Langone Health",
                    type="entity", body=surv_body)
        self.w.page("entities/new-york-university-langone-health.md",
                    title="New York University Langone Health", type="entity",
                    body=loser_body)
        if loser_aliases:
            p = self.w.wiki / "entities" / "new-york-university-langone-health.md"
            t = p.read_text(encoding="utf-8")
            p.write_text(t.replace("type: entity",
                                   f'type: entity\naliases: {loser_aliases!r}'.replace("'", '"')),
                         encoding="utf-8")

    def test_the_observed_merge_is_no_longer_refused(self):
        self._build()
        r = agent.merge_page(LOSER_REL, SURV_REL, dry_run=True)
        self.assertIsNone(r["error"], r["error"])
        self.assertEqual(r["outstanding"], [])

    def test_a_genuinely_new_fact_still_refuses(self):
        self._build(loser_body=LOSER + "\n## Funding\n\nIt received a 200 million dollar "
                                       "gift in 2026 from an anonymous donor.\n")
        r = agent.merge_page(LOSER_REL, SURV_REL, dry_run=True)
        self.assertTrue(r["error"], "the merge was allowed through with a fact unmoved")
        self.assertEqual(len(r["outstanding"]), 1, r["outstanding"])
        self.assertIn("200 million dollar", r["outstanding"][0])

    def test_the_refusal_still_names_what_is_outstanding(self):
        self._build(loser_body=LOSER + "\n## Funding\n\nIt received a 200 million dollar "
                                       "gift in 2026 from an anonymous donor.\n")
        r = agent.merge_page(LOSER_REL, SURV_REL, dry_run=True)
        self.assertIn("still says 1 thing(s)", r["error"])
        self.assertIn("--force", r["error"], "the refusal stopped naming the way through")

    def test_an_alias_of_either_page_folds_too(self):
        # The names live in aliases: as well as title:, and a sentence using one of those
        # is just as much not-a-new-fact.
        self._build(
            surv_body="# NYU Langone Health\n\n## Overview\n\nNYU Langone is an academic "
                      "medical center in Manhattan that treats obesity.\n",
            loser_body="# New York University Langone Health\n\n## Overview\n\nNYU Langone "
                       "Health is an academic medical center in Manhattan that treats "
                       "obesity.\n",
            loser_aliases=["NYU Langone"])
        r = agent.merge_page(LOSER_REL, SURV_REL, dry_run=True)
        self.assertEqual(r["outstanding"], [], r["outstanding"])

    def test_the_long_name_is_carried_over_as_an_alias(self):
        # The point of the whole exercise: after the merge the resolver finds the page by
        # the spelled-out name exactly, so nothing has to infer it again.
        self._build()
        agent.merge_page(LOSER_REL, SURV_REL)
        surv = self.w.disk("entities/nyu-langone-health.md")
        self.assertIn("New York University Langone Health", surv, surv)
        self.assertFalse((self.w.wiki / "entities" /
                          "new-york-university-langone-health.md").exists())
        by_key = {t.lower(): rel for t, rel in agent._build_title_map()}
        self.assertEqual(agent._resolve_page("New York University Langone Health", by_key),
                         "entities/nyu-langone-health.md")

    def test_two_genuinely_different_subjects_are_unaffected(self):
        # Folding only ever collapses names the two pages already claim. A fact about a
        # different hospital is still a fact the survivor does not have.
        self._build(loser_body=LOSER + "\n## Rivals\n\nMount Sinai Hospital runs a "
                                       "competing bariatric surgery programme nearby.\n")
        r = agent.merge_page(LOSER_REL, SURV_REL, dry_run=True)
        self.assertEqual(len(r["outstanding"]), 1, r["outstanding"])
        self.assertIn("Mount Sinai", r["outstanding"][0].title())

    def test_a_short_alias_folds_without_mangling_words(self):
        # "NY" is a real name and should fold. What must not happen is folding it inside
        # "company". The fold is word-bounded rather than length-limited, because a length
        # cutoff is not actually a guard here: the fold runs over BOTH pages, so mangling
        # is symmetric and cancels out.
        self._build(
            surv_body="# NYU Langone Health\n\n## Overview\n\nThe company that runs NY "
                      "operates many clinics across the region.\n",
            loser_body="# New York University Langone Health\n\n## Overview\n\nThe "
                       "company that runs NYU Langone Health operates many clinics across "
                       "the region.\n",
            loser_aliases=["NY"])
        r = agent.merge_page(LOSER_REL, SURV_REL, dry_run=True)
        self.assertEqual(r["outstanding"], [], r["outstanding"])


if __name__ == "__main__":
    unittest.main()
