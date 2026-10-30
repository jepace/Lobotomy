"""`/reading-list` read the whole reading list off disk to draw its rows.

Reported as *"hitting the back button took 46 seconds"*, and then, once slow GETs were
logged at all, straight from the live log:

    14:39:19  autolink: linked 7 title(s) … (1.2s, 16901 titles checked)
    14:39:40  ingest progress: 7/22 listed pages done      <- 21s pacing sleep
    14:39:40  GET /reading-list -> 200 in 18043ms  << SLOW

**The autolink is 1.2s there, not 39s** — the GIL and scoping fixes are deployed and
working — and the 21-second gaps between rounds are the ingest's own pacing sleep. So the
server was mostly IDLE while that request took eighteen seconds, which rules out CPU
contention and leaves I/O: the one thing a warm-cache development machine cannot show.
Every model of this ran in tens of milliseconds here while the jail took eighteen seconds.

**Measured: one `/reading-list` load read 8.2 MB — 100% of the reading list — to display
a title and a 200-character excerpt per row.** `list_inbox` called `read_text()` on every
capture, and a capture is a whole news article. After bounding it: **1.8 MB, 4.6×**.

The rule this follows is `_raw_frontmatter_items`', one function below: read a prefix, and
fall back to the whole file whenever the cheap answer could be WRONG rather than merely
incomplete. Here that is `has_content` — reporting False for an article that has content
hides its Read button and makes a saved story look empty.

The first class is an ORACLE test: it runs the original full-read logic beside the bounded
one and requires every field of every row to match, across the shapes a capture actually
takes. A faster listing that says something subtly different is not a faster listing.
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harness
import serve

BIG = "word " * 4000          # ~20KB, comfortably past the 8KB prefix


def _oracle(f):
    """What `list_inbox` did before: read the whole file, then split it."""
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = ""
    try:
        fm, body = serve._parse_frontmatter(text)
    except Exception:
        fm, body = {}, text
    return text, fm, body


class AgreesWithTheFullReadTest(unittest.TestCase):
    """Every shape a capture arrives in, compared field by field."""

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        # REPO_ROOT too: _inbox_item does relative_to(REPO_ROOT) for a wikified row,
        # so leaving it pointing at the real checkout raises on exactly the rows this
        # compares. The same fixture gap as the post-ingest chain's.
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        serve.REPO_ROOT = self.w.root
        self.addCleanup(lambda: (setattr(serve, "RAW_DIR", self._saved[0]),
                                 setattr(serve, "WIKI_DIR", self._saved[1]),
                                 setattr(serve, "REPO_ROOT", self._saved[2])))
        serve._src_index_cache = None

    def _write(self, name, text):
        p = self.w.raw / name
        p.write_text(text, encoding="utf-8")
        return p

    def _assert_agrees(self, p):
        want_text, want_fm, want_body = _oracle(p)
        got_text, got_fm, got_body = serve._read_capture_for_listing(p)
        # The row is what matters, not the bytes: compare what list_inbox builds.
        want = serve._inbox_item(p, want_text, want_fm, want_body, {}, dict)
        got = serve._inbox_item(p, got_text, got_fm, got_body, {}, dict)
        self.assertEqual(got, want, f"{p.name} listed differently")

    def test_an_ordinary_capture(self):
        self._assert_agrees(self._write("a.md",
            '---\ntitle: "A Story"\nurl: https://example.com/a\nsaved: 2026-01-01\n'
            f'wikified: false\n---\n\nFirst para.\n\nSecond para.\n\n{BIG}\n'))

    def test_a_capture_far_longer_than_the_prefix(self):
        self._assert_agrees(self._write("long.md",
            '---\ntitle: "Long"\nurl: https://example.com/l\nsaved: 2026-01-01\n'
            f'---\n\nOpening line.\n\n{BIG}\n{BIG}\n{BIG}\n'))

    def test_frontmatter_that_runs_past_the_prefix(self):
        """Every parsed field would be a guess, so this reads the file."""
        filler = "\n".join(f"extra_{i}: value {i}" for i in range(900))
        self._assert_agrees(self._write("fat-fm.md",
            f'---\ntitle: "Fat"\nurl: https://example.com/f\n{filler}\n'
            '---\n\nThe body is down here.\n'))

    def test_a_body_that_only_starts_past_the_prefix(self):
        """The failure the fallback exists for: nothing but skipped lines in the prefix
        would report has_content False for an article that has content."""
        skipped = "\n".join("<!-- a comment -->" for _ in range(600))
        self._assert_agrees(self._write("late-body.md",
            '---\ntitle: "Late"\nurl: https://example.com/x\nsaved: 2026-01-01\n'
            f'---\n\n{skipped}\n\nThe real article starts here.\n'))

    def test_a_fetch_failure_placeholder(self):
        self._assert_agrees(self._write("failed.md",
            '---\ntitle: "Failed"\nurl: https://example.com/z\nsaved: 2026-01-01\n'
            'fetch_failed: true\n---\n\nContent could not be fetched.\n'))

    def test_a_pasted_note_with_no_frontmatter(self):
        """The paste branch stores text verbatim, so there is no `---` at all."""
        self._assert_agrees(self._write("note.txt", f"Just some pasted text.\n\n{BIG}\n"))

    def test_a_url_file(self):
        self._assert_agrees(self._write("u.url", "A Title\nURL: https://example.com/u\n"))

    def test_an_empty_file(self):
        self._assert_agrees(self._write("empty.md", ""))

    def test_a_whole_mixed_listing_matches(self):
        """All of them at once, through the real list_inbox, in order."""
        random.seed(4)
        for i in range(25):
            self._write(f"s{i:02d}.md",
                f'---\ntitle: "S{i}"\nurl: https://example.com/{i}\n'
                f'saved: 2026-01-0{i % 9 + 1}\nwikified: {"true" if i % 3 else "false"}\n'
                f'---\n\nLead sentence {i}.\n\n{BIG}\n')
        got = serve.list_inbox()
        real = serve._read_capture_for_listing
        serve._read_capture_for_listing = _oracle
        try:
            want = serve.list_inbox()
        finally:
            serve._read_capture_for_listing = real
        self.assertEqual(got, want)


class ItActuallyReadsLessTest(unittest.TestCase):
    """The point of the change. Asserted on BYTES, because a timing assertion is flaky on
    someone else's disk — the same reason `test_inbox_listing` counts reads."""

    def setUp(self):
        self.w = harness.TempWiki()
        self.w.__enter__()
        self.addCleanup(self.w.__exit__, None, None, None)
        self._saved = (serve.RAW_DIR, serve.WIKI_DIR, serve.REPO_ROOT)
        serve.RAW_DIR, serve.WIKI_DIR = self.w.raw, self.w.wiki
        serve.REPO_ROOT = self.w.root
        self.addCleanup(lambda: (setattr(serve, "RAW_DIR", self._saved[0]),
                                 setattr(serve, "WIKI_DIR", self._saved[1]),
                                 setattr(serve, "REPO_ROOT", self._saved[2])))
        serve._src_index_cache = None
        self.on_disk = 0
        for i in range(30):
            t = (f'---\ntitle: "S{i}"\nurl: https://example.com/{i}\nsaved: 2026-01-01\n'
                 f'---\n\nLead {i}.\n\n{BIG}\n{BIG}\n')
            (self.w.raw / f"s{i:02d}.md").write_text(t, encoding="utf-8")
            self.on_disk += len(t.encode())

    def _bytes_for_a_listing(self):
        read = {"n": 0}
        real_rt, real_open = Path.read_text, Path.open

        def rt(self_p, *a, **kw):
            out = real_rt(self_p, *a, **kw)
            read["n"] += len(out.encode("utf-8", errors="replace"))
            return out

        def op(self_p, *a, **kw):
            fh = real_open(self_p, *a, **kw)
            inner = fh.read

            def r(n=-1):
                out = inner(n)
                if isinstance(out, str):
                    read["n"] += len(out.encode("utf-8", errors="replace"))
                return out
            fh.read = r
            return fh

        Path.read_text, Path.open = rt, op
        try:
            serve.list_inbox()
        finally:
            Path.read_text, Path.open = real_rt, real_open
        return read["n"]

    def test_a_listing_does_not_read_the_whole_reading_list(self):
        got = self._bytes_for_a_listing()
        self.assertLess(got, self.on_disk / 2,
                        f"read {got} of {self.on_disk} bytes — still reading the articles")

    def test_the_prefix_is_bounded_per_file(self):
        """Roughly the prefix each, not the article each."""
        self.assertLess(self._bytes_for_a_listing(), 30 * serve._FM_PREFIX_BYTES * 1.5)


if __name__ == "__main__":
    unittest.main()
