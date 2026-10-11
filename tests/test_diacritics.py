"""An accented letter and its base are the same letter for matching.

Reported as *"ingest wanted Peter but page is Péter Magyar. I added the alias, but maybe
there's a better way?"* There is, and the alias was the wrong tool twice over: it has to be
added by hand for every accented name in a growing wiki, and `aliases:` is documented as a
human override for a name the page does **not** carry — while "Peter Magyar" is the same
name with the accent dropped the way prose drops it.

**The accent was being DELETED rather than folded, and that was not in the report.**
Measured before changing anything:

    _norm_name_key('Péter Magyar')  ->  'p ter magyar'
    _slug_for('Péter Magyar')       ->  'p-ter-magyar'

`é` is not in `[a-z0-9]`, so it became a *separator*: one name turned into two words. So
the resolver was not merely missing a match, it was comparing a mangled string — and a new
page for that subject would have been filed under a slug nobody would ever type. Three
consequences, all closed here: a duplicate page waiting to be created, a prose mention that
never links, and a filename with a hole in it.

**The prefilters are the half that makes or breaks it.** `_autolink` rejects a title whose
tokens are not in the body, and rejects a line that does not contain the title's longest
token — both literal. A title "Péter Magyar" probing for `péter` finds nothing in a line
spelling the name `Peter`, so the line is thrown out *before* the flexible pattern can
match it and the whole fix does nothing. That trap is already written down for `noah's`
against `noah’s`; this is the same trap one alphabet along, and
`PrefilterTest` is here because the first version of this change had exactly that bug — the
regex matched and no link appeared.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWiki

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class FoldTest(unittest.TestCase):
    def test_the_reported_name(self):
        self.assertEqual(agent._fold_diacritics("Péter Magyar"), "Peter Magyar")

    def test_combining_marks_across_alphabets(self):
        for before, after in [
            ("Zoë", "Zoe"), ("Ångström", "Angstrom"), ("Besançon", "Besancon"),
            ("Łódź", "Lodz"), ("Škoda", "Skoda"), ("Nuñez", "Nunez"),
            ("İstanbul", "Istanbul"), ("Dvořák", "Dvorak"),
        ]:
            with self.subTest(before=before):
                self.assertEqual(agent._fold_diacritics(before), after)

    def test_letters_with_no_combining_mark_need_their_own_table(self):
        """NFKD decomposes é into "e" plus a mark, but ø, ł, đ, ß and æ carry no mark at
        all and come through unchanged — so a fold built on NFKD alone silently misses
        every Scandinavian and Polish name."""
        for before, after in [("Ø", "O"), ("ø", "o"), ("ß", "ss"),
                              ("æ", "ae"), ("Æ", "AE"), ("þ", "th"), ("ð", "d")]:
            with self.subTest(before=before):
                self.assertEqual(agent._fold_diacritics(before), after)

    def test_case_is_left_alone(self):
        """Callers lowercase for their own reasons; folding case here as well would hide
        which of them needed it."""
        self.assertEqual(agent._fold_diacritics("PÉTER"), "PETER")
        self.assertEqual(agent._fold_diacritics("Péter"), "Peter")

    def test_plain_ascii_is_returned_unchanged(self):
        for s in ("Peter Magyar", "", "a-b c.d", "123"):
            with self.subTest(s=s):
                self.assertEqual(agent._fold_diacritics(s), s)

    def test_non_latin_text_is_not_mangled(self):
        """Folding is about Latin accents. A script with no ASCII base must come through
        intact rather than losing characters to the decomposition."""
        for s in ("Киев", "北京", "العربية", "Ελλάδα"):
            with self.subTest(s=s):
                self.assertEqual(agent._fold_diacritics(agent._fold_diacritics(s)),
                                 agent._fold_diacritics(s))
                self.assertTrue(agent._fold_diacritics(s))

    def test_it_is_idempotent(self):
        for s in ("Péter Magyar", "Łódź", "ß", "Zoë"):
            with self.subTest(s=s):
                once = agent._fold_diacritics(s)
                self.assertEqual(agent._fold_diacritics(once), once)


class NormKeyAndSlugTest(unittest.TestCase):
    def test_the_comparison_key_folds(self):
        self.assertEqual(agent._norm_name_key("Péter Magyar"),
                         agent._norm_name_key("Peter Magyar"))

    def test_the_accent_is_not_treated_as_a_separator(self):
        """The measured defect: `[^a-z0-9]+` turned one name into two words."""
        self.assertEqual(agent._norm_name_key("Péter Magyar"), "peter magyar")
        self.assertNotIn("p ter", agent._norm_name_key("Péter Magyar"))

    def test_the_slug_folds(self):
        self.assertEqual(agent._slug_for("Péter Magyar"), "peter-magyar")
        self.assertEqual(agent._slug_for("Łódź"), "lodz")

    def test_distinct_names_still_differ(self):
        """Folding must not collapse subjects that are genuinely different."""
        self.assertNotEqual(agent._norm_name_key("Péter Magyar"),
                            agent._norm_name_key("Peter Magyari"))
        self.assertNotEqual(agent._norm_name_key("Zoe Saldana"),
                            agent._norm_name_key("Zoe Kravitz"))


class ResolvePageTest(unittest.TestCase):
    """`_resolve_page` is the one place that decides create-vs-update, and `lookup_titles`,
    `done()`'s worklist and `create_file`'s duplicate check all share it — so this gap
    would have produced a second page for the same politician, and every caller would have
    been wrong the same way at once."""

    def _resolve(self, name):
        return agent._resolve_page(
            name, {t.lower(): rel for t, rel in agent._build_title_map()})

    def test_the_unaccented_spelling_finds_the_accented_page(self):
        with TempWiki() as w:
            w.page("entities/peter-magyar.md", title="Péter Magyar",
                   body="## Overview\n\nA politician.\n")
            self.assertEqual(self._resolve("Peter Magyar"), "entities/peter-magyar.md")

    def test_and_the_other_direction(self):
        with TempWiki() as w:
            w.page("entities/peter-magyar.md", title="Peter Magyar",
                   body="## Overview\n\nA politician.\n")
            self.assertEqual(self._resolve("Péter Magyar"), "entities/peter-magyar.md")

    def test_a_page_already_filed_under_the_mangled_slug_still_resolves(self):
        """**The compatibility case.** Pages created before this fix are on disk as
        `p-ter-magyar.md`, because the accent split the slug. Folding `_slug_for` means the
        filename fallback no longer matches them — so the title key has to carry it, or
        this change would break resolution for exactly the pages that motivated it."""
        with TempWiki() as w:
            w.page("entities/p-ter-magyar.md", title="Péter Magyar",
                   body="## Overview\n\nA politician.\n")
            self.assertEqual(self._resolve("Peter Magyar"), "entities/p-ter-magyar.md")
            self.assertEqual(self._resolve("Péter Magyar"), "entities/p-ter-magyar.md")

    def test_create_file_refuses_the_duplicate(self):
        """What the gap would have cost: a second page for one subject."""
        with TempWiki() as w:
            w.page("entities/peter-magyar.md", title="Péter Magyar",
                   body="## Overview\n\nA politician.\n")
            agent.init_session()
            out = agent.TOOL_FNS["create_file"]({
                "path": "wiki/entities/peter-magyar-politician.md",
                "title": "Peter Magyar", "type": "entity",
                "body": "## Overview\n\nA politician.\n",
            })
            self.assertIn("Error", out, out)
            self.assertIn("peter-magyar.md", out)

    def test_lookup_titles_routes_it_to_update(self):
        with TempWiki() as w:
            w.page("entities/peter-magyar.md", title="Péter Magyar",
                   body="## Overview\n\nA politician.\n")
            agent.init_session()
            out = agent._lookup_titles({"names": ["Peter Magyar"]})
            self.assertIn("peter-magyar.md", out)


class AutolinkTest(unittest.TestCase):
    """The half the alias was standing in for."""

    def _link_line(self, page_title, text):
        with TempWiki() as w:
            w.page("entities/peter-magyar.md", title=page_title,
                   body="## Overview\n\nA politician.\n")
            p = w.page("entities/other.md", title="Other Page",
                       body=f"## Overview\n\n{text}\n")
            agent._autolink_now(p)
            return p.read_text().split("## Overview", 1)[1].strip().split("\n")[0]

    def test_prose_without_the_accent_links_to_the_accented_page(self):
        got = self._link_line("Péter Magyar", "Peter Magyar leads the party.")
        self.assertEqual(got, "[Peter Magyar](../entities/peter-magyar.md) "
                              "leads the party.")

    def test_prose_with_the_accent_links_to_the_plain_page(self):
        got = self._link_line("Peter Magyar", "Péter Magyar leads the party.")
        self.assertIn("](../entities/peter-magyar.md)", got)

    def test_the_display_text_keeps_what_the_PROSE_wrote(self):
        """Not the title's spelling. Rewriting an author's punctuation is not the linker's
        job — the same rule the apostrophe family already follows."""
        got = self._link_line("Péter Magyar", "Peter Magyar leads the party.")
        self.assertIn("[Peter Magyar]", got)
        self.assertNotIn("[Péter Magyar]", got)

    def test_case_insensitive_still(self):
        got = self._link_line("Péter Magyar", "PETER MAGYAR leads the party.")
        self.assertIn("[PETER MAGYAR](../entities/peter-magyar.md)", got)

    def test_a_different_name_is_not_linked(self):
        got = self._link_line("Péter Magyar", "Pete Magyar leads the party.")
        self.assertNotIn("](", got)

    def test_the_whole_family_matches(self):
        """One generated table rather than a hand-written list, so the next alphabet costs
        no code. Each of these is a page title met by prose spelling it the plain way."""
        for title, prose in [("Łódź", "Lodz"), ("Besançon", "Besancon"),
                             ("Škoda Auto", "Skoda Auto"), ("Dvořák", "Dvorak"),
                             ("Nuñez", "Nunez"), ("Ångström", "Angstrom")]:
            with self.subTest(title=title):
                with TempWiki() as w:
                    w.page("entities/x.md", title=title, body="## Overview\n\nA thing.\n")
                    p = w.page("entities/other.md", title="Other",
                               body=f"## Overview\n\nThe {prose} report is out.\n")
                    agent._autolink_now(p)
                    self.assertIn("x.md)", p.read_text(),
                                  f"{prose!r} did not link to a page titled {title!r}")

    def test_the_multi_letter_folds_go_one_direction_only(self):
        """A title carrying ß may be written "ss" in the text. The reverse is NOT offered:
        letting a title's "ss" match a ß would make "Essen" match "Eßen", and a title's
        plain letters must not start matching ligatures nobody wrote."""
        self.assertIn("ss", agent._esc_flex("ß"))
        self.assertNotIn("ß", agent._esc_flex("ss"))

    def test_an_alias_is_no_longer_needed_and_does_no_harm(self):
        """The user's workaround. Both names now match the same text, and the
        once-per-section budget is keyed on (target page, section ordinal) — shared across
        every name for a page — so the alias cannot produce a second link in one
        paragraph. It is redundant, not harmful."""
        with TempWiki() as w:
            w.page("entities/peter-magyar.md", title="Péter Magyar",
                   aliases=["Peter Magyar"], body="## Overview\n\nA politician.\n")
            p = w.page("entities/other.md", title="Other",
                       body="## Overview\n\nPeter Magyar leads it. Peter Magyar said so.\n")
            agent._autolink_now(p)
            body = p.read_text().split("## Overview", 1)[1]
            self.assertEqual(body.count("](../entities/peter-magyar.md)"), 1,
                             "the alias and the title both spent the section's mention")


class TwoNamesForOnePageTest(unittest.TestCase):
    """**A latent bug this change surfaced, worth its own name because it has nothing to do
    with accents.** The pass that writes a link marks the section's mention spent; a later
    pass for another name of the same page then meets that link at group 1, reads "mention
    already spent", and strips it as a repeat — leaving NO link where there had been one.

    It needs two names whose `\\w+` tokens are exactly equal, which an alias rarely is
    ("Lancet" vs "The Lancet", "PG&E" vs "PG and E" all differ) — so it sat there. Folding
    made "Péter Magyar" and "Peter Magyar" token-identical and the reported fix produced a
    page with no link at all.

    A reordered alias is the version that was always reachable, and it is the first case
    below."""

    def _one_link(self, title, aliases, text):
        with TempWiki() as w:
            w.page("entities/subject.md", title=title, aliases=aliases,
                   body="## Overview\n\nA politician.\n")
            p = w.page("entities/other.md", title="Other",
                       body=f"## Overview\n\n{text}\n")
            agent._autolink_now(p)
            body = p.read_text().split("## Overview", 1)[1]
            return body.count("subject.md)"), body

    def test_a_reordered_alias_does_not_unlink_the_title(self):
        """No diacritics involved: "Magyar Peter" has exactly the tokens of
        "Peter Magyar", so this was reachable before this change."""
        n, body = self._one_link("Peter Magyar", ["Magyar Peter"],
                                 "Peter Magyar leads the party.")
        self.assertEqual(n, 1, f"expected one link, got {n}: {body!r}")

    def test_the_accented_pair_keeps_its_link(self):
        n, body = self._one_link("Péter Magyar", ["Peter Magyar"],
                                 "Peter Magyar leads the party.")
        self.assertEqual(n, 1, f"expected one link, got {n}: {body!r}")

    def test_a_genuine_repeat_in_the_same_section_is_still_unlinked(self):
        """The credit must not become a licence to keep two links: it is spent by the
        first group-1 encounter, so a second mention on the same line still loses its
        link. This is the rule the whole mechanism exists to enforce."""
        with TempWiki() as w:
            w.page("entities/subject.md", title="Péter Magyar", aliases=["Peter Magyar"],
                   body="## Overview\n\nA politician.\n")
            p = w.page("entities/other.md", title="Other",
                       body="## Overview\n\nPeter Magyar leads it, and "
                            "[Peter Magyar](../entities/subject.md) said so.\n")
            agent._autolink_now(p)
            body = p.read_text().split("## Overview", 1)[1]
            self.assertEqual(body.count("subject.md)"), 1,
                             f"the section kept more than one link: {body!r}")

    def test_each_section_gets_its_own_link(self):
        """The credit is keyed per line, so it cannot leak into the next section and
        suppress the link there."""
        with TempWiki() as w:
            w.page("entities/subject.md", title="Péter Magyar", aliases=["Peter Magyar"],
                   body="## Overview\n\nA politician.\n")
            p = w.page("entities/other.md", title="Other",
                       body="## Overview\n\nPeter Magyar leads it.\n\n"
                            "## Background\n\nPeter Magyar founded it.\n")
            agent._autolink_now(p)
            self.assertEqual(p.read_text().count("subject.md)"), 2)

    def test_a_list_row_still_always_links(self):
        with TempWiki() as w:
            w.page("entities/subject.md", title="Péter Magyar", aliases=["Peter Magyar"],
                   body="## Overview\n\nA politician.\n")
            p = w.page("sources/x-2026-y.md", title="A Source", type="source",
                       body="## Entities\n\n- Peter Magyar\n- Peter Magyar\n")
            agent._autolink_now(p)
            self.assertEqual(p.read_text().count("subject.md)"), 2)


class PrefilterTest(unittest.TestCase):
    """**The first version of this change failed here**, and failed silently: the pattern
    matched and no link appeared, because two literal prefilters sit in front of it.

    `_autolink` skips a title whose `\\w+` tokens are not all in the body, then skips each
    line that does not contain the title's longest token. Both compare literally, so
    `péter` is absent from a body spelling it `Peter` and the title never reaches the
    regex. Both sides are folded now.
    """

    def test_the_body_token_prefilter_folds_both_sides(self):
        needed = frozenset(re.findall(r"\w+", agent._fold_diacritics("Péter Magyar").lower()))
        body_tokens = set(re.findall(
            r"\w+", agent._fold_diacritics("Peter Magyar leads the party.").lower()))
        self.assertTrue(needed <= body_tokens,
                        "the title's tokens are not found in a body spelling it plainly")

    def test_the_per_line_probe_is_folded(self):
        """It is derived from the folded token set rather than recomputed — which is also
        what took the cost of this change from ~10% of a whole-page autolink to ~0, since
        it ran once per candidate title per page."""
        src = (Path(agent.__file__)).read_text(encoding="utf-8")
        i = src.index("_probe = max(")
        self.assertIn("needed", src[i:i + 120])

    def test_accented_PROSE_is_found_when_the_probe_token_carries_the_accent(self):
        """**`mutate.py` reported the line-probe fold MISSED, and it was right.** Every
        other test here writes the prose plainly, where the probe survives by luck: for
        "Péter Magyar" the longest token is `magyar`, which is ASCII and present either
        way. The uncovered case is the mirror — accented prose whose longest token IS the
        accented one. The title folds its probe to `besancon`, an unfolded line holds
        `besançon`, the substring test fails, and the line is rejected before the pattern
        can match: the accented spelling, on a page named for it, goes unlinked.

        Which is to say the probe dodged the bug for the commonest shape and not for all
        of them — the reason a mutation is worth more than a passing suite."""
        with TempWiki() as w:
            w.page("entities/besancon.md", title="Besançon",
                   body="## Overview\n\nA city in France.\n")
            p = w.page("entities/other.md", title="Other",
                       body="## Overview\n\nThe Besançon observatory opened in 1878.\n")
            agent._autolink_now(p)
            self.assertIn("besancon.md)", p.read_text(),
                          "accented prose was rejected by the per-line probe")

    def test_a_long_page_still_links_the_accented_title(self):
        """The end-to-end version, on a body long enough that the prefilters matter."""
        with TempWiki() as w:
            w.page("entities/peter-magyar.md", title="Péter Magyar",
                   body="## Overview\n\nA politician.\n")
            filler = "\n\n".join(f"Paragraph {i} about unrelated matters." for i in range(200))
            p = w.page("entities/other.md", title="Other",
                       body=f"## Overview\n\n{filler}\n\nPeter Magyar spoke today.\n")
            agent._autolink_now(p)
            self.assertIn("[Peter Magyar](../entities/peter-magyar.md)", p.read_text())

    def test_the_line_cache_is_folded_where_it_is_rewritten(self):
        """`lines_lower` is refreshed in place after a substitution, and a refresh that
        forgot to fold would make the probe miss every LATER title on that line."""
        with TempWiki() as w:
            w.page("entities/peter-magyar.md", title="Péter Magyar",
                   body="## Overview\n\nA politician.\n")
            w.page("entities/fidesz.md", title="Fidesz", body="## Overview\n\nA party.\n")
            p = w.page("entities/other.md", title="Other",
                       body="## Overview\n\nPeter Magyar opposes Fidesz in Hungary.\n")
            agent._autolink_now(p)
            text = p.read_text()
            self.assertIn("](../entities/peter-magyar.md)", text)
            self.assertIn("fidesz.md)", text, "the second title on the line was lost")


if __name__ == "__main__":
    unittest.main()
