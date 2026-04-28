#!/usr/bin/env python3
"""
Task manager for wiki/tasks.md. Parses, filters, and updates tasks.
"""

import re
from pathlib import Path
from datetime import datetime

TASKS_FILE = Path(__file__).resolve().parent.parent / "wiki" / "tasks.md"


class Task:
    """Represents a single task with all its metadata."""

    def __init__(self, line, line_num, raw_notes=""):
        """Parse a task line. Expects format: - [x] description #tag:val #tag:val"""
        self.line_num = line_num
        self.raw_notes = raw_notes
        self.section = None

        # Parse checkbox and description
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

        # Split description and tags
        tag_pattern = r'#\w+:?[^\s]*'
        tag_matches = list(re.finditer(tag_pattern, rest))

        if tag_matches:
            desc_end = tag_matches[0].start()
            self.description = rest[:desc_end].strip()
            tags_text = rest[desc_end:].strip()
        else:
            self.description = rest.strip()
            tags_text = ""

        # Parse tags
        self.tags = {}
        for match in re.finditer(tag_pattern, tags_text):
            tag_full = match.group(0)
            if ':' in tag_full:
                key, val = tag_full.split(':', 1)
                self.tags[key] = val
            else:
                self.tags[tag_full] = None

    def to_line(self):
        """Reconstruct task line from current state."""
        checkbox = 'x' if self.complete else ' '
        tags_str = ' '.join(
            f"{k}:{v}" if v else k
            for k, v in sorted(self.tags.items())
        )
        line = f"{self.indent}- [{checkbox}] {self.description}"
        if tags_str:
            line += f" {tags_str}"
        return line

    @property
    def due(self):
        return self.tags.get('#due:', None)

    @property
    def priority(self):
        return self.tags.get('#p:', None)

    @property
    def context(self):
        return self.tags.get('#ctx:', None)

    @property
    def project(self):
        return self.tags.get('#proj:', None)

    @property
    def status(self):
        return self.tags.get('#s:', None)

    @property
    def recurrence(self):
        return self.tags.get('#rep:', None)

    @property
    def notes(self):
        return self.raw_notes.strip()

    def set_due(self, val):
        if val:
            self.tags['#due:'] = val
        else:
            self.tags.pop('#due:', None)

    def set_priority(self, val):
        if val:
            self.tags['#p:'] = val
        else:
            self.tags.pop('#p:', None)

    def set_context(self, val):
        if val:
            self.tags['#ctx:'] = val
        else:
            self.tags.pop('#ctx:', None)

    def set_project(self, val):
        if val:
            self.tags['#proj:'] = val
        else:
            self.tags.pop('#proj:', None)

    def set_status(self, val):
        if val:
            self.tags['#s:'] = val
        else:
            self.tags.pop('#s:', None)

    def set_notes(self, val):
        self.raw_notes = val

    def complete_task(self):
        """Mark task as complete and set done date."""
        self.complete = True
        today = datetime.now().strftime('%Y-%m-%d')
        self.tags['#done:'] = today


def read_tasks():
    """Read all tasks from wiki/tasks.md. Return list of Task objects."""
    if not TASKS_FILE.exists():
        return []

    text = TASKS_FILE.read_text(encoding='utf-8')
    lines = text.split('\n')

    tasks = []
    current_section = None
    i = 0

    while i < len(lines):
        line = lines[i]

        # Detect section headers
        if line.startswith('##'):
            current_section = line.replace('##', '').strip()
            i += 1
            continue

        # Detect task lines
        if re.match(r'^\s*- \[[x ]\]', line):
            # Collect notes (indented lines following the task)
            notes_lines = []
            i += 1
            while i < len(lines):
                next_line = lines[i]
                # Stop if we hit another task or section
                if re.match(r'^##', next_line) or re.match(r'^\s*- \[[x ]\]', next_line):
                    break
                # Include indented lines as notes
                if next_line and (next_line[0] in ' \t' or next_line.strip() == ""):
                    notes_lines.append(next_line)
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
    """Write tasks back to wiki/tasks.md, preserving structure."""
    if not TASKS_FILE.exists():
        return

    text = TASKS_FILE.read_text(encoding='utf-8')
    lines = text.split('\n')

    # Build a map of line_num -> task for quick lookup
    task_map = {task.line_num: task for task in tasks}

    # Rebuild the file
    new_lines = []
    i = 0
    task_count = 0

    while i < len(lines):
        line = lines[i]

        # Check if this is a task line
        if re.match(r'^\s*- \[[x ]\]', line):
            # Find corresponding task
            if task_count in task_map:
                task = task_map[task_count]
                new_lines.append(task.to_line())
                # Add notes if any
                if task.raw_notes.strip():
                    for note_line in task.raw_notes.split('\n'):
                        new_lines.append(note_line)
                task_count += 1

            # Skip old notes
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if re.match(r'^##', next_line) or re.match(r'^\s*- \[[x ]\]', next_line):
                    break
                i += 1
            continue

        # Copy everything else
        new_lines.append(line)
        i += 1

    TASKS_FILE.write_text('\n'.join(new_lines), encoding='utf-8')


def get_all_contexts():
    """Return list of all contexts used in tasks."""
    tasks = read_tasks()
    return sorted(set(t.context for t in tasks if t.context))


def get_all_projects():
    """Return list of all projects used in tasks."""
    tasks = read_tasks()
    return sorted(set(t.project for t in tasks if t.project))


def get_all_sections():
    """Return list of all section names."""
    tasks = read_tasks()
    return sorted(set(t.section for t in tasks if t.section))
