# Everyday workflows

Recipes for using the server day to day. Tool names are the MCP tools from [TOOLS.md](TOOLS.md); in
Claude Code they appear as `mcp__moodle__<tool>`.

## Morning check (1 call)

> "What's due this week and what's new in Moodle?"

`get_deadlines(days=7)` + `get_updates()`. `get_updates` remembers when you last asked, so the next
call only shows what changed since then. Or run the `weekly_briefing` prompt.

Things worth knowing:
- Quiz attempts that are started but not submitted are flagged with ⚠ — some courses only count
  manually submitted attempts.
- `get_deadlines` shows what Moodle's timeline still lists as open. Finished items disappear there;
  `get_activity_status(course)` shows the full picture per course.

## Keep a course folder in sync

```
download_files(course="SE2", dest="~/Uni/SE2/materials")            # PDFs, slides, attachments
download_files(course="SE2", dest="~/Uni/SE2/materials", layout="sections")  # one folder per section
```

Repeated runs download only new or changed files (tracked in `.moodle-sync.json` in the folder) and
never delete anything. Files you edited locally (annotations…) are never overwritten — a newer Moodle
version is saved next to them as `name (moodle YYYY-MM-DD).ext`. Videos are skipped unless
`include_videos=true`. File names are kept as in Moodle with spaces replaced by `_` (`sanitize_names=false`
keeps spaces). Files you already put there by hand with the same name and size are adopted instead of
downloaded again.

## Course "passport" / status page

`course_sync` prompt, or manually:

1. `get_course("SE2")` — rules and grading usually live in section texts and labels.
2. `get_activity_status("SE2")` — due dates, submission status and grades per activity.
3. `get_grades(course="SE2")` — points, weights, teacher feedback.
4. `get_announcements(course="SE2", days=90)` — announcement log.
5. `get_deadlines(course="SE2", days=150)` — open items with status.

## Reading material

- `list_files("DLfNLP")` → `read_file(cmid=…, filename=…)`; for long PDFs use `pages="1-10"`.
- Pictures embedded in pages/labels (e.g. a schedule screenshot) appear as `![name](url)` —
  `read_file(url=…)` shows the image to the model.
- `get_activity(cmid)` on a `page` or `book` returns the full text.

## Exam preparation

`exam_prep` prompt, or: `search(query="exam", scope="SE2")` for the rules, `read_file` for slides,
`get_quiz_review(quiz=<cmid>, only_incorrect=true)` for past mistakes, `get_quiz_questions(quiz=<cmid>)` for a quiz
attempt that is open right now, `search(query="…", scope="all")`
to find a topic in older courses.

## Scripts and automation

Every tool is callable from the shell, so you can cron a digest or feed other tools:

```bash
moodle-mcp call get_deadlines days=3 > ~/deadlines.md
moodle-mcp call get_updates remember=false since=1d
moodle-mcp call download_files course=051050 dest=~/Uni/SE2/materials
```

## Example: a Claude Code skill for a study folder

If you keep one folder per course with a README "passport", a project skill can drive the whole
sync through the MCP — no browser needed. A minimal `.claude/skills/sync/SKILL.md`:

```markdown
---
name: sync
description: Update course passports and the dashboard from Moodle. Use on "sync", "what's new", "update SE2".
argument-hint: "[course key]"
---

Courses: SE2 → Moodle course id 123456 → folder SE2/, NLP → 234567 → NLP/ (adapt to your courses).

1. `mcp__moodle__get_updates` (all courses) — what changed since the last sync.
2. For each course in scope:
   - `mcp__moodle__get_course(course=<id>)` for new sections/rules,
   - `mcp__moodle__get_activity_status(course=<id>)` and `mcp__moodle__get_grades(course=<id>)`,
   - `mcp__moodle__get_announcements(course=<id>, days=30)` → append new ones to the passport log,
   - `mcp__moodle__download_files(course=<id>, dest="<folder>/materials")`.
3. `mcp__moodle__get_deadlines(days=30)` → refresh the deadline tables (keep past rows with their final status).
4. Rebuild the dashboard; report what is new, what is due within 7 days, and anything that failed.

Never submit anything in Moodle — the MCP is read-only anyway.
```
