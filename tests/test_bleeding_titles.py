"""Which page titles are common words — measured from the wiki, not from a dictionary.

A page titled "Lost" links the word *lost* in "the hikers were lost for three days".
"Agency" and "Power" do the same. The autolinker matches case-insensitively, so a title that
is also an ordinary English word bleeds into every page that uses it — and the
once-per-section rule makes that worse than a stray link, because the wrong mention SPENDS
the section's one link and the genuine mention then gets nothing. Demonstrated: adding
aliases: ["Lost"] to a renamed page produced

    The hikers were [lost](../entities/lost-tv-series.md) for three days,
    and the finale of Lost aired in 2010.

— the adjective linked, the real subject bare.

No dictionary is needed to find these and none is used. A proper noun is written capitalised
wherever it appears and a common noun is written lowercase, so the wiki reports on itself.
The load-bearing assertion in this module is the NEGATIVE one: a real proper noun used many
times must not be flagged, or the report is just a list of every page and nobody reads it.
"""
import io
import contextlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent
import bleeding_titles as bt


class ScanTest(TempWikiTestCase):

    def _subject(self, slug, title, typ="entity"):
        folder = "concepts" if typ == "concept" else "entities"
        self.w.page(f"{folder}/{slug}.md", title=title, type=typ,
                    body=f"# {title}\n\n## Overview\n\nA thing.\n")

    def _prose(self, body, name="a"):
        self.w.page(f"entities/{name}.md", title=name.upper(), type="entity",
                    body=f"# {name.upper()}\n\n## Overview\n\n{body}\n")
        agent._title_map_cache = None

    def _rows(self):
        agent._title_map_cache = None
        return {r["key"]: r for r in bt.scan()}

    # -- the negative that makes the report worth reading -----------------------

    def test_a_real_proper_noun_is_not_flagged(self):
        self._subject("nvidia", "Nvidia")
        self._prose("Nvidia shipped chips. Nvidia profits doubled. Nvidia again.")
        self.assertNotIn("nvidia", self._rows())

    def test_a_title_never_used_at_all_is_not_flagged(self):
        self._subject("obscure", "Obscure")
        self._prose("Nothing here mentions it.")
        self.assertNotIn("obscure", self._rows())

    # -- the positives ----------------------------------------------------------

    def test_a_common_word_title_is_flagged(self):
        self._subject("power", "Power", typ="concept")
        self._prose("The plant supplies power. Power demand rose and power is scarce.")
        r = self._rows()["power"]
        self.assertEqual(r["bare_lower"], 2)      # two lowercase; "Power demand" is capped
        self.assertEqual(r["cap"], 1)

    def test_links_already_on_disk_are_counted_separately(self):
        """Damage done, as distinct from damage pending. They are different decisions."""
        self._subject("lost", "Lost")
        self._prose("The hikers were [lost](../entities/lost.md) for days; nothing was lost. "
                    "The finale of Lost aired in 2010.")
        r = self._rows()["lost"]
        self.assertEqual(r["linked_lower"], 1)
        self.assertEqual(r["bare_lower"], 1)
        self.assertEqual(r["cap"], 1)

    def test_a_link_is_not_counted_twice(self):
        """The bare scan runs over text with links removed, or every existing link would be
        counted once as a link and again as prose."""
        self._subject("power", "Power", typ="concept")
        self._prose("Supplies [power](../concepts/power.md) to the grid.")
        r = self._rows()["power"]
        self.assertEqual(r["linked_lower"], 1)
        self.assertEqual(r["bare_lower"], 0)

    def test_a_correctly_capitalised_link_is_not_damage(self):
        self._subject("lost", "Lost")
        self._prose("The finale of [Lost](../entities/lost.md) aired in 2010.")
        self.assertNotIn("lost", self._rows())

    def test_a_link_to_another_page_is_not_attributed_here(self):
        """Display text can coincide with a title while pointing somewhere else entirely."""
        self._subject("power", "Power", typ="concept")
        self._subject("grid", "Grid", typ="concept")
        self._prose("The [power](../concepts/grid.md) of the grid.")
        self.assertNotIn("power", self._rows())

    # -- what is deliberately not counted ---------------------------------------

    def test_headings_are_excluded(self):
        """The autolinker skips heading lines, so an occurrence there is not linkable."""
        self._subject("power", "Power", typ="concept")
        self.w.page("entities/a.md", title="A", type="entity",
                    body="# A\n\n## power struggles\n\nNothing lowercase in the prose.\n")
        self.assertNotIn("power", self._rows())

    def test_a_page_does_not_report_its_own_title(self):
        self.w.page("concepts/power.md", title="Power", type="concept",
                    body="# Power\n\n## Definition\n\nThe power to act; power in general.\n")
        self.assertNotIn("power", self._rows())

    def test_generated_pages_are_skipped(self):
        self._subject("power", "Power", typ="concept")
        (self.w.wiki / "log.md").write_text(
            "# Log\n\n- an ingest mentioning power and power again\n")
        self.assertNotIn("power", self._rows())

    # -- shape of the answer ----------------------------------------------------

    def test_the_worst_offender_sorts_first(self):
        self._subject("power", "Power", typ="concept")
        self._subject("lost", "Lost")
        self._prose("power power power power", name="a")
        self._prose("was [lost](../entities/lost.md) and [lost](../entities/lost.md) again",
                    name="b")
        agent._title_map_cache = None
        rows = bt.scan()
        self.assertEqual(rows[0]["key"], "lost",
                         "links already on disk outrank not-yet-linked prose")

    def test_multi_word_titles_are_only_checked_when_asked(self):
        self.w.page("concepts/data-center.md", title="Data Center", type="concept",
                    body="# Data Center\n\n## Definition\n\nA building.\n")
        self._prose("The data center hummed; another data center opened.")
        self.assertNotIn("data center", self._rows())
        agent._title_map_cache = None
        rows = {r["key"]: r for r in bt.scan(max_words=2)}
        self.assertIn("data center", rows)
        self.assertEqual(rows["data center"]["bare_lower"], 2)

    def test_it_writes_nothing(self):
        self._subject("power", "Power", typ="concept")
        self._prose("power power power")
        before = {p: p.read_text() for p in agent.wiki_pages()}
        bt.scan()
        self.assertEqual({p: p.read_text() for p in agent.wiki_pages()}, before)

    def test_the_report_prints_a_rename_command(self):
        self._subject("power", "Power", typ="concept")
        self._prose("power power power power power power")
        buf, argv = io.StringIO(), sys.argv
        sys.argv = ["bleeding_titles.py"]
        try:
            with contextlib.redirect_stdout(buf):
                bt.main()
        finally:
            sys.argv = argv
        out = buf.getvalue()
        self.assertIn("rename_page.py", out)
        self.assertIn("concepts/power.md", out)
        # And the caveat, because the rename alone wins no links back.
        self.assertIn("no_autolink", out)

    def test_nothing_to_report_says_so(self):
        self._subject("nvidia", "Nvidia")
        self._prose("Nvidia shipped chips.")
        buf, argv = io.StringIO(), sys.argv
        sys.argv = ["bleeding_titles.py"]
        try:
            with contextlib.redirect_stdout(buf):
                rc = bt.main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0)
        self.assertIn("No bleeding titles found", buf.getvalue())


class AliasTest(TempWikiTestCase):
    """An alias bleeds exactly like a title, and is the thing someone reaches for when a
    disambiguated rename leaves the page unlinked — so it has to be in the report."""

    def test_an_alias_that_is_a_common_word_is_reported(self):
        self.w.page("entities/lost-tv-series.md", title="Lost (TV series)", type="entity",
                    aliases=["Lost"], body="# Lost (TV series)\n\n## Overview\n\nA show.\n")
        self.w.page("entities/a.md", title="A", type="entity",
                    body="# A\n\n## Overview\n\nThe hikers were lost; nothing was lost.\n")
        agent._title_map_cache = None
        rows = {r["key"]: r for r in bt.scan()}
        self.assertIn("lost", rows)
        self.assertEqual(rows["lost"]["bare_lower"], 2)


if __name__ == "__main__":
    unittest.main()
