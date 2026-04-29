#!/usr/bin/env python3
"""
Lobotomy — Task Filter CLI

Reads wiki/tasks.md and filters/displays tasks.

Usage: python3 tools/tasks.py [options]

Options:
  --open            Show open tasks only (default)
  --done            Show completed tasks only
  --all             Show all tasks regardless of status
  --priority P      Filter by priority: top, high, medium, low
  --status S        Filter by status: next, waiting, someday, hold
  --context C       Filter by context tag (#ctx:C), e.g. work, home, computer
  --project P       Filter by project tag (#proj:P)
  --due-today       Show tasks due today or overdue
  --overdue         Show tasks with a past due date only
  --star            Show starred tasks only
  --repeat          Show recurring tasks only

Results are sorted by due date (soonest first, no-date last), then by priority.

No external dependencies required.
"""

import sys
import re
from pathlib import Path
from datetime import date


PRIORITY_ORDER = {"top": 0, "high": 1, "medium": 2, "low": 3}


def find_tasks_file(script_path):
    repo_root = script_path.resolve().parent.parent
    tasks_file = repo_root / "wiki" / "tasks.md"
    if tasks_file.exists():
        return tasks_file
    cwd_tasks = Path.cwd() / "wiki" / "tasks.md"
    if cwd_tasks.exists():
        return cwd_tasks
    raise FileNotFoundError(
        f"Cannot find wiki/tasks.md at {tasks_file} or {cwd_tasks}\n"
        "Run from the repository root or the tools/ directory."
    )


def parse_tag(line, prefix):
    m = re.search(r"#" + re.escape(prefix) + r":(\S+)", line)
    return m.group(1) if m else None


def parse_tasks(text):
    tasks = []
    lines = text.splitlines()
    i = 0
    current_section = "Inbox"
    today = date.today().isoformat()

    while i < len(lines):
        line = lines[i]

        if line.startswith("## "):
            current_section = line[3:].strip()
            i += 1
            continue

        task_match = re.match(r"^- (\[[ x]\]) (.+)$", line)
        if task_match:
            done = task_match.group(1) == "[x]"
            rest = task_match.group(2)
            desc = re.sub(r"\s*#\S+", "", rest).strip()
            start = parse_tag(line, "start") or ""
            # Skip future-start tasks in open view
            if start and start > today and not done:
                i += 1
                continue
            task = {
                "done":      done,
                "description": desc,
                "section":   current_section,
                "priority":  parse_tag(line, "p"),
                "due":       parse_tag(line, "due"),
                "context":   parse_tag(line, "ctx"),
                "project":   parse_tag(line, "proj"),
                "status":    parse_tag(line, "s"),
                "start":     start,
                "length":    parse_tag(line, "len"),
                "repeat":    parse_tag(line, "rep"),
                "star":      bool(re.search(r"#star\b", line)),
                "done_date": parse_tag(line, "done"),
                "notes":     [],
                "subtasks":  [],
            }
            i += 1
            while i < len(lines):
                sub = lines[i]
                if re.match(r"^    - \[[ x]\]", sub):
                    task["subtasks"].append(sub.strip())
                    i += 1
                elif sub.startswith("  ") and sub.strip():
                    task["notes"].append(sub.strip())
                    i += 1
                else:
                    break
            tasks.append(task)
        else:
            i += 1

    return tasks


def parse_date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def priority_key(p):
    return PRIORITY_ORDER.get(p, 99)


def format_task(task, today):
    status = "[x]" if task["done"] else "[ ]"
    star   = "★ " if task["star"] else ""
    pri    = f"#p:{task['priority']}" if task["priority"] else ""
    st     = f"#{task['status']}" if task["status"] else ""
    rep    = f"rep:{task['repeat']}" if task["repeat"] else ""
    lng    = f"~{task['length']}" if task["length"] else ""

    due_str = ""
    if task["due"]:
        d = parse_date(task["due"])
        if d:
            if task["done"]:
                due_str = f"due:{task['due']}"
            elif d < today:
                due_str = f"OVERDUE({task['due']})"
            elif d == today:
                due_str = "DUE TODAY"
            else:
                delta = (d - today).days
                due_str = f"due:{task['due']} ({delta}d)"
        else:
            due_str = f"due:{task['due']}"

    ctx  = f"@{task['context']}" if task["context"] else ""
    proj = f"+{task['project']}" if task["project"] else ""

    meta = "  ".join(x for x in [pri, st, due_str, rep, lng, ctx, proj] if x)
    line = f"  {status} {star}{task['description']}"
    if meta:
        line += f"  [{meta}]"
    return line


def main():
    args = sys.argv[1:]

    show_open        = True
    show_done        = False
    filter_priority  = None
    filter_status    = None
    filter_context   = None
    filter_project   = None
    due_today_only   = False
    overdue_only     = False
    star_only        = False
    repeat_only      = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--open":
            show_open, show_done = True, False
        elif a == "--done":
            show_open, show_done = False, True
        elif a == "--all":
            show_open = show_done = True
        elif a == "--priority" and i + 1 < len(args):
            filter_priority = args[i + 1].lower(); i += 1
        elif a == "--status" and i + 1 < len(args):
            filter_status = args[i + 1].lower(); i += 1
        elif a == "--context" and i + 1 < len(args):
            filter_context = args[i + 1].lower(); i += 1
        elif a == "--project" and i + 1 < len(args):
            filter_project = args[i + 1].lower(); i += 1
        elif a == "--due-today":
            due_today_only = True
        elif a == "--overdue":
            overdue_only = True
        elif a == "--star":
            star_only = True
        elif a == "--repeat":
            repeat_only = True
        elif a in ("-h", "--help"):
            print(__doc__.strip())
            sys.exit(0)
        else:
            print(f"Unknown option: {a}\nRun with --help for usage.", file=sys.stderr)
            sys.exit(1)
        i += 1

    try:
        tasks_file = find_tasks_file(Path(__file__))
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    text      = tasks_file.read_text(encoding="utf-8", errors="replace")
    all_tasks = parse_tasks(text)
    today     = date.today()

    filtered = []
    for t in all_tasks:
        if t["done"] and not show_done:    continue
        if not t["done"] and not show_open: continue
        if filter_priority and t["priority"] != filter_priority: continue
        if filter_status   and t["status"]   != filter_status:   continue
        if filter_context  and t["context"]  != filter_context:  continue
        if filter_project  and t["project"]  != filter_project:  continue
        if star_only   and not t["star"]:   continue
        if repeat_only and not t["repeat"]: continue
        if due_today_only:
            d = parse_date(t["due"])
            if not d or d > today: continue
        if overdue_only:
            d = parse_date(t["due"])
            if not d or d >= today: continue
        filtered.append(t)

    if not filtered:
        print("No tasks match the given filters.")
        sys.exit(0)

    filtered.sort(key=lambda t: (
        parse_date(t["due"]) or date(9999, 12, 31),
        priority_key(t["priority"]),
        t["description"].lower(),
    ))

    by_section = {}
    for t in filtered:
        by_section.setdefault(t["section"], []).append(t)

    print(f"\nTasks ({len(filtered)} shown):\n")
    for section, section_tasks in by_section.items():
        print(f"### {section}")
        for t in section_tasks:
            print(format_task(t, today))
            for note in t["notes"]:
                print(f"         {note}")
            for sub in t["subtasks"]:
                print(f"         {sub}")
        print()


if __name__ == "__main__":
    main()
