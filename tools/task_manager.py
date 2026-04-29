#!/usr/bin/env python3
"""
Task manager for wiki/tasks.md. Parses, filters, and updates tasks.
"""

import re
from pathlib import Path
from datetime import datetime, timedelta

TASKS_FILE = Path(__file__).resolve().parent.parent / "wiki" / "tasks.md"


class Task:
    """Represents a single task with all its metadata."""

    def __init__(self, line, line_num, raw_notes=""):
        self.line_num = line_num
        self.raw_notes = raw_notes
        self.section = None

        match = re.match(r'^(\s*)- \[([x ])\] (.+)$', line)
        if not match:
            self.indent = ""
            self.complete = False
            self.description = ""
            self.tags = {}
            return

        self.indent = match.group(1)
        self.complete = match.group(2) == 'x'
        rest = match.group(3)

        # Split description from tags
        tag_pattern = r'#\w+(?::[^\s]*)?'
        tag_matches = list(re.finditer(tag_pattern, rest))

        if tag_matches:
            self.description = rest[:tag_matches[0].start()].strip()
            tags_text = rest[tag_matches[0].start():]
        else:
            self.description = rest.strip()
            tags_text = ""

        # Parse tags — keys stored WITHOUT colon: '#p' -> 'high'
        self.tags = {}
        for m in re.finditer(tag_pattern, tags_text):
            tag_full = m.group(0)
            if ':' in tag_full:
                key, val = tag_full.split(':', 1)
                self.tags[key] = val.lstrip(':')  # strip extra colons from old corrupted data
            else:
                self.tags[tag_full] = None  # e.g. '#star': None

    def to_line(self):
        """Reconstruct markdown task line."""
        checkbox = 'x' if self.complete else ' '
        tags_str = ' '.join(
            f"{k}:{v}" if v is not None else k
            for k, v in sorted(self.tags.items())
        )
        line = f"{self.indent}- [{checkbox}] {self.description}"
        if tags_str:
            line += f" {tags_str}"
        return line

    # ── Properties — keys WITHOUT colon ──────────────────────
    @property
    def due(self):        return self.tags.get('#due')

    @property
    def priority(self):   return self.tags.get('#p')

    @property
    def context(self):    return self.tags.get('#ctx')

    @property
    def project(self):    return self.tags.get('#proj')

    @property
    def status(self):     return self.tags.get('#s')

    @property
    def recurrence(self): return self.tags.get('#rep')

    @property
    def start(self):      return self.tags.get('#start')

    @property
    def notes(self):      return self.raw_notes.strip()

    # ── Setters — keys WITHOUT colon ─────────────────────────
    def _set(self, key, val):
        if val:
            self.tags[key] = val
        else:
            self.tags.pop(key, None)

    def set_due(self, val):        self._set('#due', val)
    def set_priority(self, val):   self._set('#p', val)
    def set_context(self, val):    self._set('#ctx', val)
    def set_project(self, val):    self._set('#proj', val)
    def set_status(self, val):     self._set('#s', val)
    def set_recurrence(self, val): self._set('#rep', val)
    def set_start(self, val):      self._set('#start', val)
    def set_notes(self, val):      self.raw_notes = val

    def complete_task(self):
        self.complete = True
        self.tags['#done'] = datetime.now().strftime('%Y-%m-%d')

    def reopen_task(self):
        self.complete = False
        self.tags.pop('#done', None)

    def get_next_recurrence(self):
        """If task is recurring, return a new Task for the next occurrence."""
        if not self.recurrence or not self.complete:
            return None

        rep = self.recurrence.lower()

        # Parse recurrence: e.g. "1d", "1w+", "2m", etc.
        match = re.match(r'^(\d+)([dwmy])(\+?)$', rep)
        if not match:
            return None

        count = int(match.group(1))
        unit = match.group(2)
        relative = match.group(3) == '+'  # '+' means relative to completion date

        # Get base date
        if relative:
            base = datetime.now().date()
        else:
            base = datetime.fromisoformat(self.due or datetime.now().isoformat()).date() if self.due else datetime.now().date()

        # Calculate next due date
        if unit == 'd':
            next_due = base + timedelta(days=count)
        elif unit == 'w':
            next_due = base + timedelta(weeks=count)
        elif unit == 'm':
            # Month calculation
            month = base.month + count
            year = base.year
            while month > 12:
                month -= 12
                year += 1
            try:
                next_due = base.replace(year=year, month=month)
            except ValueError:  # Day doesn't exist in target month
                next_due = base.replace(year=year, month=month, day=1) - timedelta(days=1)
        elif unit == 'y':
            next_due = base.replace(year=base.year + count)
        else:
            return None

        # Create new task for next occurrence
        next_task = Task("", -1)
        next_task.indent = self.indent
        next_task.complete = False
        next_task.description = self.description
        next_task.tags = self.tags.copy()
        next_task.tags.pop('#done', None)  # Remove done tag
        next_task.tags['#due'] = next_due.isoformat()
        next_task.section = self.section

        return next_task


def read_tasks():
    """Read all tasks from wiki/tasks.md."""
    if not TASKS_FILE.exists():
        return []

    lines = TASKS_FILE.read_text(encoding='utf-8').split('\n')
    tasks = []
    current_section = None
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith('##'):
            current_section = line.lstrip('#').strip()
            i += 1
            continue

        if re.match(r'^\s*- \[[x ]\]', line):
            notes_lines = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if re.match(r'^##', nxt) or re.match(r'^\s*- \[[x ]\]', nxt):
                    break
                if nxt and (nxt[0] in ' \t' or not nxt.strip()):
                    notes_lines.append(nxt)
                    i += 1
                else:
                    break

            task = Task(line, len(tasks), '\n'.join(notes_lines))
            task.section = current_section
            tasks.append(task)
        else:
            i += 1

    return tasks


def write_tasks(tasks):
    """Write updated tasks back to wiki/tasks.md."""
    if not TASKS_FILE.exists():
        return

    lines = TASKS_FILE.read_text(encoding='utf-8').split('\n')
    task_map = {t.line_num: t for t in tasks}
    new_lines = []
    i = 0
    task_count = 0

    while i < len(lines):
        line = lines[i]

        if re.match(r'^\s*- \[[x ]\]', line):
            if task_count in task_map:
                task = task_map[task_count]
                new_lines.append(task.to_line())
                if task.raw_notes.strip():
                    for note_line in task.raw_notes.split('\n'):
                        new_lines.append(note_line)
            task_count += 1
            i += 1
            # Skip old inline notes
            while i < len(lines):
                nxt = lines[i]
                if re.match(r'^##', nxt) or re.match(r'^\s*- \[[x ]\]', nxt):
                    break
                i += 1
            continue

        new_lines.append(line)
        i += 1

    TASKS_FILE.write_text('\n'.join(new_lines), encoding='utf-8')


def get_all_contexts():
    return sorted(set(t.context for t in read_tasks() if t.context))

def get_all_projects():
    return sorted(set(t.project for t in read_tasks() if t.project))

def get_all_sections():
    return sorted(set(t.section for t in read_tasks() if t.section))
