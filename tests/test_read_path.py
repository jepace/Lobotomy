"""read_file's outline and the offset sentinel — docs/test-plan.md section 5.

offset defaults to -1, not 0: "no offset given" and "offset 0" are different requests.
int(x or 0) collapsed them once, since 0 is falsy — see CLAUDE.md "Reading a large page".
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import agent


class ReadPathTest(TempWikiTestCase):

    def test_page_under_limit_returns_full_text_no_outline(self):
        self.w.page("entities/small.md", title="Small", type="entity",
                     body="## Overview\n\nA short page.\n")
        text = self.w.read("wiki/entities/small.md")
        self.assertNotIn("[OUTLINE", text)
        self.assertIn("A short page.", text)

    def test_page_over_limit_no_offset_returns_outline_with_all_sections(self):
        sections = "".join(f"## Section {i}\n\n{'word ' * 400}\n\n" for i in range(60))
        self.w.page("entities/big.md", title="Big", type="entity",
                     body="## Overview\n\nIntro.\n\n" + sections)
        full_len = len((self.w.wiki / "entities/big.md").read_text(encoding="utf-8"))
        self.assertGreater(full_len, agent._WIKI_READ_LIMIT)
        text = self.w.read("wiki/entities/big.md")
        self.assertIn("[OUTLINE", text)
        for i in range(60):
            self.assertIn(f"Section {i}", text)
        self.assertLess(len(text), full_len // 2)

    def test_explicit_offset_zero_returns_truncated_chunk_not_outline(self):
        sections = "".join(f"## Section {i}\n\n{'word ' * 400}\n\n" for i in range(60))
        self.w.page("entities/big.md", title="Big", type="entity",
                     body="## Overview\n\nIntro.\n\n" + sections)
        text = self.w.read("wiki/entities/big.md", offset=0)
        self.assertNotIn("[OUTLINE", text)
        self.assertIn("[TRUNCATED", text)

    def test_paging_to_eof_then_update_file_accepted(self):
        sections = "".join(f"## Section {i}\n\n{'word ' * 400}\n\n" for i in range(60))
        p = self.w.page("entities/big.md", title="Big", type="entity",
                         body="## Overview\n\nIntro.\n\n" + sections)
        offset = 0
        for _ in range(50):
            chunk = self.w.read("wiki/entities/big.md", offset=offset)
            if "[END OF FILE" in chunk or "[TRUNCATED" not in chunk:
                break
            offset = int(chunk.rsplit("offset=", 1)[1].split(".")[0].split()[0])
        full_body = p.read_text(encoding="utf-8").split("---\n", 2)[2]
        result = agent._update_file("wiki/entities/big.md", full_body + "\nMore.\n")
        self.assertFalse(result.startswith("Error:"), result)

    def test_update_file_refused_after_outline_only_read(self):
        sections = "".join(f"## Section {i}\n\n{'word ' * 400}\n\n" for i in range(60))
        p = self.w.page("entities/big.md", title="Big", type="entity",
                         body="## Overview\n\nIntro.\n\n" + sections)
        self.w.read("wiki/entities/big.md")  # outline only — nothing quoted, nothing credited
        before = p.read_text(encoding="utf-8")
        body_only = before.split("---\n", 2)[2]
        result = agent._update_file("wiki/entities/big.md", body_only + "\nExtra.\n")
        self.assertTrue(result.startswith("Error:"), result)
        self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_raw_file_over_limit_pages_never_outlines(self):
        content = "word " * 20000
        self.w.raw_file("huge.md", content)
        full_len = len(content)
        self.assertGreater(full_len, agent._RAW_READ_LIMIT)
        text = self.w.read("raw/huge.md")
        self.assertNotIn("[OUTLINE", text)
        self.assertIn("[TRUNCATED", text)

    def _big(self):
        sections = "".join(f"## Section {i}\n\n{'word ' * 400}\n\n" for i in range(60))
        self.w.page("entities/big.md", title="Big", type="entity",
                     body="## Overview\n\nIntro.\n\n" + sections)

    def test_outline_read_logs_that_it_returned_an_outline(self):
        # The outline path quotes nothing, so it must not claim a truncation. It used to
        # log "truncated — showed chars 0-0 of 43700", describing something that did not
        # happen, because the message reused a char counter the outline path sets to 0.
        self._big()
        with self.assertLogs("lobotomy.agent", level="INFO") as cm:
            self.w.read("wiki/entities/big.md")
        self.assertTrue(any("section outline" in m for m in cm.output), cm.output)
        self.assertFalse(any("0-0" in m for m in cm.output), cm.output)

    def test_paged_read_logs_a_real_truncation(self):
        self._big()
        with self.assertLogs("lobotomy.agent", level="INFO") as cm:
            self.w.read("wiki/entities/big.md", offset=0)
        self.assertTrue(any("truncated" in m for m in cm.output), cm.output)
        self.assertFalse(any("0-0" in m for m in cm.output), cm.output)


class SectionNotFoundTest(TempWikiTestCase):
    """read_section's reply when the section is missing — a live ingest hit this on four
    of twenty-eight pages, because 736 pages have no Overview and Overview is the
    schema-correct first guess."""

    def _page(self):
        self.w.page("entities/donald-trump.md", title="Donald Trump", type="entity",
                    body=("# Donald Trump\n\nTitle line.\n\n"
                          "## Background\n\n" + "b " * 40 + "\n\n"
                          "## First Presidental Term\n\n" + "t " * 60 + "\n\n"
                          "## Sources\n\n- x\n"))

    def test_page_own_h1_is_not_offered_as_a_section(self):
        self._page()
        r = agent.TOOL_FNS["read_section"]({"path": "wiki/entities/donald-trump.md",
                                            "section": "Overview"})
        self.assertTrue(r.startswith("Error:"), r)
        self.assertNotIn("# Donald Trump\n", r.replace("## ", "# ") + "\n")
        self.assertNotIn("Donald Trump  (", r)

    def test_generated_sources_section_is_not_offered(self):
        self._page()
        r = agent.TOOL_FNS["read_section"]({"path": "wiki/entities/donald-trump.md",
                                            "section": "Overview"})
        self.assertNotIn("## Sources", r)

    def test_reply_carries_each_sections_opening_not_just_its_name(self):
        # Names alone cost a round: the agent must read again before it can compose.
        self._page()
        r = agent.TOOL_FNS["read_section"]({"path": "wiki/entities/donald-trump.md",
                                            "section": "Overview"})
        self.assertIn("## Background", r)
        self.assertIn("## First Presidental Term", r)
        self.assertIn("b b b", r, "section openings were not included")
        self.assertIn("chars)", r, "section sizes were not included")

    def test_update_section_not_found_reply_matches_read_section(self):
        self._page()
        a = agent.TOOL_FNS["read_section"]({"path": "wiki/entities/donald-trump.md",
                                            "section": "Overview"})
        b = agent.TOOL_FNS["update_section"]({"path": "wiki/entities/donald-trump.md",
                                              "section": "Overview", "content": "x"})
        for probe in ("## Background", "## First Presidental Term"):
            self.assertIn(probe, a)
            self.assertIn(probe, b)
        self.assertNotIn("Donald Trump  (", b)


if __name__ == "__main__":
    unittest.main()
