# Lobotomy TODO

(Task management moved to its own project, DoIt — old task-feature items removed.)

## Backlog

### Data repair (one-shot scripts)
- **Backfill lost source attributions**: before the re-ingest fix, entity pages updated in
  a session whose source page already existed silently kept their old `sources:` list.
  Script: for each `wiki/sources/*.md`, find entity/concept pages whose body cites it but
  whose `sources:` frontmatter omits it; backfill and re-render `## Sources`. Dry-run first.
- **Review queue for thin entity pages**: pages created before the "central to the story"
  bar (reporters, one-quote spokespeople) and pages with slug-style or lowercase titles.
  List candidates (single source + short body) for human review — not auto-delete, since
  deleting a page the autolinker knows about leaves broken links behind.

### Performance
- `_title_alts()` cost grows ~n² with title word count (one regex alternative per
  contiguous word sub-span). Cap or trim it — biggest remaining autolink cost.
- Job worker shares the process (and GIL) with Flask; heavy autolink stretches stall the
  UI. If it gets worse: move the worker to its own process (event files already provide
  the IPC).

### Config housekeeping (live server)
- `max_retries` is 999 in the live config — set back to ~6 now that daily-quota 429s use
  their own slow retry interval instead of the fast ladder.
- Revisit `max_rpm` — the 130K-token index injection is gone, so the real ceiling is
  likely much higher than the current setting assumes.
