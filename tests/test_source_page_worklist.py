"""Creating a source page hands back the lookup for its own Entities/Concepts lists.

Those names are the ingest's committed worklist — `done()` refuses until each has a page —
and looking them up is unavoidably the next step, so charging a round for it buys nothing.

The sharper reason is that an early lookup is over the WRONG SET by construction. From a
real log: the agent called `lookup_titles` first with seven names and was told outright that
"Patrick Lennox" had no page and not to `read_file` it. Two rounds later it did. In between
it wrote a source page listing TEN names — the lookup predated the page, so it covered a
guess, and when the agent went back to work the list it worked the page's list, which the
lookup did not correspond to.

This handback is over the committed list, produced at the moment that list comes into
existence, in the same message. Saving the round is a side benefit.

The reply is produced by `_lookup_titles` itself rather than a second implementation, so the
two cannot disagree, and it carries the per-page read route for the same reason. The CREATE
group is what prevents the blind read: it says outright that those names have no file to
read.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent

BODY = ("## Summary\n\nA summary.\n\n## Claims\n\n- A claim.\n\n"
        "## Entities\n\n- Alberta\n- Patrick Lennox\n\n## Concepts\n\n- Separatism\n")


class WorklistHandbackTest(TempWikiTestCase):

    def setUp(self):
        super().setUp()
        self.w.page("entities/alberta.md", title="Alberta", type="entity",
                    body="# Alberta\n\n## Overview\n\nA province.\n")
        agent._title_map_cache = None

    def _create(self, body=BODY, path="wiki/sources/s.md"):
        out = agent._create_file({"path": path, "title": "S", "type": "source",
                                  "tags": ["news"], "body": body})
        self.assertNotIn("Error", out.splitlines()[0], out[:200])
        return out

    def test_the_reply_carries_the_lookup(self):
        out = self._create()
        self.assertIn("UPDATE (1)", out)
        self.assertIn("CREATE (2)", out)
        self.assertIn("wiki/entities/alberta.md", out)

    def test_a_name_with_no_page_is_told_not_to_read_it(self):
        """The wasted round: read_file on a page that does not exist."""
        out = self._create()
        create_block = out.split("CREATE (2)")[1]
        self.assertIn("Patrick Lennox", create_block)
        self.assertIn("Do NOT call read_file on them first", out)

    def test_it_carries_the_read_route_too(self):
        out = self._create()
        self.assertIn("reads whole — read_file first", out)

    def test_it_says_the_lookup_is_not_needed(self):
        self.assertIn("do not need to call lookup_titles", self._create())

    def test_it_is_the_same_answer_lookup_titles_gives(self):
        """Same function, so the two cannot drift apart."""
        out = self._create()
        direct = agent._lookup_titles({"names": ["Alberta", "Patrick Lennox", "Separatism"]})
        self.assertIn(direct, out)

    def test_the_created_page_itself_is_unchanged_by_this(self):
        self._create()
        disk = self.w.disk("sources/s.md")
        self.assertIn("## Entities", disk)
        self.assertIn("Patrick Lennox", disk)
        self.assertNotIn("UPDATE (", disk, "the lookup leaked into the page")

    # -- where it must NOT appear ----------------------------------------------

    def test_an_entity_page_gets_no_worklist(self):
        out = agent._create_file({"path": "wiki/entities/x.md", "title": "X",
                                  "type": "entity", "tags": ["news"],
                                  "body": "## Overview\n\nA thing.\n"})
        self.assertNotIn("UPDATE (", out)
        self.assertNotIn("worklist", out)

    def test_a_source_page_with_no_listed_names_gets_no_worklist(self):
        """Nothing to hand back, so nothing is added."""
        out = self._create(
            body="## Summary\n\nx\n\n## Claims\n\n- A claim.\n\n## Entities\n\n"
                 "## Concepts\n\n",
            path="wiki/sources/empty.md")
        self.assertNotIn("UPDATE (", out)

    def test_the_first_line_still_reports_the_write(self):
        """Whatever is appended, the reply still opens by saying what happened."""
        first = self._create().splitlines()[0]
        self.assertTrue(first.startswith("Created wiki/sources/s.md"), first)

    def test_the_worklist_is_appended_after_the_status_line(self):
        """Not spliced into it. A source page gets no "no sources cited" warning — its
        sources: IS itself — but the suffix slot is shared, so order matters."""
        out = self._create()
        self.assertLess(out.index("(219 bytes)") if "(219 bytes)" in out else out.index("bytes)"),
                        out.index("worklist"))


if __name__ == "__main__":
    unittest.main()
