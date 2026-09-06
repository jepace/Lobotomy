"""Characterization harness for the autolinker.

The autolinker is the most bug-prone code here (see CLAUDE.md): its matching semantics are
subtle — all occurrences linked, never inside an existing link, partial links upgraded to
the longer title, headings skipped — and easy to break while changing something else.

This runs a corpus of tricky inputs through _autolink in a throwaway wiki and dumps the
results. Capture a baseline before a change, compare after; the outputs should be
byte-identical unless the change is meant to alter behavior.

    python3 tools/tests/run_autolink_cases.py before.json
    # ...make the change...
    python3 tools/tests/run_autolink_cases.py after.json
    diff before.json after.json
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import agent
from autolink_cases import CASES

SANDBOX = Path(__file__).parent / "alwiki"


def build(pages, target_subdir, body):
    shutil.rmtree(SANDBOX, ignore_errors=True)
    agent.WIKI_DIR = SANDBOX
    agent.HISTORY_DIR = SANDBOX / ".history"
    agent.REPO_ROOT = SANDBOX.parent
    for sd in ("entities", "concepts", "sources", "synthesis"):
        (SANDBOX / sd).mkdir(parents=True, exist_ok=True)

    for title, aliases, no_auto in pages:
        slug = agent._slug_for(title) or "untitled"
        fm = [f'title: "{title}"', "type: entity", "tags: []",
              "created: 2026-01-01", "updated: 2026-01-01", "sources: []"]
        if aliases:
            fm.append("aliases: " + json.dumps(aliases))
        if no_auto:
            fm.append("no_autolink: true")
        (SANDBOX / "entities" / f"{slug}.md").write_text(
            "---\n" + "\n".join(fm) + "\n---\n\n# " + title + "\n\nStub.\n")

    target = SANDBOX / target_subdir / "target-page.md"
    target.write_text(
        '---\ntitle: "Target Page"\ntype: entity\ntags: []\n'
        'created: 2026-01-01\nupdated: 2026-01-01\nsources: []\n---\n\n' + body + "\n")
    agent._title_map_cache = None
    agent._title_regex_cache.clear()
    return target


results = {}
for name, pages, subdir, body in CASES:
    target = build(pages, subdir, body)
    rel = target.relative_to(SANDBOX).as_posix()
    try:
        msg = agent._autolink({"path": f"{SANDBOX.name}/{rel}"})
    except Exception as e:
        msg = f"EXCEPTION: {type(e).__name__}: {e}"
    out = target.read_text()
    results[name] = {"message": msg, "content": out}

shutil.rmtree(SANDBOX, ignore_errors=True)
Path(sys.argv[1]).write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"wrote {len(results)} case results to {sys.argv[1]}")
