#!/usr/bin/env python3
"""
Find — and optionally repair — pages carrying the same heading more than once.

The write paths refuse these now, but pages damaged earlier keep their duplicates. Three
routes made them, and each leaves different wreckage:

  * update_section content that echoed its own heading. The content is placed under the
    real heading, so the page ends up with the heading twice and the FIRST copy empty.
  * append_section asked for a name that missed an existing heading by punctuation or
    case, creating a second section beside the first, material split across them.
  * A whole-page rewrite that pasted an old copy of a section beside its replacement,
    leaving two similar-but-different bodies.

--fix repairs only what needs no judgment:

  empty      one copy has no body at all      -> drop that copy
  identical  the copies have the same body    -> keep one
  contained  one body contains all the others -> keep the one that contains them

Two copies with genuinely divergent content are left alone and listed, because choosing
what survives is a human call. Repairs go through the normal write path, so every page
changed keeps a version-history entry and can be reverted from its History view.

Run from the repo root:
  python3 tools/find_duplicate_sections.py               # report only
  python3 tools/find_duplicate_sections.py --fix --dry-run
  python3 tools/find_duplicate_sections.py --fix
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import (WIKI_DIR, wiki_pages, _heading_dupes, _norm_heading,
                   _atomic_write, begin_write_scope)

FIX = "--fix" in sys.argv
DRY = "--dry-run" in sys.argv
HEAD_RE = re.compile(r"^(#{1,6})[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)


def sections(body: str):
    """(heading_start, heading_end, span_end, level, name) for every heading.

    A section runs to the next heading at the same or a higher level, so its span carries
    any subsections with it — which is what makes comparing two copies meaningful, and
    what makes deleting one delete the whole thing rather than orphaning its children.
    """
    heads = [(m.start(), m.end(), len(m.group(1)), m.group(2).strip())
             for m in HEAD_RE.finditer(body)]
    out = []
    for i, (hs, he, lvl, name) in enumerate(heads):
        end = len(body)
        for hs2, _, lvl2, _ in heads[i + 1:]:
            if lvl2 <= lvl:
                end = hs2
                break
        out.append((hs, he, end, lvl, name))
    return out


def norm_body(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def repair(body: str):
    """Return (new_body, [(name, how)] merged, [name] needing a human)."""
    merged, manual, skip = [], [], set()
    while True:
        secs = sections(body)
        groups: dict = {}
        for idx, (_, _, _, lvl, name) in enumerate(secs):
            groups.setdefault((lvl, _norm_heading(name)), []).append(idx)

        target = key = None
        for k, g in groups.items():
            if len(g) > 1 and k not in skip:
                key, target = k, g
                break
        if target is None:
            return body, merged, manual

        name = secs[target[0]][4]
        bodies = [norm_body(body[secs[i][1]:secs[i][2]]) for i in target]

        drop = how = None
        if any(b == "" for b in bodies):
            keep = max(range(len(bodies)), key=lambda i: len(bodies[i]))
            drop, how = [target[i] for i in range(len(target)) if i != keep], "empty"
        elif len(set(bodies)) == 1:
            drop, how = target[1:], "identical"
        else:
            holder = next((i for i in range(len(bodies))
                           if all(b in bodies[i] for b in bodies)), None)
            if holder is not None:
                drop, how = [target[i] for i in range(len(target)) if i != holder], "contained"

        if drop is None:
            skip.add(key)          # leave it in place; do not reconsider it
            manual.append(name)
            continue

        for idx in sorted(drop, reverse=True):      # back to front keeps offsets valid
            hs, _, end, _, _ = secs[idx]
            body = body[:hs] + body[end:]
        merged.append((name, how))


found = pages_fixed = 0
still_manual = []
for p in wiki_pages():
    text = p.read_text(encoding="utf-8", errors="replace")
    fm_m = re.match(r"^(---\s*\n.*?\n---\s*\n)", text, re.DOTALL)
    fm, body = (fm_m.group(1), text[fm_m.end():]) if fm_m else ("", text)
    # The same check the write paths use, so this reports exactly what they now refuse —
    # including headings differing only by trailing punctuation or case ("Key Policies"
    # beside "Key Policies:"), which an exact comparison treats as two separate sections.
    if not _heading_dupes(body):
        continue
    found += 1
    rel = p.relative_to(WIKI_DIR).as_posix()

    if not FIX:
        print(f"{rel}: {', '.join(_heading_dupes(body))}")
        continue

    new_body, merged, manual = repair(body)
    print(f"{rel}\n    merged: " + (", ".join(f"{n} ({how})" for n, how in merged) or "none"))
    if manual:
        print(f"    needs a human: {', '.join(manual)}")
        still_manual.append((rel, manual))
    if merged and not DRY:
        begin_write_scope()
        _atomic_write(p, fm + new_body)
        pages_fixed += 1

if not found:
    print("No duplicated headings found.")
elif not FIX:
    print(f"\n{found} page(s) affected. Re-run with --fix to merge the copies that can be "
          f"merged without judgment — one empty, identical, or one containing the other — "
          f"and to see which are left. Add --dry-run first to see what it would do.")
else:
    print(f"\n{found} page(s) had duplicates; "
          f"{'would repair' if DRY else 'repaired'} {found - len(still_manual)}.")
    if still_manual:
        print(f"\n{len(still_manual)} page(s) still need a human — two copies with "
              f"genuinely different content, where what survives is not mechanical:")
        for rel, names in still_manual:
            print(f"  {rel}: {', '.join(names)}")
    if DRY:
        print("\nDry run — nothing was written.")
    elif pages_fixed:
        print("\nEvery page changed has a history entry, revertable from its History view.")
