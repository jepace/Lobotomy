"""Code is not prose, so nothing inside it is linked.

Reported from a live page:

    The GA release introduces native CLI (`wslc.exe` /
    `[container](../concepts/container.md).exe`) and API support for building,...

A page named "Container" existed, the prose said `container.exe` inside backticks, and the
autolinker matched the word and linked it. `_BARE_PATH` could not help — it covers `.md`
only — and the general rule it is a special case of was missing: **nothing inside code is
prose, whatever the extension**.

Reproducing it found the inline case was the mild one. Nothing protected code at all:

    ```sh
    [docker](../concepts/docker.md) run --rm [python](../concepts/python.md):3
    ```

A fenced block, an indented block, a `<code>` tag — all linked. That is not cosmetic: the
reader copies that line into a terminal. A `#` inside a fence was also counted as a heading,
which advanced the section ordinal and reset the once-per-section budget mid-section.

**Prevention alone freezes the damage**, which is the lesson `_MANGLED_URL_RE` already paid
for: once a link is inside a span, group 1 protects it, so the mechanism that stops new
ones is what keeps the old ones forever. `_unlink_in_code` runs at the top of `_autolink`,
before anything else reads the text, and the pages heal on the next write or relink.

It is scoped to links the autolinker itself would have written — a relative path ending
`.md` — so a page DOCUMENTING markdown keeps `[label](https://example.com)` in its code
span exactly as its author wrote it.

**Known gap, stated rather than silently half-handled:** a 4-space *indented* code block is
not protected. Distinguishing one from a lazy continuation line under a bullet needs list
state, and getting that wrong would silently stop linking inside ordinary nested lists —
a worse failure than the one it fixes. Fenced blocks are the form these pages actually use.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class _Base(TempWikiTestCase):
    def setUp(self):
        super().setUp()
        for title, slug in (("Container", "container"), ("Docker", "docker"),
                            ("Python", "python")):
            self.w.page(f"concepts/{slug}.md", title=title, type="concept",
                        body=f"# {title}\n\n## Definition\n\nx\n")
        agent._title_map_cache = None

    def _link(self, body):
        self.w.page("entities/p.md", title="P", type="entity", body=f"# P\n\n{body}")
        agent._autolink_now(agent.WIKI_DIR / "entities/p.md")
        text = (agent.WIKI_DIR / "entities/p.md").read_text(encoding="utf-8")
        return text.split("---", 2)[2]


class InlineCodeTest(_Base):

    def test_the_reported_case(self):
        out = self._link("## Overview\n\nNative CLI (`wslc.exe` / `container.exe`) ships.\n")
        self.assertIn("`container.exe`", out)
        self.assertNotIn("[container]", out)

    def test_a_shell_command_in_a_span_is_untouched(self):
        out = self._link("## Overview\n\nRun `pip install python` first.\n")
        self.assertIn("`pip install python`", out)

    def test_a_double_backtick_span_holding_a_backtick(self):
        """CommonMark closes a span on a run of the SAME length, so this is ONE span. The
        first pattern used `[^`\\n]*` for every length, which cannot hold the inner tick —
        the span never matched and the title inside it was linked."""
        out = self._link("## Overview\n\nUse ``docker ` ps`` carefully.\n")
        self.assertIn("``docker ` ps``", out)
        self.assertNotIn("[docker]", out)

    def test_an_html_code_tag_is_untouched(self):
        out = self._link("## Overview\n\nA <code>python</code> tag.\n")
        self.assertIn("<code>python</code>", out)

    def test_prose_on_the_same_line_still_links(self):
        """Protecting code must not protect the sentence around it."""
        out = self._link("## Overview\n\nThe `docker` flag runs a container.\n")
        self.assertIn("`docker`", out)
        self.assertIn("[container](../concepts/container.md)", out)


