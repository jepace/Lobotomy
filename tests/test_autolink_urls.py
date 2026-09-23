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


if __name__ == "__main__":
    unittest.main()
