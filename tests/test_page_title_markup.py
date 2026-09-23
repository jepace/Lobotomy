"""The wiki page view prints the page's title as a title, above the content.

The name used to live only in the sticky toolbar, wedged between a back arrow and five
buttons — on a phone the flex:1 label was squeezed to nothing, so a page often showed no
title at all, and on a desktop it read as a toolbar label rather than as the article's
heading. The file's own `# {title}` could not supply it either: `render_md` strips the H1
precisely because the template was supposed to be printing the name itself.

Two things have to stay true together, and they are in different files:

  - `wiki.html` prints the title inside `.wiki-content`, above the rendered body.
  - `render_md` keeps stripping the file's H1.

Drop the first and the page loses its title. Drop the second and every page shows its name
twice. This checks the template half; `test_page_h1.py` covers the stripping.

Markup assertions are weak tests and this file is deliberately small. It exists because
the CSS half of this change was wrong in a way nothing caught: `.wiki-page-title` is less
specific than base.html's `.wiki-content h1`, so the font-size was silently ignored and
the title rendered at section size. That is only visible in a browser — what is checkable
here is that the selector stayed qualified.
"""
import re
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "tools" / "templates"


class WikiPageTitleMarkup(unittest.TestCase):

    def setUp(self):
        self.html = (TEMPLATES / "wiki.html").read_text(encoding="utf-8")

    def test_the_title_is_printed_inside_the_content_block(self):
        m = re.search(r'<div class="wiki-content">(.*?)</div>', self.html, re.DOTALL)
        self.assertIsNotNone(m, "the .wiki-content block moved or was renamed")
        block = m.group(1)
        self.assertIn("wiki-page-title", block,
                      "the page title is no longer rendered above the content")
        self.assertLess(block.index("wiki-page-title"), block.index("content | safe"),
                        "the title is printed below the body it titles")

    def test_the_title_style_stays_qualified_by_wiki_content(self):
        # base.html's `.wiki-content h1` outranks a bare `.wiki-page-title`, so an
        # unqualified selector loses every property they both set — which is how this
        # shipped once, rendering the title at the same size as an `##` heading.
        self.assertRegex(self.html, r"\.wiki-content\s+h1\.wiki-page-title\s*\{",
                         "the page-title rule lost its .wiki-content qualifier and is "
                         "now outranked by base.html")

    def test_the_toolbar_keeps_a_copy_for_when_the_title_scrolls_away(self):
        self.assertIn('id="topbar-title"', self.html)
        self.assertIn('id="page-title"', self.html)
        self.assertIn("IntersectionObserver", self.html,
                      "nothing hides the toolbar copy while the real title is on screen")

    def test_the_toolbar_copy_is_visible_by_default(self):
        # It is hidden by a class the script adds. If the script never runs the name shows
        # twice, which is the right way for this to fail.
        self.assertRegex(self.html, r"\.topbar-title\.hidden\s*\{[^}]*opacity:\s*0",
                         "the toolbar copy is hidden by default — with no JS the page "
                         "would have no title in the bar at all")
        self.assertNotRegex(
            self.html, r"<span class=\"topbar-title hidden\"",
            "the toolbar copy ships pre-hidden, so it depends on JS to ever appear")


if __name__ == "__main__":
    unittest.main()
