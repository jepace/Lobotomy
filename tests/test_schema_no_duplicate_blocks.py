"""No block of LOBOTOMY.md is written twice.

Found while consolidating the read-route advice. Step 5 (entity documents) and Step 6
(concept documents) each carried the SAME 100 lines, verbatim: how to fold a source into an
existing page, how to read it, which section to choose, the heading rules, what not to set
in frontmatter. Two costs, and the second is the one that matters:

  * The schema is prepended to every request of every round of every ingest, so a duplicated
    hundred lines is paid thousands of times a day.
  * It is the same failure the code half of this change was about. One copy gets updated and
    the other does not. Both copies said "outline only — go straight to update_section" at a
    point when `done()`'s refusal had already been corrected to say read_section, so the
    schema disagreed with the tool in two places at once, and fixing the one you happened to
    search for left the other.

Step 6 now points at Step 5 rather than restating it. This module is what stops the restating
from coming back — in LOBOTOMY.md, where the model reads it, and in CLAUDE.md, where the next
person does.

The window is six lines, which is longer than any legitimate repetition in either file: a
shared sentence or a repeated bullet is fine and common, a repeated half-page is not. The
comparison ignores blank lines so reflowing cannot hide a duplicate.
"""
import hashlib
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_WINDOW = 6


def _duplicate_blocks(text: str):
    """[(line A, line B, run length)] for every repeated run of _WINDOW+ non-blank lines."""
    lines = [l.rstrip() for l in text.splitlines()]
    seen, out = {}, []
    for i in range(len(lines) - _WINDOW):
        window = [l for l in lines[i:i + _WINDOW] if l.strip()]
        if len(window) < _WINDOW:
            continue                      # ran into blanks — not a solid block
        key = hashlib.md5("\n".join(window).encode("utf-8")).hexdigest()
        if key in seen:
            a, b = seen[key], i
            n = 0
            while (a + n < len(lines) and b + n < len(lines)
                   and lines[a + n] == lines[b + n]):
                n += 1
            out.append((a + 1, b + 1, n))
        else:
            seen[key] = i
    return out


class SchemaDuplicationTest(unittest.TestCase):

    def _check(self, name):
        dups = _duplicate_blocks((REPO / name).read_text(encoding="utf-8"))
        if dups:
            a, b, n = dups[0]
            self.fail(f"{name}: {len(dups)} duplicated block(s); the first repeats "
                      f"{n} lines between line {a} and line {b}. Point one at the other "
                      f"instead — two copies of a procedure drift, and this one is sent on "
                      f"every round of every ingest.")

    def test_lobotomy_md(self):
        self._check("LOBOTOMY.md")

    def test_claude_md(self):
        self._check("CLAUDE.md")

    def test_the_detector_finds_a_real_duplicate(self):
        """A test that cannot fail proves nothing — so make it fail on purpose."""
        block = "".join(f"line {i} of a repeated procedure\n" for i in range(9))
        self.assertTrue(_duplicate_blocks("preamble\n" + block + "middle\n" + block))

    def test_the_detector_tolerates_a_short_repetition(self):
        """A repeated bullet or sentence is normal and must not be reported."""
        self.assertFalse(_duplicate_blocks(
            "- Do not set `sources:`.\n\nsomething else\n\n- Do not set `sources:`.\n"))


if __name__ == "__main__":
    unittest.main()
