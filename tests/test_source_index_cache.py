"""The source-page map is cached between requests, and a new ingest still shows up.

Reported as *"i can't add articles to the reading list (using share in iOS) they timeout,
because the server is busy processing ingestions? not sure about cause, but timing problems
somewhere"* — the instinct was right and the mechanism is measurable.

**Measured on a 1,500-source-page tree**: one `/inbox/list` cost **1,590 file reads**, 1,500
of them rebuilding this map — and `/inbox` polls that route every 8 seconds per visible tab.
One call is 93ms and harmless. **Six at once took 3.5 SECONDS each**, which is worse than
serialising them would be:

    sequential /inbox/list x4:   93, 92, 92, 93 ms
    6 concurrent /inbox/list:    3507, 3474, 3443, 3496, 3455, 3482 ms   (wall 3.5s)

The GIL is not held for long — a monitor thread ticking through a 48-second autolink saw a
longest stall of **37ms** and a median of **5.365ms**, which is exactly the interpreter's
switch interval. That is the whole mechanism: **a request's latency under load is roughly
its number of blocking operations × 5ms**, so a handler doing 1,590 reads waits seconds
while anything else is busy, and a POST arriving in the middle of that convoy waits behind
it. After caching: **3.5s → 368ms**, and reads per listing 1,590 → 90.

**The old entry said a cache was impossible**: *"deliberately NOT cached across requests —
the polling exists to notice items that have just been wikified, so a cache would break the
feature it serves."* True of a cache keyed on TIME, false of one keyed on the directory. An
article becomes wikified by an ingest **creating its source page**, which changes
`wiki/sources/`, which invalidates the cache — the thing the cache must not hide is the
thing that unavoidably invalidates it. `NoticesNewWorkTest` is the half that proves it, and
it is the half worth keeping.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: F401  — copies config.json.example so `import serve` can work

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import serve


class _Base(unittest.TestCase):
    N_SRC = 300

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lobotomy-srcidx-")).resolve()
        (self.root / "raw").mkdir()
        (self.root / "wiki" / "sources").mkdir(parents=True)
        self._saved = {k: getattr(serve, k) for k in ("REPO_ROOT", "WIKI_DIR", "RAW_DIR")}
        serve.REPO_ROOT = self.root
        serve.WIKI_DIR = self.root / "wiki"
        serve.RAW_DIR = self.root / "raw"
        serve._src_index_cache = None          # a module global: never leak it between tests
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(lambda: setattr(serve, "_src_index_cache", None))
        self.addCleanup(lambda: [setattr(serve, k, v) for k, v in self._saved.items()])

    def _raw(self, i, *, wikified=True):
        fm = [f"title: Article {i}", f"url: https://example.com/{i}",
              f"added: 2026-09-{(i % 28) + 1:02d}"]
        if wikified:
            fm.append("wikified: true")
        (self.root / "raw" / f"a{i}.md").write_text(
            "---\n" + "\n".join(fm) + "\n---\n\nBody text.\n")

    def _source(self, name, raw_source=""):
        rs = f"raw_source: {raw_source}" if raw_source else ""
        (self.root / "wiki" / "sources" / name).write_text(
            f"---\ntitle: {name}\ntype: source\n{rs}\n---\n\n# S\n\n## Summary\n\nProse.\n")

    def _sources(self, n, *, matching=0):
        for i in range(n):
            self._source(f"s{i}.md",
                         f"raw/a{n - 1 - i}.md" if i >= n - matching else "")

    def _reads(self, fn=None):
        """Count read_text() calls under wiki/sources across one call."""
        n = [0]
        real = Path.read_text

        def spy(p, *a, **kw):
            if "sources" in p.parts:
                n[0] += 1
            return real(p, *a, **kw)

        Path.read_text = spy
        try:
            out = (fn or serve.list_inbox)()
        finally:
            Path.read_text = real
        return out, n[0]


class CacheTest(_Base):
    def test_the_second_listing_does_not_re_read_the_source_pages(self):
        self._sources(self.N_SRC, matching=5)
        for i in range(5):
            self._raw(i)
        _items, first = self._reads()
        _items, second = self._reads()
        self.assertGreater(first, 0, "the fixture never read any source page")
        self.assertEqual(second, 0,
                         f"the map was rebuilt on the second listing ({second} reads)")

    def test_the_map_is_still_correct_from_the_cache(self):
        self._sources(self.N_SRC, matching=5)
        for i in range(5):
            self._raw(i)
        first = {i["name"]: i["wiki_path"] for i in serve.list_inbox()}
        second = {i["name"]: i["wiki_path"] for i in serve.list_inbox()}
        self.assertEqual(first, second)
        self.assertTrue(any(v for v in first.values()),
                        "no item resolved to a source page, so this proves nothing")

    def test_adding_reading_list_items_does_not_invalidate_it(self):
        """Writing to `raw/` cannot change a raw_source → page mapping, and this is the
        case that matters for the reported symptom: the poll runs while articles arrive."""
        self._sources(self.N_SRC, matching=5)
        for i in range(5):
            self._raw(i)
        self._reads()
        for i in range(5, 40):
            self._raw(i)
        _items, after = self._reads()
        self.assertEqual(after, 0, "adding raw files rebuilt the source map")

    def test_the_returned_map_cannot_be_mutated(self):
        """It is shared between requests now, so a caller that mutated it would corrupt
        every later listing. Enforced rather than documented."""
        self._sources(5, matching=1)
        idx = serve._raw_source_index()
        with self.assertRaises(TypeError):
            idx["raw/whatever.md"] = "sources/nope.md"


class NoticesNewWorkTest(_Base):
    """**The half that must survive**, and the reason the old comment said a cache was
    impossible: `/inbox` polls so it can notice an article that has just been wikified."""

    def test_a_new_source_page_invalidates_the_cache(self):
        self._sources(20, matching=0)
        self._raw(1)
        items = {i["name"]: i["wiki_path"] for i in serve.list_inbox()}
        self.assertEqual(items["a1.md"], "", "the fixture started out already resolved")

        # What an ingest does when it finishes an article: it creates the source page.
        self._source("brand-new-2026-article.md", "raw/a1.md")
        items = {i["name"]: i["wiki_path"] for i in serve.list_inbox()}
        self.assertEqual(items["a1.md"], "sources/brand-new-2026-article.md",
                         "the poll could not see an article that had just been wikified — "
                         "the cache broke the feature it was supposed to be free of")

    def test_a_deleted_source_page_invalidates_the_cache(self):
        self._sources(5, matching=0)
        self._source("gone-2026-x.md", "raw/a1.md")
        self._raw(1)
        self.assertEqual(serve._raw_source_index().get("raw/a1.md"),
                         "sources/gone-2026-x.md")
        (self.root / "wiki" / "sources" / "gone-2026-x.md").unlink()
        self.assertIsNone(serve._raw_source_index().get("raw/a1.md"),
                          "a deleted source page was still being reported from the cache")

    def test_the_directory_key_changes_when_a_page_is_added(self):
        self._sources(3, matching=0)
        before = serve._sources_dir_key()
        self._source("another-2026-y.md", "raw/a9.md")
        self.assertNotEqual(before, serve._sources_dir_key())

    def test_the_key_survives_a_missing_directory(self):
        """A fresh install has no wiki/sources yet, and a listing must not 500 over it."""
        shutil.rmtree(self.root / "wiki" / "sources")
        self.assertEqual(serve._sources_dir_key(), ())
        self._raw(1)
        self.assertEqual(len(serve.list_inbox()), 1)

    def test_an_empty_key_is_never_treated_as_a_valid_cache(self):
        """`()` means the directory could not be read. Caching against it would freeze
        whatever map happened to be in hand, and the next read of a restored directory
        would keep serving the frozen one.

        **The first version of this test could not reach the guard**, and `mutate.py`
        reported it MISSED: it primed the cache with `()` while the directory still
        existed, so the live key differed and the map was rebuilt either way. The guard
        only does anything when the CURRENT key is also `()` — so the directory has to be
        gone for the fixture to touch it. Same trap as the `✗ create_file` fixture: a
        fixture that cannot reach the code it names proves nothing.
        """
        self._sources(3, matching=1)
        self._raw(2)
        shutil.rmtree(self.root / "wiki" / "sources")
        self.assertEqual(serve._sources_dir_key(), ())
        serve._src_index_cache = ((), {"raw/stale.md": "sources/stale.md"})
        self.assertIsNone(serve._raw_source_index().get("raw/stale.md"),
                          "an unreadable directory was accepted as a cache key, so a "
                          "stale map is served for as long as it stays unreadable")


class IOBudgetTest(_Base):
    """The measurement that found it, as a test: the count is the complexity, and
    wall-clock is flaky on someone else's disk (the rule `test_inbox_listing` set)."""

    def test_a_steady_state_listing_reads_only_the_reading_list(self):
        self._sources(self.N_SRC, matching=10)
        for i in range(10):
            self._raw(i)
        serve.list_inbox()                     # warm

        n = [0]
        real = Path.read_text

        def spy(p, *a, **kw):
            n[0] += 1
            return real(p, *a, **kw)

        Path.read_text = spy
        try:
            serve.list_inbox()
        finally:
            Path.read_text = real
        self.assertLessEqual(n[0], 10 + 2,
                             f"a warm listing still read {n[0]} files; it should read the "
                             f"10 reading-list items and little else")

    def test_the_validity_check_does_not_read_any_file(self):
        """It is two syscalls against 1,500 reads. Reading even one file per entry would
        put the cost straight back."""
        self._sources(self.N_SRC, matching=0)
        n = [0]
        real = Path.read_text

        def spy(p, *a, **kw):
            n[0] += 1
            return real(p, *a, **kw)

        Path.read_text = spy
        try:
            serve._sources_dir_key()
        finally:
            Path.read_text = real
        self.assertEqual(n[0], 0)


if __name__ == "__main__":
    unittest.main()
