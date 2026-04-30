# Lobotomy TODO

## Completed
- ✅ Fix todo list bugs: recurrence, notes persistence, overdue header, timezone, lowercase text
- ✅ UI polish: month labels, context autocomplete, delete button
- ✅ Keyboard shortcuts (R, N, ?)
- ✅ Group headers for context and priority sorting
- ✅ Bookmarklet popup improvements

## In Progress
(none)

## Backlog

### User Settings Page
- Create `/settings` route and template
- Settings to persist:
  - Dark mode toggle
  - Font size preference
  - Task list sort preferences (default column, direction)
  - Password change
- Store in user profile/database
- Add settings icon/link to navbar

### Daily Email Digest
- Background scheduler (APScheduler or similar)
- Send daily email containing:
  - Tasks due today and overdue
  - Items in reading list
- Use Resend API (already configured)
- Allow enable/disable per user
- Configurable time (morning/evening)
- Requires: user settings for email frequency/time

### Nice-to-Have
- Task keyboard navigation (arrow keys to move between rows)
- Inline task creation with keyboard
- Bulk recurrence pattern change
- Task templates/quick-add buttons
- Mobile UI improvements for touch
- Dark mode CSS variables already in place, just needs toggle
