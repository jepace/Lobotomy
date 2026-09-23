"""The JavaScript embedded in the templates parses.

The templates carry real logic — the wiki search popup does keyboard navigation, the
history view rewrites what Ctrl+A selects — and none of it is reachable from the Python
suite. A syntax error there is invisible until someone opens the page and a feature is
silently dead, because a browser abandons the whole <script> block.

This does not test behaviour; it catches the one failure that costs nothing to catch.
`node --check` parses without executing. Skipped when node is not installed, so it is
optional tooling in the spirit of the rest: no new dependency, just a better check when
the machine happens to have one.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "tools" / "templates"
NODE = shutil.which("node")
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
# A Jinja expression inside JS is not JavaScript, so a template that interpolates into a
# script block cannot be parsed as-is. Substituting a literal keeps the surrounding syntax
# checkable, which is the point — "1" is valid in every position a value can appear.
JINJA_EXPR_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
JINJA_TAG_RE = re.compile(r"\{%.*?%\}", re.DOTALL)


@unittest.skipUnless(NODE, "node is not installed — template JS not syntax-checked")
class TemplateJavaScriptParses(unittest.TestCase):

    def test_every_inline_script_parses(self):
        checked = 0
        for tpl in sorted(TEMPLATES.glob("*.html")):
            text = tpl.read_text(encoding="utf-8")
            for i, block in enumerate(SCRIPT_RE.findall(text)):
                js = JINJA_TAG_RE.sub("", JINJA_EXPR_RE.sub("1", block))
                if not js.strip():
                    continue
                with self.subTest(template=tpl.name, block=i):
                    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                                     encoding="utf-8") as fh:
                        fh.write(js)
                        path = fh.name
                    try:
                        r = subprocess.run([NODE, "--check", path],
                                           capture_output=True, text=True, timeout=30)
                        self.assertEqual(r.returncode, 0,
                                         f"{tpl.name} script block {i} does not parse:\n"
                                         f"{r.stderr.strip()}")
                    finally:
                        Path(path).unlink(missing_ok=True)
                checked += 1
        self.assertGreater(checked, 0, "no inline script blocks were found to check")

    def test_the_search_popup_script_is_among_them(self):
        # Named explicitly so deleting the search JS shows up as a failure here rather
        # than as a quietly smaller `checked` count above.
        text = (TEMPLATES / "wiki.html").read_text(encoding="utf-8")
        self.assertIn("wiki-search-results", text)
        self.assertIn("ArrowDown", text, "the search popup lost its keyboard navigation")


if __name__ == "__main__":
    unittest.main()
