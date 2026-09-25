"""The autolinker never writes a link inside a bare URL, and unwrites the ones it did.

Found in the committed golden baseline, where it had been recorded as correct behaviour:

    See https://example.com/meta/page and Meta here.
  → See https://example.com/[meta](../entities/meta.md)/page and Meta here.

Two separate failures in one. The URL is broken — it no longer resolves to anything — and
the link points at a page the sentence was never about: "meta" there is a path segment,
not a mention of Meta the company. It also silently spent that section's first mention, so
the real mention afterwards stayed plain.

The cause is that group 1 only knew about markdown links. A bare URL is the other thing on
a page whose innards are not prose, so it now sits in group 1 too and is consumed before
any title can match inside it.

Prevention alone was not enough. Group 1 protects complete links, so a link already sitting
inside a URL is protected by the very mechanism that should have stopped it — nothing would
ever take it back out. So _autolink also heals: it unwraps markdown links found inside a
URL before it looks at anything else. That runs on every write and every relink, so the
wiki repairs itself with no separate pass.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class UrlTest(TempWikiTestCase):

    def _run(self, body):
        self.w.page("entities/meta.md", title="Meta", type="entity",
                    body="# Meta\n\nA firm.\n")
        self.w.page("entities/t.md", title="T Corp", type="entity",
                    body="# T Corp\n\n" + body)
        agent._autolink({"path": "wiki/entities/t.md"})
        return self.w.disk("entities/t.md")

    def test_a_title_inside_a_bare_url_is_not_linked(self):
        out = self._run("## Overview\n\nSee https://example.com/meta/page for details.\n")
        self.assertIn("https://example.com/meta/page", out)
        self.assertNotIn("](../entities/meta.md)", out)

    def test_a_url_already_mangled_is_healed(self):
        out = self._run("## Overview\n\nSee "
                        "https://example.com/[meta](../entities/meta.md)/page here.\n")
        self.assertIn("https://example.com/meta/page", out, out)

    def test_healing_does_not_cost_the_sections_real_mention(self):
        # The mangled link used to be the section's "first mention", so the sentence's
        # actual mention of the company stayed plain.
        out = self._run("## Overview\n\nSee https://example.com/meta/page and Meta here.\n")
        self.assertIn("https://example.com/meta/page", out)
        self.assertIn("and [Meta](../entities/meta.md) here", out, out)

    def test_a_real_markdown_link_to_a_url_is_untouched(self):
        out = self._run("## Overview\n\nPer [the writeup](https://example.com/meta/x).\n")
        self.assertIn("[the writeup](https://example.com/meta/x)", out, out)

    def test_a_url_containing_parentheses_is_left_alone(self):
        out = self._run("## Overview\n\nSee "
                        "https://en.wikipedia.org/wiki/Meta_(company) for more.\n")
        self.assertIn("https://en.wikipedia.org/wiki/Meta_(company)", out, out)

    def test_an_angle_bracket_autolink_is_left_alone(self):
        out = self._run("## Overview\n\nSee <https://example.com/meta> for more.\n")
        self.assertIn("<https://example.com/meta>", out, out)

    def test_a_title_after_a_url_still_links(self):
        # The URL must stop at whitespace, not swallow what follows it.
        out = self._run("## Overview\n\nSee https://example.com/x and Meta said so.\n")
        self.assertIn("[Meta](../entities/meta.md) said so", out, out)

    def test_ftp_and_http_are_both_recognised(self):
        out = self._run("## Overview\n\nAt http://example.com/meta/a and "
                        "ftp://example.com/meta/b.\n")
        self.assertIn("http://example.com/meta/a", out)
        self.assertIn("ftp://example.com/meta/b", out)
        self.assertNotIn("](../entities/meta.md)", out)

    def test_it_converges(self):
        out = self._run("## Overview\n\nSee "
                        "https://example.com/[meta](../entities/meta.md)/page and Meta.\n")
        agent._autolink({"path": "wiki/entities/t.md"})
        self.assertEqual(self.w.disk("entities/t.md"), out, "autolink does not converge")

    def test_a_source_pages_url_frontmatter_survives(self):
        # wiki/sources/ pages carry the article URL in frontmatter and in ## Sources; a
        # title appearing in that path is the commonest way this bug shows up in practice.
        self.w.page("entities/meta.md", title="Meta", type="entity",
                    body="# Meta\n\nA firm.\n")
        p = self.w.wiki / "sources" / "meta-2026-report.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('---\ntitle: "Meta 2026 Report"\ntype: source\n'
                     'url: "https://example.com/meta/2026-report"\ntags: []\n'
                     'created: 2026-01-01\nupdated: 2026-01-01\nsources: []\n---\n\n'
                     '# Meta 2026 Report\n\n## Summary\n\nA report.\n', encoding="utf-8")
        agent._autolink({"path": "wiki/sources/meta-2026-report.md"})
        self.assertIn('url: "https://example.com/meta/2026-report"',
                      p.read_text(encoding="utf-8"))


class BareRelativePathTest(TempWikiTestCase):
    """The same hazard as a bare URL, without a scheme — and the one that actually bit.

    A source page said "Adapted from ../sources/backgammon-wikipedia.md" and the
    autolinker linked the titles it found inside the path:

        ../sources/[backgammon](../sources/backgammon-wikipedia.md)-[wikipedia](../concepts/wikipedia.md).md

    _BARE_URL covered http:// and ftp://; a relative wiki path is just as much not-prose,
    and it is far commoner in this wiki because every page's own links look like one.

    Worse than a one-off: once a link is sitting in there, group 1 protects it, so the
    damage is permanent unless something unwraps it. It renders as a sentence with two
    plausible-looking links, so it reads fine and only /wiki/lint ever notices.
    """

    def _run(self, body, extra=()):
        self.w.page("concepts/wikipedia.md", title="Wikipedia", type="concept",
                    body="# Wikipedia\n\nAn encyclopedia.\n")
        self.w.page("sources/backgammon-wikipedia.md", title="Backgammon", type="source",
                    body="# Backgammon\n\n## Summary\n\nA game.\n")
        for rel, title in extra:
            self.w.page(rel, title=title, type="concept", body=f"# {title}\n\nX.\n")
        self.w.page("sources/backgammon-rules.md", title="Backgammon Rules", type="source",
                    body="# Backgammon Rules\n\n## Summary\n\n" + body)
        agent._autolink({"path": "wiki/sources/backgammon-rules.md"})
        return self.w.disk("sources/backgammon-rules.md")

    def test_a_bare_relative_path_is_not_linked_inside(self):
        out = self._run("See ../sources/backgammon-wikipedia.md here.\n")
        self.assertIn("See ../sources/backgammon-wikipedia.md here.", out, out)

    def test_an_already_mangled_path_is_healed(self):
        out = self._run(
            "See ../sources/[backgammon](../sources/backgammon-wikipedia.md)"
            "-[wikipedia](../concepts/wikipedia.md).md here.\n")
        self.assertIn("See ../sources/backgammon-wikipedia.md here.", out, out)

    def test_healing_is_stable(self):
        out = self._run(
            "See ../sources/[backgammon](../sources/backgammon-wikipedia.md)"
            "-[wikipedia](../concepts/wikipedia.md).md here.\n")
        for _ in range(3):
            agent._autolink({"path": "wiki/sources/backgammon-rules.md"})
        self.assertEqual(self.w.disk("sources/backgammon-rules.md"), out)


    def test_a_nested_path_recovers_the_innermost_target(self):
        """The mangling applies to its own output, so it nests:

            X0   = ../sources/backgammon-wikipedia.md
            Xk+1 = ../sources/[backgammon](Xk)-[wikipedia](../concepts/wikipedia.md).md

        One real page reached twenty-eight layers. The original path is still in there,
        innermost, and it is the FIRST complete path the scan meets — every outer
        "../sources/" is immediately followed by "[", so only the innermost matches a path
        pattern at all. Taking the LAST match returns "../concepts/wikipedia.md", a
        different page; that shipped briefly and the two-title test above caught it.
        """
        x = "../sources/backgammon-wikipedia.md"
        for _ in range(5):
            x = ("../sources/[backgammon](" + x
                 + ")-[wikipedia](../concepts/wikipedia.md).md")
        out = self._run(f"## Overview\n\nSee {x} here.\n")
        recovered = out.split("See ")[1]
        self.assertTrue(recovered.startswith("../sources/backgammon-wikipedia.md"),
                        f"the innermost path was not what came back: {recovered[:80]}")
        # What it does NOT claim: that the line is clean afterwards. Each layer appended a
        # real "-wikipedia.md", so unwinding leaves those behind and no pass can know they
        # were fabricated. Recovering the target is the most that is knowable here; the
        # rest is a human edit. Prevention is what matters, and that is _BARE_PATH.

    def test_flattening_is_still_used_for_a_url(self):
        # The scheme branch must NOT take the innermost path: there the display text is
        # the segment it replaced, so unwrapping restores the URL exactly.
        out = self._run("## Overview\n\nSee "
                        "https://example.com/[meta](../entities/meta.md)/page here.\n")
        self.assertIn("https://example.com/meta/page", out, out)

    def test_a_path_without_directories_is_also_protected(self):
        out = self._run("Adapted from backgammon-wikipedia.md in the archive.\n",
                        extra=(("concepts/archive.md", "Archive"),))
        self.assertIn("from backgammon-wikipedia.md in", out, out)

    def test_a_real_link_whose_target_is_a_path_still_works(self):
        out = self._run("See [the article](../sources/backgammon-wikipedia.md) here.\n")
        self.assertIn("[the article](../sources/backgammon-wikipedia.md)", out, out)

    def test_prose_around_the_path_still_links_normally(self):
        out = self._run("Wikipedia says so; see ../sources/backgammon-wikipedia.md.\n")
        self.assertIn("[Wikipedia](../concepts/wikipedia.md) says so", out, out)
        self.assertIn("see ../sources/backgammon-wikipedia.md.", out, out)


class MalformedLinkIsConsumedWholeTest(TempWikiTestCase):
    """Group 1 must swallow "[[a](b)](c)" entirely, or it feeds the interior back.

    The growth engine behind a page that reached twenty-eight layers of nesting. The plain
    pattern "\\[[^\\]]*\\]\\([^)]*\\)" mis-parses a link whose display text is itself a
    link: it consumes "[[backgammon](../concepts/backgammon.md)" and leaves
    "](../sources/backgammon-wikipedia.md)" behind. The scanner then meets that path as
    ordinary text, links the title inside it, and the whole thing gains a layer — every
    pass, forever.

    Two independent protections cover it now: _BARE_PATH stops a path being linked into
    at all, and this stops group 1 handing an interior to group 2 in the first place.
    Verified independently, with the other disabled.

    Whatever writes the malformed link — and that is still unknown — it must not be able
    to compound.
    """

    MALFORMED = ("- The first moves of a [[backgammon](../concepts/backgammon.md)]"
                 "(../sources/backgammon-wikipedia.md)\n")

    def _wiki(self, body):
        for slug, t, ty in (("concepts/backgammon.md", "Backgammon", "concept"),
                            ("concepts/wikipedia.md", "Wikipedia", "concept")):
            self.w.page(slug, title=t, type=ty, body=f"# {t}\n\n## Definition\n\nX.\n")
        self.w.page("sources/backgammon-wikipedia.md",
                    title="Backgammon Wikipedia Article", type="source",
                    body="# X\n\n## Summary\n\nX.\n")
        self.w.page("sources/bot.md", title="BOT", type="source",
                    body="# BOT\n\n## Summary\n\n" + body)
        agent._autolink({"path": "wiki/sources/bot.md"})
        return next(l for l in self.w.disk("sources/bot.md").splitlines()
                    if l.startswith("- The first"))

    def test_the_interior_path_is_not_mangled(self):
        self.assertIn("(../sources/backgammon-wikipedia.md)", self._wiki(self.MALFORMED))

    def test_it_does_not_gain_a_layer(self):
        before = len(self.MALFORMED.rstrip("\n"))
        self.assertEqual(len(self._wiki(self.MALFORMED)), before,
                         "the malformed link grew — group 1 handed its interior back")

    def test_it_stays_stable_across_passes(self):
        once = self._wiki(self.MALFORMED)
        for _ in range(4):
            agent._autolink({"path": "wiki/sources/bot.md"})
        self.assertEqual(
            next(l for l in self.w.disk("sources/bot.md").splitlines()
                 if l.startswith("- The first")), once)

    def test_the_pattern_consumes_the_whole_malformed_link(self):
        import re as _re
        rx = _re.compile(agent._LINK_G1)
        bad = "[[backgammon](../concepts/backgammon.md)](../sources/backgammon-wikipedia.md)"
        self.assertEqual(rx.search(bad).group(0), bad,
                         "group 1 left part of the malformed link exposed")

    def test_ordinary_links_still_parse(self):
        import re as _re
        rx = _re.compile(agent._LINK_G1)
        self.assertEqual([m.group(0) for m in rx.finditer("[a](x) and [b](y)")],
                         ["[a](x)", "[b](y)"])
        # A legitimate bracketed display text, which the old pattern could not handle
        # either and which must not be split.
        self.assertEqual(rx.search("[text with [brackets]](u.md)").group(0),
                         "[text with [brackets]](u.md)")


if __name__ == "__main__":
    unittest.main()
