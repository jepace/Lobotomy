"""list_inbox() must not re-derive the raw_source map once per item.

Observed: 90 reading-list items, most of them ingested, and /inbox took a long time to
load. The wikified branch resolved "which wiki/sources page came from this raw file" by
globbing wiki/sources/ and frontmatter-parsing every page until it found the match — and a
miss parsed all of them. That mapping is identical for every item, so 85 wikified items
against a few thousand source pages rebuilt the same dictionary 85 times: on a synthetic
tree at that scale the listing went from 1.0s to 0.03s once it was built once.

Asserting the wall-clock would be a flaky test on someone else's disk, so these count the
reads instead. The complexity is the thing that regressed, and a count is what pins it:
reads of wiki/sources must not grow with the number of inbox items.

The raw files were also each read and frontmatter-parsed three times — once for the sort
key, once for the excerpt, once for the wikified/archived flags — so that is counted too.
"""
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: F401  — copies config.json.example so `import serve` can work

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import serve


class InboxListingTest(unittest.TestCase):

    N_SRC = 400

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lobotomy-inbox-")).resolve()
        (self.root / "raw").mkdir()
        (self.root / "wiki" / "sources").mkdir(parents=True)
        self._saved = {k: getattr(serve, k) for k in ("REPO_ROOT", "WIKI_DIR", "RAW_DIR")}
        serve.REPO_ROOT = self.root
        serve.WIKI_DIR = self.root / "wiki"
        serve.RAW_DIR = self.root / "raw"
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(lambda: [setattr(serve, k, v) for k, v in self._saved.items()])

    def _raw(self, i, *, wikified=True, archived=False, extra=""):
        fm = [f"title: Article {i}", f"url: https://example.com/{i}",
              f"added: 2026-09-{(i % 28) + 1:02d}"]
        if wikified:
            fm.append("wikified: true")
        if archived:
            fm.append("archived: true")
        (self.root / "raw" / f"a{i}.md").write_text(
            "---\n" + "\n".join(fm) + f"\n{extra}---\n\nBody text for article {i}.\n")

    def _sources(self, n, *, matching=0):
        """n source pages; the LAST `matching` of them carry a raw_source, so a linear
        scan pays close to full price rather than finding its match immediately."""
        for i in range(n):
            rs = f"raw_source: raw/a{n - 1 - i}.md" if i >= n - matching else ""
            (self.root / "wiki" / "sources" / f"s{i}.md").write_text(
                f"---\ntitle: S{i}\ntype: source\n{rs}\n---\n\n# S{i}\n\nProse.\n")

    def _counted(self):
        """Count read_text() calls per directory across one list_inbox()."""
        counts = {"sources": 0, "raw": 0}
        real = Path.read_text

        def spy(p, *a, **kw):
            parts = p.parts
            if "sources" in parts:
                counts["sources"] += 1
            elif "raw" in parts:
                counts["raw"] += 1
            return real(p, *a, **kw)

        Path.read_text = spy
        try:
            items = serve.list_inbox()
        finally:
            Path.read_text = real
        return items, counts

    # -- the bug ----------------------------------------------------------------

    def test_source_pages_are_read_once_no_matter_how_many_items(self):
        self._sources(self.N_SRC, matching=20)
        for i in range(20):
            self._raw(i)
        items, counts = self._counted()
        self.assertEqual(len(items), 20)
        self.assertLessEqual(
            counts["sources"], self.N_SRC,
            "wiki/sources is being scanned more than once — the raw_source map is being "
            "rebuilt per inbox item")

    def test_the_scan_does_not_grow_with_the_number_of_items(self):
        """The complexity assertion. Five items and forty must cost the same."""
        self._sources(self.N_SRC, matching=40)
        for i in range(5):
            self._raw(i)
        _, few = self._counted()
        for i in range(5, 40):
            self._raw(i)
        _, many = self._counted()
        self.assertEqual(few["sources"], many["sources"],
                         f"source reads scaled with item count: {few} -> {many}")

    def test_each_raw_file_is_read_once(self):
        self._sources(10, matching=10)
        for i in range(10):
            self._raw(i)
        _, counts = self._counted()
        self.assertEqual(counts["raw"], 10,
                         "raw files are read more than once per listing")

    def test_no_source_scan_at_all_when_nothing_is_wikified(self):
        self._sources(self.N_SRC, matching=0)
        for i in range(10):
            self._raw(i, wikified=False)
        _, counts = self._counted()
        self.assertEqual(counts["sources"], 0,
                         "wiki/sources was scanned although no item is wikified")

    # -- behaviour that must survive the rewrite -------------------------------

    def test_the_wiki_page_is_still_resolved(self):
        self._sources(50, matching=3)
        for i in range(3):
            self._raw(i)
        items = {i["name"]: i for i in serve.list_inbox()}
        # _sources maps the last pages to raw/a{n-1-i}.md, i.e. s49->a0, s48->a1, s47->a2.
        self.assertEqual(items["a0.md"]["wiki_path"], "sources/s49.md")
        self.assertEqual(items["a1.md"]["wiki_path"], "sources/s48.md")
        self.assertEqual(items["a2.md"]["wiki_path"], "sources/s47.md")

    def test_an_unmatched_wikified_item_gets_an_empty_path(self):
        self._sources(20, matching=0)
        self._raw(0)
        self.assertEqual(serve.list_inbox()[0]["wiki_path"], "")

    def test_title_excerpt_and_url_survive_the_single_read(self):
        self._sources(1, matching=0)
        self._raw(7, wikified=False)
        it = serve.list_inbox()[0]
        self.assertEqual(it["title"], "Article 7")
        self.assertEqual(it["source_url"], "https://example.com/7")
        self.assertIn("Body text for article 7", it["excerpt"])
        self.assertTrue(it["has_content"])

    def test_archived_items_are_hidden_unless_asked_for(self):
        self._sources(1, matching=0)
        self._raw(0, wikified=False)
        self._raw(1, wikified=False, archived=True)
        self.assertEqual([i["name"] for i in serve.list_inbox()], ["a0.md"])
        self.assertEqual(
            sorted(i["name"] for i in serve.list_inbox(show_archived=True)),
            ["a0.md", "a1.md"])

    def test_newest_first_by_added_date(self):
        self._sources(1, matching=0)
        for i in (1, 5, 3):
            self._raw(i, wikified=False)   # added: 2026-09-02, -06, -04
        self.assertEqual([i["name"] for i in serve.list_inbox()],
                         ["a5.md", "a3.md", "a1.md"])

    def test_an_unreadable_item_does_not_take_the_whole_page_down(self):
        """`archived` used to be assigned only inside a try/except that swallowed
        everything, so a read failure reached the item dict with the name unbound and
        /inbox died with a NameError instead of listing the other 89 items."""
        self._sources(1, matching=0)
        self._raw(0, wikified=False)
        bad = self.root / "raw" / "bad.md"
        bad.write_text("---\ntitle: Bad\n---\n\nx\n")
        real = Path.read_text

        def spy(p, *a, **kw):
            if p.name == "bad.md":
                raise OSError("simulated read failure")
            return real(p, *a, **kw)

        Path.read_text = spy
        try:
            names = [i["name"] for i in serve.list_inbox()]
        finally:
            Path.read_text = real
        self.assertIn("a0.md", names)
        self.assertIn("bad.md", names)

    def test_a_url_item_is_listed(self):
        self._sources(1, matching=0)
        (self.root / "raw" / "link.url").write_text("Some Title\nURL: https://example.com/x\n")
        it = next(i for i in serve.list_inbox() if i["name"] == "link.url")
        self.assertEqual(it["title"], "Some Title")
        self.assertEqual(it["source_url"], "https://example.com/x")


if __name__ == "__main__":
    unittest.main()
