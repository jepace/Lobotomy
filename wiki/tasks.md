---
title: "Tasks"
type: tasks
tags: [tasks, meta]
created: 2026-04-27
updated: 2026-04-27
sources: []
---

# Tasks

*Task manager. Replaces Toodledo.*
*See `CLAUDE.md` Section 10 for the full task workflow.*

## Tag Reference

| Tag | Values | Meaning |
|-----|--------|---------|
| `#p:` | `top`, `high`, `medium`, `low` | Priority (omit for none) |
| `#due:` | `YYYY-MM-DD` | Due date |
| `#start:` | `YYYY-MM-DD` | Hide until this date |
| `#ctx:` | `home`, `work`, `computer`, `errands`, `calls` | GTD context |
| `#proj:` | `any-slug` | Project |
| `#s:` | `next`, `waiting`, `someday`, `hold` | Status (omit = active) |
| `#rep:` | `1d`, `7d`, `2w`, `1m`, `1y` | Fixed recurrence |
| `#rep:` | `7d+`, `1m+` | Relative recurrence (after completion) |
| `#len:` | `30m`, `2h` | Estimated duration |
| `#star` | *(no value)* | Starred / flagged |
| `#done:` | `YYYY-MM-DD` | Set automatically when completing |

Example task:
```
- [ ] Write quarterly review #p:high #due:2026-05-01 #ctx:computer #proj:work #len:2h
  Notes: Use the template from last quarter
    - [ ] Gather metrics
    - [ ] Write narrative
```

Recurring example:
```
- [ ] Pay rent #p:top #due:2026-05-01 #rep:1m
- [ ] Floss #rep:1d+
```

---

## Inbox

<!-- Quick-capture tasks go here. Assign to a project section when ready. -->

*No tasks yet.*

---

<!-- Add project sections below as needed:

## Project Name

- [ ] Task description #p:high #due:YYYY-MM-DD #ctx:work

-->