class FencedBlockTest(_Base):

    def test_a_backtick_fence_is_untouched(self):
        out = self._link("## Install\n\n```sh\ndocker run --rm python:3\n```\n")
        self.assertIn("docker run --rm python:3", out)
        self.assertNotIn("[docker]", out)
        self.assertNotIn("[python]", out)

    def test_a_tilde_fence_is_untouched(self):
        out = self._link("## Install\n\n~~~sh\ndocker run\n~~~\n")
        self.assertIn("~~~sh\ndocker run\n~~~", out)

    def test_a_different_fence_char_inside_does_not_close_it(self):
        """A ~~~ inside a ``` block is content. Closing on it would resume linking."""
        out = self._link("## Install\n\n```\ndocker a\n~~~\ndocker b\n```\n")
        self.assertNotIn("[docker]", out)

    def test_a_shorter_run_inside_does_not_close_it(self):
        out = self._link("## Install\n\n````\ndocker a\n```\ndocker b\n````\n")
        self.assertNotIn("[docker]", out)

    def test_prose_after_the_fence_links_again(self):
        """The fence must CLOSE — a leaked fence state would silently stop linking for the
        rest of the page, which looks like nothing happening."""
        out = self._link("## Install\n\n```sh\ndocker run\n```\n\nThen docker works.\n")
        self.assertIn("[docker](../concepts/docker.md) works", out)

    def test_a_hash_inside_a_fence_is_not_a_heading(self):
        """It advanced the section ordinal, resetting the once-per-section budget, so the
        same title could link twice in one section."""
        out = self._link("## Install\n\nUse docker.\n\n```sh\n# install docker\nls\n```\n\n"
                         "Then docker again.\n")
        self.assertEqual(out.count("[docker]"), 1, out)


class HealingTest(_Base):
    """Prevention alone freezes the damage: group 1 protects a link already inside code."""

    def test_a_link_inside_a_span_is_flattened(self):
        out = self._link("## Overview\n\nCLI (`[container](../concepts/container.md).exe`)"
                         " ships.\n")
        self.assertIn("`container.exe`", out)
        self.assertNotIn("[container]", out)

    def test_a_link_inside_a_fence_is_flattened(self):
        out = self._link("## Install\n\n```sh\n[docker](../concepts/docker.md) run\n```\n")
        self.assertIn("docker run", out)
        self.assertNotIn("[docker]", out)

    def test_the_display_text_is_what_survives(self):
        """`[the tool](../concepts/docker.md)` must leave "the tool", not "docker" — the
        bytes the author wrote are the ones that belong in the code."""
        out = self._link("## Overview\n\nRun `[the tool](../concepts/docker.md) ps`.\n")
        self.assertIn("`the tool ps`", out)

    def test_a_markdown_example_is_left_alone(self):
        """A page documenting markdown. Scoped to relative .md targets for exactly this."""
        out = self._link("## Overview\n\nWrite `[label](https://example.com)` for a link.\n")
        self.assertIn("`[label](https://example.com)`", out)

    def test_an_absolute_link_in_code_is_left_alone(self):
        out = self._link("## Overview\n\nSee `[docs](/guide/index.html)` there.\n")
        self.assertIn("`[docs](/guide/index.html)`", out)

    def test_healing_is_idempotent(self):
        """heal_pages runs at startup and after every ingest; a pass that rewrote every
        time would fill page history with empty revisions forever."""
        body = "## Overview\n\nCLI (`[container](../concepts/container.md).exe`) ships.\n"
        once = self._link(body)
        agent._autolink_now(agent.WIKI_DIR / "entities/p.md")
        twice = (agent.WIKI_DIR / "entities/p.md").read_text(
            encoding="utf-8").split("---", 2)[2]
        self.assertEqual(once, twice)

    def test_links_outside_code_are_not_flattened(self):
        """The healer must not touch the links the autolinker is supposed to write."""
        out = self._link("## Overview\n\nA [container](../concepts/container.md) is a "
                         "thing.\n")
        self.assertIn("[container](../concepts/container.md)", out)


class RegressionTest(_Base):

    def test_ordinary_prose_still_links(self):
        out = self._link("## Overview\n\nA container runs docker and python.\n")
        for slug in ("container", "docker", "python"):
            self.assertIn(f"../concepts/{slug}.md", out)

    def test_a_page_with_no_code_is_unaffected(self):
        body = "## Overview\n\nA container is a thing.\n"
        self.assertIn("[container]", self._link(body))


if __name__ == "__main__":
    unittest.main()
