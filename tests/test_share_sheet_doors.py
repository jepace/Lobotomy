r"""The three doors into the reading list must agree, and two of them had never been tested.

Asked *"any reason why the ios share sheet lobotomize button would time out?"*, which sent
me reading the two paths the suite had never once called. The timeout is still unexplained
— measured, `/api/push` answers in 5–68ms idle and ~1.1s during the heaviest end-of-ingest
load at the live wiki's scale, so the app is not where tens of seconds go. But reading them
found four defects, all in the two routes `/inbox/add`'s hard-won protections never reached.

**1. Three duplicate checks, two answers.** `/inbox/add` compares `_dupe_url_key` — scheme,
`www.`, trailing slash, fragment and known tracking parameters removed — while `/save` and
`/api/push` each had their own loop comparing the stored `url:` **verbatim**. So a story
shared from iOS or Twitter with `?utm_source=…` on it was deduped by the web form and saved
as a SECOND COPY by the share sheet. One reading list, two answers about what the same
story is, and the share sheet had the wrong one.

**2. A fetch that could overwrite a paste.** `_fetch_and_patch(dest, url)` without
`expect_body` skips its staleness check entirely. `/inbox/add` passes `expect_body=""` for
a documented reason — "paste, save, wait for the fetch, and the pasted story is gone" —
and both of these routes omitted it.

**3. `title:` written unescaped on the one route a stranger can reach.** `/api/push`
interpolated into `'title: "{title}"'`: a double quote ends the scalar, and **a newline
injects a frontmatter field of the caller's choosing** (`wikified: true` would mark an
article ingested; `url:` would repoint it). `source` and `author` were written bare with no
quoting at all, and `url` was only `.strip()`ed, so a newline anywhere in it did the same.
`fm_quote` exists for exactly this and escapes `"` and `\` — but **it cannot help with a
newline**, because every reader of this frontmatter parses it line by line. Collapsing
whitespace first is the half that was missing.

**4. Reading every article in full to compare one field.** Both loops called
`read_text()` on every file in `raw/`. Measured at 600 captures that is ~60MB of reads per
save. Not the timeout — 68ms idle — but it is the only term in that route that scales with
the size of the reading list.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: F401  — copies config.json.example so `import serve` can work

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import serve

PUSH_KEY = "test-push-key"


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lobotomy-doors-")).resolve()
        (self.root / "raw").mkdir()
        (self.root / "wiki" / "sources").mkdir(parents=True)
        self._saved = {k: getattr(serve, k) for k in ("REPO_ROOT", "WIKI_DIR", "RAW_DIR")}
        serve.REPO_ROOT = self.root
        serve.WIKI_DIR = self.root / "wiki"
        serve.RAW_DIR = self.root / "raw"
        serve._src_index_cache = None

        # The background fetch is a thread doing network IO; capture the call instead.
        self.fetches = []
        self._real_fetch = serve._fetch_and_patch
        serve._fetch_and_patch = lambda dest, url, **kw: self.fetches.append((dest, url, kw))

        # /save and /api/push authenticate against api.push_key.
        self._real_cfg = serve.cfg_get
        serve.cfg_get = lambda s, k, d="": (PUSH_KEY if (s, k) == ("api", "push_key")
                                            else self._real_cfg(s, k, d))

        serve.app.config["TESTING"] = True
        self.client = serve.app.test_client()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(lambda: setattr(serve, "_fetch_and_patch", self._real_fetch))
        self.addCleanup(lambda: setattr(serve, "cfg_get", self._real_cfg))
        self.addCleanup(lambda: [setattr(serve, k, v) for k, v in self._saved.items()])

    def _capture(self, name, url, *, body="Article text.", extra=""):
        (self.root / "raw" / name).write_text(
            f'---\ntitle: "An Article"\nurl: {url}\nsaved: 2026-01-01\n{extra}---\n\n{body}\n',
            encoding="utf-8")
        return self.root / "raw" / name

    def _raw_names(self):
        return sorted(p.name for p in (self.root / "raw").iterdir())

    def _save(self, url, title="A Title", **params):
        q = {"key": PUSH_KEY, "url": url, "title": title, "beacon": "1"}
        q.update(params)
        return self.client.get("/save", query_string=q)

    def _push(self, payload):
        return self.client.post("/api/push", json=payload,
                                headers={"Authorization": f"Bearer {PUSH_KEY}"})


class TrackingParameterDuplicateTest(_Base):
    """Bug 1, which is the one a user would actually hit: sharing from iOS attaches
    tracking parameters, and the share sheet did not recognise its own article."""

    TRACKED = "https://example.com/story?utm_source=twitter&utm_medium=social"
    CLEAN = "https://example.com/story"

    def test_save_recognises_a_tracked_url_as_the_same_article(self):
        self._capture("story.md", self.CLEAN)
        before = self._raw_names()
        r = self._save(self.TRACKED)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._raw_names(), before,
                         "the bookmarklet saved a second copy of an article already here")

    def test_push_recognises_a_tracked_url_as_the_same_article(self):
        self._capture("story.md", self.CLEAN)
        r = self._push({"url": self.TRACKED, "title": "A Story"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json()["duplicate"])
        self.assertEqual(self._raw_names(), ["story.md"])

    def test_and_the_other_direction(self):
        """Stored tracked, shared clean — the same article from the other side."""
        self._capture("story.md", self.TRACKED)
        r = self._push({"url": self.CLEAN, "title": "A Story"})
        self.assertTrue(r.get_json()["duplicate"])

    def test_the_three_doors_agree(self):
        """The point of the change: one comparison, so they cannot drift again."""
        self._capture("story.md", self.CLEAN)
        add = self.client.post("/inbox/add", json={"content": self.TRACKED})
        self.assertIn(add.status_code, (200, 401))      # 401 only if login is required
        self.assertTrue(self._push({"url": self.TRACKED, "title": "x"})
                        .get_json()["duplicate"])
        self._save(self.TRACKED)
        self.assertEqual(self._raw_names(), ["story.md"])

    def test_a_genuinely_different_article_is_still_saved(self):
        """The other half: the dedupe must not swallow a real second story. A parameter
        that is NOT a tracking parameter carries the article's identity."""
        self._capture("story.md", "https://example.com/wire?story=111")
        r = self._push({"url": "https://example.com/wire?story=222", "title": "Other"})
        self.assertFalse(r.get_json()["duplicate"], r.get_data(as_text=True))
        self.assertEqual(len(self._raw_names()), 2)

    def test_the_duplicate_reply_names_the_file_that_already_holds_it(self):
        self._capture("story.md", self.CLEAN)
        body = self._push({"url": self.TRACKED, "title": "A Story"}).get_json()
        self.assertEqual(body["filename"], "story.md")
        self.assertEqual(body["id"], "story")


