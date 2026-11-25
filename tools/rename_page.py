#!/usr/bin/env python3
"""
Rename a wiki page — file, title, and every link pointing at it.

Pages are titled by the LLM at creation from whatever the source called the subject, and
it sometimes picks a fragment: an article about the airline mentioned "United", so the
page became entities/united.md titled "United". A title that is a common word or part of
a longer proper noun then does real damage, because the autolinker matches it everywhere —
that page turned "United Nations" into "[United](../entities/united.md) Nations" across
the wiki.

    python3 tools/rename_page.py wiki/entities/united.md wiki/entities/united-airlines.md \\
        --title "United Airlines"

What it does:
  * moves the page, and its version history along with it
  * sets title: in the frontmatter when --title is given
  * repoints every link that pointed at the old path

Links whose display text was the OLD title are stripped back to plain text rather than
repointed, because that text is what is now wrong: "[United](...) Nations" becomes
"United Nations", not a link relabelled to an airline. Run relink.py afterwards and the
prose that genuinely names the subject gets linked again under the new title, while the
prose that never meant it stays plain.

Add --dry-run to see the counts without writing. Everything it changes goes through the
normal write path, so each page keeps a history entry and is revertable.

**This is a thin CLI over `agent.rename_page`, deliberately.** It used to BE the
implementation — 205 lines of top-level script with no functions — which is exactly how
its copy of the generated-pages bug went uncovered while `merge_page`'s was caught:
nothing in-process could reach it. The logic now lives beside every other maintenance
pass, so a route and a unit test can call it too, and there is one answer rather than two.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import rename_page


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    new_title = None
    if "--title" in argv:
        i = argv.index("--title")
        if i + 1 >= len(argv):
            print("--title needs a value", file=sys.stderr)
            return 2
        new_title = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("-")]
    if len(args) != 2:
        print(__doc__.strip().split("\n\n")[0] + "\n\n"
              'Usage: python3 tools/rename_page.py <old.md> <new.md> '
              '[--title "New Title"] [--dry-run]', file=sys.stderr)
        return 2

    r = rename_page(args[0], args[1], new_title=new_title, dry_run=dry)
    if r.get("error"):
        print(r["error"], file=sys.stderr)
        # Principle 4, and the half that moving the logic out nearly lost: name the CALL,
        # not only the rule. `agent.rename_page` reports the corrected PATH — it cannot
        # know this script's flag names — so composing the runnable line is the CLI's job.
        if r.get("suggested_path"):
            print(f"\nDid you mean:\n    python3 {sys.argv[0]} {args[0]} "
                  f"{r['suggested_path']} --title \"...\"", file=sys.stderr)
        return 1

    print(f'{r["old"]}  ->  {r["new"]}')
    if r.get("type_changed"):
        was, now = r["type_changed"]
        print(f"type: {was}  ->  {now}   (the destination directory decides)")
    print(f'title: "{r["old_title"]}"' +
          (f'  ->  "{r["new_title"]}"' if new_title else "  (unchanged)"))
    print(f'\nlinks pointing at it: {r["repointed"]} repointed, {r["stripped"]} stripped '
          f'to plain text, across {r["pages_touched"]} page(s)')
    if dry:
        print("\nDry run — nothing written.")
        return 0
    if r.get("history_moved"):
        print("history moved along with the page")
    print("index rebuilt")
    for note in r.get("notes", []):
        print(f"\nNOTE: {note}")
    if r.get("title_unchanged"):
        # NOT a rename command. `<new> <new>` is refused — "destination already exists" —
        # so printing it named a move that cannot be made, principle 4's own failure in
        # the tool whose error messages this CLI exists to compose. The rename is already
        # done; what is left is one frontmatter field.
        print(f'          the page is at /wiki/{r["new"].removeprefix("wiki/")} — set '
              f'title: there, or pass --title next time')
    return 0


if __name__ == "__main__":
    sys.exit(main())
