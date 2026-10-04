# Changelog

## Unreleased

- New tool `get_quiz_questions`: the questions of a quiz attempt in progress — text, how to answer, every
  option (choices, dropdown options, drag items, drop zones on images, markers, ordering, essay), the
  answer saved so far, points and time left. Question pictures come back as images — Moodle files,
  embedded `data:` images, images hosted elsewhere (e.g. pasted from Google Docs; fetched over https
  without any Moodle credentials, never from private addresses) and inline SVG diagrams (e.g. UML from
  draw.io, drawn to PNG). Code listings drawn as SVG text become code blocks. Read-only: never starts,
  saves or submits an attempt.
- Quiz rendering: italic text (e.g. translations, formula symbols) is no longer dropped together with icons;
  drag & drop onto images shows its drop zones and placed items; SVGs in reviews show code / diagram labels;
  embedded and externally hosted pictures are marked as `[image]` instead of disappearing.
- `ttl=0` client calls always ask Moodle instead of returning a cached value.
- New dependency: `resvg-py` (prebuilt wheels, no system libraries) for drawing SVG diagrams.

## 2.0.0

Complete rewrite on the MCP Python SDK 2.x (`MCPServer`).

- **20 tools** instead of 4: courses of all semesters, course structure with section texts, universal
  activity details (assignments, quizzes, pages, books, forums, choices, BBB recordings, attendance,
  checkmarks, …), search, deadlines with submission status, calendar, grades with feedback, Moodle 5
  activity overview, quiz review (questions, my answers, correct answers), announcements, forums,
  discussions, notifications, messages, file listing, file reading (PDF/PPTX/DOCX/XLSX/ODT/notebooks/
  HTML/code/images), incremental file download, "what's new" with snapshots, optional web session.
- Four prompts: `weekly_briefing`, `course_sync`, `exam_prep`, `semester_report`.
- Flexible course references (id, LV number, acronym like `SE2`, name, `SE1 2025W`), semester detection,
  course exclusion (`exclude_courses`, or hide the course in Moodle).
- Async client with request batching (`tool_mobile_call_external_functions`), short-lived cache,
  retries, server-side multilang filtering and language selection.
- Compact Markdown output with ids for follow-up calls and explicit paging hints.
- CLI: `moodle-mcp setup` (guided token capture), `check`, `tools`, `call <tool> key=value…`.
- Config file `~/.config/moodle-mcp/config.json` (0600) in addition to environment variables.
- Test suite against a fake Moodle, live smoke test, generated tool reference (`docs/TOOLS.md`).

## 0.1.0

Initial version: `get_site_info`, `get_enrolled_courses`, `get_course_contents`, `get_course_assignments`.
