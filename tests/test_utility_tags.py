"""A leading underscore marks a tag as machinery, not subject matter.

Asked for in two steps. First *"add a Tag 'todo' for todo items, so I can dig them out
quickly"* — the merge's summary marker was findable only by `grep`, while the wiki already
has tag pages built for exactly this. Then *"maybe special case '_todo' or other
underscores so we can build some utility for the future"*, which is the better shape: a
namespace rather than one tag, so the next utility tag costs a name and no code.

**The trap this closes is the one the backticked-tag episode documented.** `_collect_tags`
scans every page's tags, and `orientation_message` hands that list to every future ingest
as *"Prefer tags from this list where appropriate"*. Add `_todo` naively and the model is
told to prefer it — so it starts tagging unrelated pages `_todo`, and the single listing
this exists to produce fills with noise. **A value that is read back as input becomes
self-reinforcing, and the fix has to break the loop rather than clean the value**, which is
why the filter is in `orientation_message` and NOT in `_collect_tags`: the tag pages,
`/wiki/tags` and `/wiki/tags/_todo` must all still see it. Filtering at the source would
have removed the feature while fixing the bug.

A prefix beats a list because a list is a second place to forget. `is_utility_tag`
normalizes first, so a tag the model wrapped in backticks is still recognised — the same
reason every other tag reader goes through `norm_tag`.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class IsUtilityTagTest(unittest.TestCase):
    def test_an_underscore_prefix_marks_a_utility_tag(self):
        for t in ("_todo", "_stale", "_needs-review", "_"):
            with self.subTest(tag=t):
                self.assertTrue(agent.is_utility_tag(t))

    def test_an_ordinary_tag_is_not_one(self):
        for t in ("todo", "politics", "trump-administration", "to_do"):
            with self.subTest(tag=t):
                self.assertFalse(agent.is_utility_tag(t))

    def test_it_normalizes_before_deciding(self):
        """A tag the model rendered as code arrives wrapped in backticks — the episode
        that produced `norm_tag` in the first place. Deciding before normalizing would
        leak `` `_todo` `` into the vocabulary while `_todo` was excluded."""
        for t in ("`_todo`", '"_todo"', " _todo ", "_TODO"):
            with self.subTest(tag=t):
                self.assertTrue(agent.is_utility_tag(t))

    def test_the_todo_tag_is_in_the_namespace(self):
        self.assertTrue(agent._TODO_TAG.startswith(agent._UTILITY_TAG_PREFIX))
        self.assertTrue(agent.is_utility_tag(agent._TODO_TAG))


class NotOfferedToTheModelTest(unittest.TestCase):
    """The whole reason the namespace exists."""

    def _wiki(self, w):
        w.page("entities/a.md", title="A Page", tags=["politics", "_todo"],
               body="## Overview\n\nA page about politics.\n")
        w.page("entities/b.md", title="B Page", tags=["economy"],
               body="## Overview\n\nA page about the economy.\n")

    def test_a_utility_tag_is_not_in_the_vocabulary_handed_to_an_ingest(self):
        with TempWiki() as w:
            self._wiki(w)
            msg = agent.orientation_message()
            self.assertIn("politics", msg)
            self.assertIn("economy", msg)
            self.assertNotIn("_todo", msg,
                             "a tag offered as a preference is one the model will copy")

    def test_collect_tags_still_sees_it_so_the_tag_pages_can(self):
        """Filtering at the source would have deleted the feature while fixing the bug:
        `/wiki/tags/_todo` is the entire point of having the tag."""
        with TempWiki() as w:
            self._wiki(w)
            self.assertIn("_todo", agent._collect_tags())

    def test_a_wiki_of_nothing_but_utility_tags_offers_no_vocabulary(self):
        """Rather than offering an empty list with a 'prefer these' instruction above it."""
        with TempWiki() as w:
            w.page("entities/a.md", title="A Page", tags=["_todo"],
                   body="## Overview\n\nA page.\n")
            self.assertNotIn("Existing tags in this wiki", agent.orientation_message())


class MergeSetsTheTagTest(unittest.TestCase):
    SURV = ("## Overview\n\nAIPAC is a lobbying group in Washington that advocates "
            "pro-Israel policy to Congress.\n")
    LOSER = ("## Overview\n\nAIPAC is a lobbying group in Washington that advocates "
             "pro-Israel policy to Congress. It does not operate as a political action "
             "committee despite the name it carries.\n")

    def _merge(self, w, surv_tags=("lobbying", "israel")):
        w.page("entities/survivor.md", title="American Israel Public Affairs Committee",
               tags=list(surv_tags), body=self.SURV)
        w.page("entities/loser.md", title="AIPAC", aliases=["AIPAC"], body=self.LOSER)
        r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
        return r, (w.wiki / "entities" / "survivor.md").read_text()

    def test_a_carried_summary_tags_the_survivor(self):
        with TempWiki() as w:
            r, text = self._merge(w)
            self.assertIsNone(r["error"], r["error"])
            self.assertEqual(r["todo_tag"], "_todo")
            self.assertIn("_todo", agent.parse_tags_line(
                next(l for l in text.split("\n") if l.startswith("tags:"))))

    def test_the_pages_existing_tags_are_kept(self):
        """A tag writer that replaced the line would strip the page's subject tags, which
        is how a page stops appearing on every tag page it belonged to."""
        with TempWiki() as w:
            _r, text = self._merge(w)
            tags = agent.parse_tags_line(
                next(l for l in text.split("\n") if l.startswith("tags:")))
            self.assertEqual(tags, ["lobbying", "israel", "_todo"])

    def test_a_page_with_no_tags_line_still_gets_the_tag(self):
        with TempWiki() as w:
            _r, text = self._merge(w, surv_tags=())
            self.assertIn("_todo", agent.parse_tags_line(
                next(l for l in text.split("\n") if l.startswith("tags:"))))

    def test_the_tag_is_not_added_twice(self):
        with TempWiki() as w:
            self._merge(w)
            w.page("entities/loser.md", title="AIPAC Third", aliases=["AIPAC"],
                   body=self.LOSER.replace("name it carries.",
                                           "name, and files its own disclosures yearly."))
            agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            text = (w.wiki / "entities" / "survivor.md").read_text()
            tags = agent.parse_tags_line(
                next(l for l in text.split("\n") if l.startswith("tags:")))
            self.assertEqual(tags.count("_todo"), 1)

    def test_a_clean_merge_does_not_tag(self):
        """The tag has to mean something. Tagging every merged page makes the listing the
        set of pages that were ever merged, which answers nothing."""
        with TempWiki() as w:
            w.page("entities/survivor.md", title="AIPAC Full", tags=["lobbying"],
                   body=self.SURV)
            w.page("entities/loser.md", title="AIPAC", aliases=["AIPAC"], body=self.SURV)
            r = agent.merge_page("entities/loser.md", "entities/survivor.md", carry=True)
            self.assertIsNone(r["error"], r["error"])
            self.assertIsNone(r["todo_tag"])
            self.assertNotIn("_todo",
                             (w.wiki / "entities" / "survivor.md").read_text())

    def test_the_tag_survives_heal_pages(self):
        """`heal_pages` canonicalizes every page's tags at startup and after every ingest,
        so a tag it rejected would be gone within minutes of being written."""
        with TempWiki() as w:
            self._merge(w)
            agent.heal_pages()
            text = (w.wiki / "entities" / "survivor.md").read_text()
            self.assertIn("_todo", agent.parse_tags_line(
                next(l for l in text.split("\n") if l.startswith("tags:"))))


class FindableByTagSearchTest(unittest.TestCase):
    def test_the_tag_filter_finds_the_marked_pages(self):
        """`tag:_todo` through the same search the browser uses, which is the interface
        the request asked for: dig them out quickly."""
        with TempWiki() as w:
            w.page("entities/marked.md", title="Marked Page", tags=["politics", "_todo"],
                   body="## Overview\n\nA page needing a summary rewrite.\n")
            w.page("entities/clean.md", title="Clean Page", tags=["politics"],
                   body="## Overview\n\nA page that is fine.\n")
            res = agent.search_wiki_core("tag:_todo", w.wiki)
            self.assertEqual([r["rel"] for r in res["results"]],
                             ["wiki/entities/marked.md"])


if __name__ == "__main__":
    unittest.main()