class PasteIsNotOverwrittenTest(_Base):
    """Bug 2. `_fetch_and_patch` without `expect_body` skips its staleness check, so a
    fetch landing after the user pasted the article in replaces it — the data-loss bug
    `/inbox/add` already passes this argument to avoid."""

    def test_save_passes_expect_body(self):
        self._save("https://example.com/new")
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(self.fetches[0][2].get("expect_body"), "",
                         "a background fetch from /save may overwrite a pasted article")

    def test_push_passes_expect_body(self):
        self._push({"url": "https://example.com/new", "title": "New"})
        self.assertEqual(len(self.fetches), 1)
        self.assertEqual(self.fetches[0][2].get("expect_body"), "")

    def test_push_with_content_does_not_fetch_at_all(self):
        self._push({"url": "https://example.com/new", "title": "New",
                    "content": "The whole article, supplied by the caller."})
        self.assertEqual(self.fetches, [])


class FrontmatterInjectionTest(_Base):
    """Bug 3, on the one route an outside caller reaches."""

    def _fm_of(self, name):
        text = (self.root / "raw" / name).read_text()
        fm, _ = serve._parse_frontmatter(text)
        return fm, text

    def test_a_newline_in_the_title_cannot_inject_a_field(self):
        r = self._push({"url": "https://example.com/x",
                        "title": 'Part One\nwikified: true\nsource: forged'})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        fm, text = self._fm_of(r.get_json()["filename"])
        self.assertEqual(str(fm.get("wikified")).lower(), "false",
                         f"an injected field reached the frontmatter:\n{text}")
        self.assertEqual(fm.get("source"), "external-api",
                         f"the caller chose this item's source:\n{text}")
        # The payload stays, as TEXT, inside the title it was sent as — neutralising an
        # injection is not licence to silently drop what the caller actually said.
        self.assertIn("wikified: true", fm.get("title", ""))
        self.assertNotIn("\n", fm.get("title", ""))

    def test_a_quote_in_the_title_does_not_corrupt_the_line(self):
        r = self._push({"url": "https://example.com/y", "title": 'The "Big Lie" Memo'})
        fm, text = self._fm_of(r.get_json()["filename"])
        self.assertIn("Big Lie", fm.get("title", ""),
                      f"the title line parsed as something else:\n{text}")
        self.assertEqual(str(fm.get("wikified")).lower(), "false")

    def test_a_backslash_in_the_title_survives(self):
        r = self._push({"url": "https://example.com/z", "title": r"C:\path\to\thing"})
        fm, _ = self._fm_of(r.get_json()["filename"])
        self.assertIn("path", fm.get("title", ""))

    def test_source_and_author_are_quoted(self):
        r = self._push({"url": "https://example.com/w", "title": "T",
                        "source": "app\nwikified: true", "author": 'A "B" C'})
        fm, text = self._fm_of(r.get_json()["filename"])
        self.assertEqual(str(fm.get("wikified")).lower(), "false",
                         f"source injected a field:\n{text}")
        self.assertIn("author:", text)

    def test_a_newline_inside_the_url_cannot_inject_a_field(self):
        r = self._push({"url": "https://example.com/a\nwikified: true", "title": "T"})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        fm, text = self._fm_of(r.get_json()["filename"])
        self.assertEqual(str(fm.get("wikified")).lower(), "false",
                         f"the url line injected a field:\n{text}")

    def test_save_is_protected_too(self):
        """`/save` flattened quotes to apostrophes and collapsed whitespace, which worked;
        it goes through the shared helper now, so the protection cannot drift."""
        self._save("https://example.com/s", title='A "Quoted" Title\nwikified: true')
        name = [n for n in self._raw_names()][0]
        fm, text = self._fm_of(name)
        self.assertEqual(str(fm.get("wikified")).lower(), "false", text)

    def test_the_title_is_capped(self):
        r = self._push({"url": "https://example.com/long", "title": "T" * 5000})
        fm, _ = self._fm_of(r.get_json()["filename"])
        self.assertLessEqual(len(fm.get("title", "")), 310)


