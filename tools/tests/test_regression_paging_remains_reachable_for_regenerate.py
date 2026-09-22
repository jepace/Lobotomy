"""Regression: an earlier version of the outline change made full read coverage
unreachable for a large page — read_file always returned an outline for a page over
_WIKI_READ_LIMIT, with no way to page through it, so update_file's full-coverage
requirement could never be satisfied and the Regenerate Workflow (rewrite a whole page)
had no path that worked. The fix is the offset sentinel: offset=-1 (not given) returns the
outline, but any explicit offset (including 0) pages through the real text.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import TempWikiTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


class PagingRemainsReachableForRegenerate(TempWikiTestCase):

    def test_full_coverage_reachable_by_explicit_offset(self):
        sections = "".join(f"## Section {i}\n\n{'word ' * 400}\n\n" for i in range(60))
        p = self.w.page("entities/big.md", title="Big", type="entity",
                         body="## Overview\n\nIntro.\n\n" + sections)
        full_len = len(agent._strip_system_fm_fields(p.read_text(encoding="utf-8")))
        self.assertGreater(full_len, agent._WIKI_READ_LIMIT)

        # The un-offset read is an outline only — it must NOT by itself satisfy coverage.
        self.w.read("wiki/entities/big.md")
        self.assertLess(agent._ctx()._session_read_coverage.get("entities/big.md", 0),
                         full_len)

        # Paging with an explicit offset must be able to reach the end.
        offset = 0
        seen_end = False
        for _ in range(200):
            chunk = self.w.read("wiki/entities/big.md", offset=offset)
            if "[END OF FILE" in chunk:
                seen_end = True
                break
            if "offset=" not in chunk:
                break
            offset = int(chunk.rsplit("offset=", 1)[1].split(".")[0].split()[0])
        self.assertTrue(seen_end, "paging never reached EOF")
        self.assertGreaterEqual(
            agent._ctx()._session_read_coverage.get("entities/big.md", 0), full_len)

        # And with full coverage, update_file must actually be reachable.
        body_only = p.read_text(encoding="utf-8").split("---\n", 2)[2]
        result = agent._update_file("wiki/entities/big.md", body_only + "\nOne more line.\n")
        self.assertFalse(result.startswith("Error:"), result)


if __name__ == "__main__":
    unittest.main()
