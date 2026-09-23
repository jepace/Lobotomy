"""When a write is refused for duplicating a heading, the refusal names the call to make.

The observed failure, from an ingest on wiki/entities/kaitlan-collins.md: the model had
written new text for two sections and sent it all as the content of `## Overview`, with
`## Background & Journalism Career` embedded in the middle. That section already existed,
so the guard refused — correctly. But the refusal ended "Send the section's body only",
which says what to DELETE and nothing about where the deleted material should go.

It resent the identical call twice — two rounds, about three minutes — and then complied
in the worst available way: it dropped the heading and left that section's text sitting
inside Overview. The material was never junk. It was a second section's content, written
in the only call the model could see a use for.

That is guard principle 4. A refusal that is locally correct about its own question, and
names no move the caller can act on, does not prevent the damage — it redirects it
somewhere quieter. The fix is not a new restriction: it is telling the model that
update_section writes one section per call, and that this text belongs in its own.

Both branches matter and are different situations:
  - the heading names a section that already exists elsewhere on the page → point at that
    section's own update_section / append_section call
  - the content repeats a heading within itself → there is no other call to make; the two
    blocks have to be merged or renamed
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class UpdateSectionDuplicateTest(TempWikiTestCase):

    def _collins(self):
        self.w.page("entities/kaitlan-collins.md", title="Kaitlan Collins", type="entity",
                    body="# Kaitlan Collins\n\n## Overview\n\nA CNN journalist.\n\n"
                         "## Background & Journalism Career\n\nShe joined CNN in 2017.\n")
        agent.init_session()
        agent.TOOL_FNS["read_file"]({"path": "wiki/entities/kaitlan-collins.md"})

    def _send_both_sections(self):
        return agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/kaitlan-collins.md", "section": "Overview",
            "content": "Collins is CNN's chief White House correspondent.\n\n"
                       "## Background & Journalism Career\n\nShe joined CNN in 2017 and "
                       "rose to chief White House correspondent.\n"})

    def test_it_is_still_refused(self):
        self._collins()
        self.assertTrue(self._send_both_sections().startswith("Error:"))

    def test_the_refusal_names_the_call_that_works(self):
        self._collins()
        r = self._send_both_sections()
        self.assertIn("update_section(", r, "the refusal does not name a call to make")
        self.assertIn("'Background & Journalism Career'", r)
        self.assertIn("append_section", r, "the other route was not offered")

    def test_the_refusal_says_the_text_is_not_wasted(self):
        # The observed model's next move was to discard the intent. The refusal has to say
        # the material has a destination, not just that it cannot go here.
        self._collins()
        self.assertIn("not wasted", self._send_both_sections())

    def test_it_names_which_section_this_call_is_writing(self):
        self._collins()
        self.assertIn("'Overview'", self._send_both_sections())

    def test_the_move_it_names_actually_succeeds(self):
        # The point of principle 4, and the only version of this test that proves
        # anything: follow the refusal's instructions and the edit goes through.
        self._collins()
        self._send_both_sections()
        a = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/kaitlan-collins.md", "section": "Overview",
            "content": "Collins is CNN's chief White House correspondent.\n"})
        self.assertFalse(a.startswith("Error:"), a)
        b = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/kaitlan-collins.md",
            "section": "Background & Journalism Career",
            "content": "She joined CNN in 2017 and rose to chief White House "
                       "correspondent, covering the Natalie Harp story.\n"})
        self.assertFalse(b.startswith("Error:"), b)
        disk = self.w.disk("entities/kaitlan-collins.md")
        self.assertEqual(disk.count("## Background & Journalism Career"), 1, disk)
        self.assertIn("Natalie Harp", disk)
        self.assertIn("chief White House correspondent.\n\n## Background", disk,
                      "the second section's text stayed inside Overview")

    def test_a_content_internal_repeat_gets_the_other_message(self):
        # No other call to make here — both blocks were written in this one.
        self.w.page("entities/k.md", title="K Corp", type="entity",
                    body="# K Corp\n\n## Overview\n\nA firm.\n")
        agent.init_session()
        agent.TOOL_FNS["read_file"]({"path": "wiki/entities/k.md"})
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/k.md", "section": "Overview",
            "content": "A firm.\n\n## Legal Proceedings\n\nOne.\n\n"
                       "## Legal Proceedings\n\nTwo.\n"})
        self.assertIn("repeats this heading", r)
        self.assertIn("Merge", r)
        self.assertNotIn("update_section(", r,
                         "it pointed at a section's own call, but the section is one this "
                         "call is creating — there is nothing to call yet")

    def test_a_brand_new_section_is_not_refused_at_all(self):
        # This is what the model was reaching for, and it already worked. Only a heading
        # that COLLIDES is a problem; introducing one is how a section gets made.
        self.w.page("entities/k.md", title="K Corp", type="entity",
                    body="# K Corp\n\n## Overview\n\nA firm.\n")
        agent.init_session()
        agent.TOOL_FNS["read_file"]({"path": "wiki/entities/k.md"})
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/k.md", "section": "Overview",
            "content": "A firm.\n\n## Legal Proceedings\n\nA suit was filed.\n"})
        self.assertFalse(r.startswith("Error:"), r)
        disk = self.w.disk("entities/k.md")
        self.assertIn("\n## Legal Proceedings\n", disk)
        self.assertEqual(
            len(agent._page_section_names(disk, "K Corp")), 2,
            "the new heading did not become a sibling section")

    def test_a_page_that_already_had_duplicates_stays_editable(self):
        # Principle 2. Only duplicates this edit introduces are refused.
        self.w.page("entities/d.md", title="D Corp", type="entity",
                    body="# D Corp\n\n## Overview\n\nA firm.\n\n## History\n\nOne.\n\n"
                         "## History\n\nTwo.\n")
        agent.init_session()
        agent.TOOL_FNS["read_file"]({"path": "wiki/entities/d.md"})
        r = agent.TOOL_FNS["update_section"]({
            "path": "wiki/entities/d.md", "section": "Overview",
            "content": "A firm that makes things.\n"})
        self.assertFalse(r.startswith("Error:"), r)


class ReplaceTextDuplicateTest(TempWikiTestCase):

    def test_the_refusal_names_a_call_rather_than_just_an_instruction(self):
        self.w.page("entities/k.md", title="K Corp", type="entity",
                    body="# K Corp\n\n## Overview\n\nA firm.\n\n## History\n\nFounded.\n")
        agent.init_session()
        agent.TOOL_FNS["read_file"]({"path": "wiki/entities/k.md"})
        r = agent.TOOL_FNS["replace_text"]({
            "path": "wiki/entities/k.md", "old_text": "A firm.",
            "new_text": "A firm.\n\n## History\n\nMore on the founding."})
        self.assertTrue(r.startswith("Error:"), r)
        self.assertIn("update_section", r)
        self.assertIn("append_section", r)


if __name__ == "__main__":
    unittest.main()