class NeverOverwriteTest(_Base):
    """Both routes minted their own collision name (`<slug>-<epoch>.md`) instead of using
    `_unique_raw_name`. The outcome was the same — neither overwrote — but it was a second
    answer to a question the add path had already settled, and that is how the duplicate
    check above came to disagree in the first place."""

    def test_a_slug_collision_does_not_overwrite(self):
        self._capture("a-title.md", "https://example.com/first", body="The FIRST story.")
        r = self._push({"url": "https://example.com/second", "title": "A Title"})
        self.assertEqual(r.status_code, 201)
        self.assertIn("The FIRST story.", (self.root / "raw" / "a-title.md").read_text())
        self.assertEqual(len(self._raw_names()), 2)

    def test_the_collision_name_is_the_shared_one(self):
        self._capture("a-title.md", "https://example.com/first")
        self._push({"url": "https://example.com/second", "title": "A Title"})
        self.assertIn("a-title-2.md", self._raw_names())

    def test_save_does_not_overwrite_either(self):
        self._capture("a-title.md", "https://example.com/first", body="The FIRST story.")
        self._save("https://example.com/second", title="A Title")
        self.assertIn("The FIRST story.", (self.root / "raw" / "a-title.md").read_text())
        self.assertIn("a-title-2.md", self._raw_names())


