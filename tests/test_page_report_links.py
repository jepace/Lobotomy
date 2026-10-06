"""`page_report.py`'s LINKS and TARGETS: links that point at the wrong thing.

From `donald-trump.md`, where the link damage was the largest single category and the
mildest part of it was the part everyone notices. Dozens of common-word bleeds
(`[notes]`, `[standing]`, `[power]`) are **deliberately not judged here** — see
`_LINK_NOTE` and `BleedingIsNotJudgedHereTest` below. What this module covers is the
damage that is mechanically decidable and was reported by nothing:

  dead          a target with no file behind it
  split name    `White [House](…)` — a proper name with the link starting partway
                through it, four times on that page, plus `Donald Trump [Jr](…)`
  source link   body prose linking into `wiki/sources/`
  TARGETS       `war-with-iran`, `war-in-iran` and `iran-war`; `republican`,
                `republicans` and `republican-party` — one subject, several pages, every
                future inbound link split between them

**The split-name check took four tries and three of them cried wolf**, which is the
lesson worth keeping. A capitalised word before a capitalised one-word link flags
`Trump [Republicans](…)`, two words about two subjects. Adding `_HONORIFICS` fixes
`President [Trump](…)` and does nothing for that. What settles it is asking whether the
FULLER name is a page the autolinker knows — and that settles it the autolinker's own way,
because the title map is sorted longest-first precisely so the longer name wins, which
makes every hit a race the linker is designed to win and lost.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import page_report


def _report(w, body, **kw):
    w.page("entities/subject.md", title="Subject", body=body, **kw)
    return page_report.analyze(w.wiki / "entities" / "subject.md")


class DeadLinkTest(unittest.TestCase):
    def test_a_target_with_no_file_behind_it_is_reported(self):
        with TempWiki() as w:
            w.page("entities/real.md", title="Real", body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nSee [Real](real.md) and "
                           "[Gone](missing.md).\n")
            self.assertEqual([x["display"] for x in r["dead"]], ["Gone"])

    def test_a_target_is_resolved_from_the_pages_own_directory(self):
        """Which is how the wiki writes them and how a browser follows them. Resolving
        from the wiki root instead would call every correct `../concepts/x.md` dead."""
        with TempWiki() as w:
            w.page("concepts/thing.md", title="Thing", type="concept",
                   body="## Definition\n\nx\n")
            r = _report(w, "## Overview\n\nSee [Thing](../concepts/thing.md).\n")
            self.assertEqual(r["dead"], [])

    def test_an_anchor_does_not_make_a_live_target_dead(self):
        with TempWiki() as w:
            w.page("entities/real.md", title="Real", body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nSee [Real](real.md#overview).\n")
            self.assertEqual(r["dead"], [])

    def test_an_external_url_is_not_this_tools_business(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nSee [a site](https://example.com/x).\n")
            self.assertEqual(r["dead"], [])

    def test_the_generated_sources_section_is_not_checked_for_dead_links(self):
        """Its rows come from frontmatter, so a dead one there is a deleted source page —
        reported under SOURCES, where the repair is different."""
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nx\n\n## Sources\n\n- [A](../sources/a.md)\n")
            self.assertEqual(r["dead"], [])


class SplitNameTest(unittest.TestCase):
    def test_a_link_starting_partway_through_a_known_name_is_reported(self):
        with TempWiki() as w:
            w.page("entities/white-house.md", title="White House",
                   body="## Overview\n\nx\n")
            w.page("entities/house.md", title="House", body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nThe White [House](house.md) said so today.\n")
            self.assertEqual(len(r["split"]), 1)
            self.assertEqual(r["split"][0]["name"], "White House")
            self.assertEqual(r["split"][0]["should_be"], "entities/white-house.md")

    def test_several_preceding_words_are_tried(self):
        """"Trump Jr" is not a name and "Donald Trump Jr" is, so a one-word lookbehind
        finds the White House case and misses this one."""
        with TempWiki() as w:
            w.page("entities/donald-trump-jr.md", title="Donald Trump Jr",
                   body="## Overview\n\nx\n")
            w.page("entities/jr.md", title="Jr", body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nHe said that Donald Trump [Jr](jr.md) would "
                           "repay it.\n")
            self.assertEqual([x["name"] for x in r["split"]], ["Donald Trump Jr"])

    def test_two_capitalised_words_that_are_not_a_known_name_are_not_reported(self):
        """The test that three earlier versions failed. `Trump [Republicans](…)` is two
        words about two subjects, and flagging it buries the two real hits."""
        with TempWiki() as w:
            w.page("entities/republicans.md", title="Republicans",
                   body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nIn September Trump "
                           "[Republicans](republicans.md) were unhappy.\n")
            self.assertEqual(r["split"], [])

    def test_an_honorific_before_a_name_is_not_a_split(self):
        """And the page's own aliases make this necessary rather than theoretical:
        "President Trump" is a real alias of `donald-trump.md`, so without the honorific
        list the known-name test would confirm it."""
        with TempWiki() as w:
            w.page("entities/donald-trump.md", title="Donald Trump",
                   aliases=["Trump", "President Trump"], body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nHe met President "
                           "[Trump](donald-trump.md) on Tuesday.\n")
            self.assertEqual(r["split"], [])

    def test_a_link_opening_a_clause_is_not_a_split(self):
        """A full stop or a comma before the link ends the name, whatever follows it."""
        with TempWiki() as w:
            w.page("entities/white-house.md", title="White House",
                   body="## Overview\n\nx\n")
            w.page("entities/house.md", title="House", body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nIt happened at the White. "
                           "[House](house.md) members objected loudly.\n")
            self.assertEqual(r["split"], [])

    def test_a_multi_word_link_display_is_not_a_split(self):
        with TempWiki() as w:
            w.page("entities/white-house.md", title="White House",
                   body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nThe [White House](white-house.md) said so.\n")
            self.assertEqual(r["split"], [])


class SourceLinkTest(unittest.TestCase):
    def test_body_prose_linking_into_sources_is_reported(self):
        """Two defects wear this shape and the report names both: prose citing an article
        inline, or a source page whose `title:` is a subject rather than a headline, so
        the autolinker matched that subject's name. The page this came from had
        `[Wildlife Acoustics](../sources/ren-2026-tariffs-…)` — a company's name pointing
        at a news story. That one is only fixable on the source page, which is immutable
        to the LLM, so no relink will ever repair it."""
        with TempWiki() as w:
            w.page("sources/ren-2026-tariffs.md", title="Wildlife Acoustics",
                   type="source", body="## Summary\n\nx\n")
            r = _report(w, "## Overview\n\nFirms like [Wildlife Acoustics]"
                           "(../sources/ren-2026-tariffs.md) moved north.\n")
            self.assertEqual([x["display"] for x in r["source_links"]],
                             ["Wildlife Acoustics"])

    def test_the_generated_sources_section_is_not_reported_as_source_links(self):
        """Every row in it is a link into wiki/sources/ by construction — reporting those
        would mean every page in the wiki has dozens of findings."""
        with TempWiki() as w:
            w.page("sources/a-2026-x.md", title="A", type="source",
                   body="## Summary\n\nx\n")
            r = _report(w, "## Overview\n\nx\n\n## Sources\n\n"
                           "- [A](../sources/a-2026-x.md)\n")
            self.assertEqual(r["source_links"], [])


class CompetingTargetsTest(unittest.TestCase):
    def test_one_subject_under_several_slugs_is_one_group(self):
        with TempWiki() as w:
            for rel, title in (("entities/war-with-iran.md", "War with Iran"),
                               ("concepts/war-in-iran.md", "War in Iran"),
                               ("concepts/iran-war.md", "Iran War")):
                w.page(rel, title=title,
                       type="concept" if "concepts" in rel else "entity",
                       body="## Overview\n\nx\n")
            r = _report(w, "## Overview\n\nThe [war with Iran]"
                           "(war-with-iran.md), the [war in Iran]"
                           "(../concepts/war-in-iran.md) and the [Iran war]"
                           "(../concepts/iran-war.md) dragged on and on.\n")
            self.assertEqual(len(r["targets"]["same"]), 1)
            self.assertEqual(len(r["targets"]["same"][0]), 3)

    def test_a_plural_and_a_singular_slug_are_one_subject(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nOn [tariff](tariff.md) and "
                           "[tariffs](tariffs.md) policy.\n")
            self.assertEqual(r["targets"]["same"], [["tariff.md", "tariffs.md"]])

    def test_a_leading_article_does_not_make_a_second_subject(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nPer [NYT](new-york-times.md) and "
                           "[The NYT](the-new-york-times.md).\n")
            self.assertEqual(r["targets"]["same"],
                             [["new-york-times.md", "the-new-york-times.md"]])

    def test_two_genuinely_different_subjects_are_not_grouped(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nOn [oil](oil.md) and "
                           "[oil reserves](oil-reserves.md) and "
                           "[Iran](iran.md).\n")
            self.assertEqual(r["targets"]["same"], [])

    def test_the_subset_tier_is_opt_in(self):
        """`oil` inside `oil-reserves` is two real concepts, so this tier is often wrong.
        On by default it would make the whole section easy to skip, and the SAME tier
        would go unread with it — the bleeding_titles lesson about not crying wolf."""
        with TempWiki() as w:
            body = ("## Overview\n\nOn [oil](oil.md) and "
                    "[oil reserves](oil-reserves.md).\n")
            w.page("entities/subject.md", title="Subject", body=body)
            p = w.wiki / "entities" / "subject.md"
            self.assertEqual(page_report.analyze(p)["targets"]["subsets"], [])
            with_subsets = page_report.analyze(p, subsets=True)["targets"]["subsets"]
            self.assertTrue(with_subsets)

    def test_an_external_url_is_never_a_competing_target(self):
        with TempWiki() as w:
            r = _report(w, "## Overview\n\nSee [x](https://example.com/tariff) and "
                           "[tariffs](tariffs.md).\n")
            self.assertEqual(r["targets"]["same"], [])


class BleedingIsNotJudgedHereTest(unittest.TestCase):
    """Common-word bleeding is listed, never flagged, and that is a decision rather than
    an omission.

    A page titled "Tariffs" is SUPPOSED to be linked from the word `tariffs`, and one
    titled "Notes" is not. Nothing on a single page distinguishes them — the evidence is
    wiki-wide, which is what `bleeding_titles.py` reads: it counts capitalised against
    lowercase use of each title, because a proper noun is written capitalised wherever it
    appears and a common noun is not. Duplicating a worse version of that answer here
    would put dozens of confident wrong findings in front of the real ones.
    """

    def test_a_lowercase_link_is_listed_rather_than_reported_as_a_finding(self):
        with TempWiki() as w:
            # Both target pages exist, so a dead-link finding cannot be mistaken for the
            # bleed question being answered here. The first fixture omitted them and the
            # test "passed" its real assertion while failing on an unrelated one.
            w.page("concepts/notes.md", title="Notes", type="concept",
                   body="## Definition\n\nx\n")
            w.page("concepts/tariffs.md", title="Tariffs", type="concept",
                   body="## Definition\n\nx\n")
            r = _report(w, "## Overview\n\nHe [notes](../concepts/notes.md) the "
                           "[tariffs](../concepts/tariffs.md).\n")
            listed = {x["display"] for x in r["lowercase"]}
            self.assertEqual(listed, {"notes", "tariffs"})
            for key in ("dead", "split", "source_links"):
                self.assertEqual(r[key], [], f"{key} must not absorb the bleed question")

    def test_the_note_points_at_the_tool_that_can_answer_it(self):
        self.assertIn("bleeding_titles.py", page_report._LINK_NOTE)


if __name__ == "__main__":
    unittest.main()
