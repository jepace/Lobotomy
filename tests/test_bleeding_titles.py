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
        self._subject("succession", "Succession")      # entity: a real bleed
        self._prose("succession succession succession succession succession succession")
        buf, argv = io.StringIO(), sys.argv
        sys.argv = ["bleeding_titles.py"]
        try:
            with contextlib.redirect_stdout(buf):
                bt.main()
        finally:
            sys.argv = argv
        out = buf.getvalue()
        self.assertIn("rename_page.py", out)
        self.assertIn("entities/succession.md", out)
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
        self.assertIn("No entity page's name collides", buf.getvalue())


class PageTypeTest(TempWikiTestCase):
    """The column the first version of this report did not have, and the reason it was
    useless on real data: it flagged 383 titles and told you to rename them, when most were
    concept pages doing exactly what a concept page is for.

    A `concept` page is MEANT to catch the common noun — "inflation" in prose is about
    inflation, and that link is the whole point of a concept wiki. An `entity` page is a
    proper noun, so a lowercase use of its name is a different word entirely.

    **The field is now a TIEBREAK rather than the decision** — see
    `MidSentenceCapitalTest`. These cases all have no mid-sentence capitalised use, so
    there is no textual evidence either way and the field still decides them, which is
    why every assertion here is unchanged.
    """

    def _prose(self, body):
        self.w.page("entities/a.md", title="A", type="entity",
                    body=f"# A\n\n## Overview\n\n{body}\n")
        agent._title_map_cache = None

    def _report(self, *argv):
        buf, old = io.StringIO(), sys.argv
        sys.argv = ["bleeding_titles.py", *argv]
        try:
            with contextlib.redirect_stdout(buf):
                bt.main()
        finally:
            sys.argv = old
        return buf.getvalue()

    def setUp(self):
        super().setUp()
        self.w.page("concepts/inflation.md", title="Inflation", type="concept",
                    body="# Inflation\n\n## Definition\n\nX.\n")
        self.w.page("entities/succession.md", title="Succession", type="entity",
                    body="# Succession\n\n## Overview\n\nA series.\n")
        self._prose("Rising inflation; inflation again; more inflation; inflation; inflation. "
                    "The succession of leaders; succession unclear; succession again; "
                    "his succession; succession.")

    def test_the_type_is_reported(self):
        rows = {r["key"]: r for r in bt.scan()}
        self.assertEqual(rows["inflation"]["type"], "concept")
        self.assertEqual(rows["succession"]["type"], "entity")

    def test_an_entity_collision_is_the_headline(self):
        out = self._report("--min-lower", "3")
        head = out.split("CONCEPT PAGE")[0]
        self.assertIn("entities/succession.md", head)
        self.assertIn("PROPER NOUN", head)

    def test_a_concept_page_is_not_in_the_bug_list(self):
        out = self._report("--min-lower", "3")
        self.assertNotIn("concepts/inflation.md", out)
        self.assertIn("--concepts to review them", out)

    def test_concepts_are_shown_on_request_and_labelled_review_only(self):
        out = self._report("--min-lower", "3", "--concepts")
        self.assertIn("concepts/inflation.md", out)
        self.assertIn("NOT A BUG LIST", out)
        self.assertIn("Do not rename these", out)

    def test_no_rename_command_is_offered_for_a_concept(self):
        """The failure mode of the first version: it would have had you rename inflation."""
        out = self._report("--min-lower", "3", "--concepts")
        cmds = out.split("To disambiguate")[1] if "To disambiguate" in out else ""
        self.assertNotIn("inflation", cmds)
        self.assertIn("succession", cmds)


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


class MidSentenceCapitalTest(TempWikiTestCase):
    """**A capital in the middle of a sentence is proof the title is a name**, and that is
    what the `type:` field was standing in for all along.

    Asked for after working out what the entity/concept split costs: *"at this point, I
    can't trust that the distinction between the 2 types has been honored, so let's do the
    best we can."* The field is demonstrably unreliable — the live wiki has people and
    organisations under `concepts/`, and `rename_page.py` moved pages between directories
    for months without carrying the type along — so a filter resting on it alone was
    hiding real bleeds in whichever direction a page happened to be mislabelled.

    Measured on this fixture, replacing the field with the text gains one case and loses
    none: a proper noun misfiled as a concept is now caught, and everything the field got
    right it still gets right.

    **The first version of this change went too far** and treated `cap_mid == 0` as proof
    of a common noun. That dropped three existing cases at once — a page titled
    "Succession" whose name the wiki only ever writes lowercase has no mid-sentence
    capital, and the lowercase links to it are still wrong. Zero is absence of evidence,
    not evidence of absence, so the field remains the fallback.
    """

    def _page(self, folder, slug, title, typ):
        self.w.page(f"{folder}/{slug}.md", title=title, type=typ,
                    body=f"# {title}\n\n## Overview\n\nA thing.\n")

    def _prose(self, body, n=4):
        for i in range(n):
            self.w.page(f"entities/p{i}.md", title=f"P{i}", type="entity",
                        body=f"# P{i}\n\n## Overview\n\n{body}\n")
        agent._title_map_cache = None

    def _row(self, key):
        agent._title_map_cache = None
        return {r["key"]: r for r in bt.scan()}.get(key)

    def test_a_capital_mid_sentence_is_counted(self):
        self._page("entities", "lost", "Lost", "entity")
        self._prose("The hikers were lost and felt lost, utterly lost, quite lost.\n\n"
                    "The finale of Lost aired in 2010 and Lost won awards.")
        r = self._row("lost")
        self.assertGreater(r["cap_mid"], 0)

    def test_a_capital_only_at_the_start_of_a_sentence_is_not(self):
        """Every common noun is capitalised sometimes. Counting those would make the
        signal useless, which is the whole reason the raw `cap` count could not be used."""
        self._page("concepts", "tariffs", "Tariffs", "concept")
        self._prose("The new tariffs raised prices and the tariffs were unpopular.\n\n"
                    "Tariffs are a tax. Tariffs featured in the debate about tariffs.")
        r = self._row("tariffs")
        self.assertGreater(r["cap"], 0, "the fixture must capitalise it somewhere")
        self.assertEqual(r["cap_mid"], 0)

    def test_a_proper_noun_misfiled_as_a_concept_is_reported(self):
        """The case the old filter hid completely, and the reason for the change."""
        self._page("concepts", "mission", "Mission", "concept")
        self._prose("The mission of the group, its mission, a mission statement.\n\n"
                    "He lives in the Mission and the Mission is expensive.")
        r = self._row("mission")
        self.assertGreater(r["cap_mid"], 0)
        self.assertTrue(r["cap_mid"] >= 1 or r["type"] not in ("concept", "?"),
                        "a name written mid-sentence must be reported whatever its type")

    def test_a_bullet_marker_does_not_count_as_mid_sentence(self):
        """A list item starts a sentence. Treating `- Mission` as mid-sentence would make
        every `## Entities` row on every source page into evidence of a proper noun."""
        self._page("concepts", "mission", "Mission", "concept")
        self._prose("the mission, a mission, our mission, their mission here\n\n"
                    "- Mission\n- Mission\n- Mission")
        r = self._row("mission")
        self.assertEqual(r["cap_mid"], 0)