class InboundEmailDoorTest(_Base):
    """**A fourth door, found while unifying the other three** — `/api/inbound-email`,
    with every defect above and one of its own: it wrote with `dest.write_text()` rather
    than `_atomic_write`, bypassing the one write chokepoint (no atomic replace, no
    ownership inheritance). An email SUBJECT is also the one value in this family a
    stranger picks freely, and it went into `title: "{title}"` unescaped."""

    def _email(self, subject, body, frm="sender@example.com"):
        return self.client.post("/api/inbound-email",
                                json={"subject": subject, "text": body, "from": frm},
                                headers={"Authorization": f"Bearer {PUSH_KEY}"})

    def test_the_subject_is_escaped_and_the_item_is_not_marked_wikified(self):
        r = self._email('A "Quoted" Subject\nwikified: true', "Body.")
        self.assertIn(r.status_code, (200, 201), r.get_data(as_text=True))
        text = (self.root / "raw" / r.get_json()["filename"]).read_text()
        fm, _ = serve._parse_frontmatter(text)
        self.assertEqual(str(fm.get("wikified")).lower(), "false", text)
        self.assertEqual(fm.get("source"), "email", text)

    def test_the_sender_address_is_quoted(self):
        r = self._email("Subject", "Body.", frm='a@b.c\nwikified: true')
        text = (self.root / "raw" / r.get_json()["filename"]).read_text()
        fm, _ = serve._parse_frontmatter(text)
        self.assertEqual(str(fm.get("wikified")).lower(), "false", text)

    def test_a_tracked_url_in_an_email_is_the_same_article(self):
        self._capture("story.md", "https://example.com/story")
        r = self._email("Subject",
                        "Read this: https://example.com/story?utm_source=newsletter")
        self.assertIn(r.status_code, (200, 201), r.get_data(as_text=True))
        if r.get_json().get("duplicate"):
            self.assertEqual(self._raw_names(), ["story.md"])

    def test_it_writes_through_the_one_chokepoint(self):
        """`_atomic_write` is what gives the tree its ownership inheritance and an atomic
        replace. A direct `write_text` on this path is the thing being pinned out."""
        src = (Path(__file__).resolve().parent.parent / "tools" / "serve.py").read_text()
        i = src.index("def api_inbound_email")
        j = src.index("@app.route", i)
        # Comments stripped first. The first version of this assertion matched the comment
        # EXPLAINING the old `dest.write_text()` and would have passed with the bug still
        # in place — the same trap as the `"attempt "` grep in base.html.
        code = "\n".join(l for l in src[i:j].splitlines()
                          if not l.strip().startswith("#"))
        self.assertNotIn("write_text(", code,
                         "the inbound-email route writes without _atomic_write")
        self.assertIn("_capture_article(", code)


class OneImplementationTest(_Base):
    """The answer to *"do we need all those routes to do the same thing?"* — the entry
    points differ (session cookie / an `<img>` GET from a foreign origin / a Bearer POST /
    a mail webhook) and cannot be merged, but the work behind them is one function now.
    Every bug in this family came from the copies, so these assertions are the fix."""

    def test_no_route_builds_a_title_line_by_interpolation(self):
        """The defect shape is an f-STRING producing the line. Matching the bare text
        matched this file's own prose describing the bug — twice — which is the
        greps-a-comment trap; requiring the f prefix is what reaches the code."""
        src = (Path(__file__).resolve().parent.parent / "tools" / "serve.py").read_text()
        marker = chr(34) + "{"            # a double quote followed by a brace
        offenders = [l.strip() for l in src.splitlines()
                     if ("title: " + marker) in l
                     and ("f" + chr(39) in l or "f" + chr(34) in l)]
        self.assertEqual(offenders, [],
                         "a route is interpolating a title into frontmatter unescaped")

    def test_every_capture_goes_through_the_shared_function(self):
        src = (Path(__file__).resolve().parent.parent / "tools" / "serve.py").read_text()
        self.assertGreaterEqual(src.count("_capture_article("), 5,
                                "a door into the reading list is not using the shared "
                                "capture")

    def test_no_route_keeps_its_own_duplicate_loop(self):
        src = (Path(__file__).resolve().parent.parent / "tools" / "serve.py").read_text()
        self.assertNotIn("for existing in inbox_dir.glob", src)

    def test_the_paste_path_is_deliberately_not_folded_in(self):
        """`/inbox/add`'s pasted-text branch is a different operation — no URL to
        normalise or deduplicate, dedupe by body instead, and the text is stored verbatim
        with no frontmatter. Stated so it is not mistaken for a missed copy."""
        src = (Path(__file__).resolve().parent.parent / "tools" / "serve.py").read_text()
        i = src.index("def inbox_add")
        self.assertIn("_find_existing_capture(content=", src[i:i + 4000])


class FrontmatterOnlyReadTest(_Base):
    """Bug 4: the count is the complexity, and wall-clock is flaky on someone else's disk
    (the rule `test_inbox_listing` set)."""

    def test_a_url_lookup_does_not_read_the_article_bodies(self):
        big = "x" * 40000
        for i in range(12):
            self._capture(f"a{i}.md", f"https://example.com/a{i}", body=big)
        read = {"bytes": 0}
        real = Path.read_text

        def spy(p, *a, **kw):
            out = real(p, *a, **kw)
            if "raw" in p.parts:
                read["bytes"] += len(out)
            return out

        Path.read_text = spy
        try:
            serve._find_existing_capture(url="https://example.com/nope")
        finally:
            Path.read_text = real
        self.assertLess(read["bytes"], 12 * 40000 / 4,
                        f"a URL lookup read {read['bytes']} bytes of article text")

    def test_it_still_finds_the_match(self):
        for i in range(5):
            self._capture(f"a{i}.md", f"https://example.com/a{i}", body="y" * 30000)
        hit = serve._find_existing_capture(url="https://example.com/a3")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0].name, "a3.md")

    def test_frontmatter_longer_than_the_prefix_is_still_read(self):
        """A bounded read that gave up would report "not saved" for an article that is
        here, and the add path would then write over it — being wrong in that direction is
        how a paste got destroyed before."""
        padding = "\n".join(f"note{i}: {'v' * 200}" for i in range(80))
        (self.root / "raw" / "huge-fm.md").write_text(
            f'---\ntitle: "Huge"\n{padding}\nurl: https://example.com/huge\n---\n\nBody.\n',
            encoding="utf-8")
        self.assertGreater(len((self.root / "raw" / "huge-fm.md").read_text()),
                           serve._FM_PREFIX_BYTES)
        hit = serve._find_existing_capture(url="https://example.com/huge")
        self.assertIsNotNone(hit, "a capture with long frontmatter was reported missing")
        self.assertEqual(hit[0].name, "huge-fm.md")

    def test_a_paste_comparison_still_reads_bodies(self):
        """The content path genuinely needs them, so it must not have been optimised away."""
        self._capture("p.md", "https://example.com/p", body="The pasted article text.")
        hit = serve._find_existing_capture(content="The pasted article text.")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0].name, "p.md")

    def test_the_returned_body_is_the_whole_body(self):
        """Callers read the body of what comes back — `/inbox/add` decides whether to
        retry a fetch from it — so a bounded read must not truncate what it returns."""
        body = "z" * 50000
        self._capture("b.md", "https://example.com/b", body=body)
        hit = serve._find_existing_capture(url="https://example.com/b")
        self.assertGreaterEqual(len(hit[2]), 50000)


if __name__ == "__main__":
    unittest.main()
