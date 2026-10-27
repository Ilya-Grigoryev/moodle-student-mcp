# Moodle Student MCP v2 — Technical Specification

Status: implemented in 2.0.0 (see CHANGELOG.md; tool reference in docs/TOOLS.md) · Target: `moodle-student-mcp` 2.0.0 · Reference instance: moodle.univie.ac.at (Moodle 5.2)

## 1. Goal

A student should be able to ask an LLM anything about their Moodle — deadlines, materials, grades,
announcements, quiz results, what changed since last week — and get an answer without opening a
browser. The server must be good enough to replace the "open Moodle in Chrome and click around" sync
workflow entirely, for current **and** past semesters.

v1 had four thin wrappers (`get_site_info`, `get_enrolled_courses` capped at 12 courses,
`get_course_contents`, `get_course_assignments`). They forced the model to chain discovery calls,
returned raw-ish JSON, dropped dates/descriptions, and could not download or read any file.

## 2. Findings from analysing the live instance

Everything below was verified against the real Moodle with a mobile-app token (`moodle_mobile_app`
service, 460 web-service functions available).

| Area | Finding | Consequence |
|---|---|---|
| Courses | `core_enrol_get_users_courses` returns every enrolment (43 courses over 7 semesters). Shortname encodes semester + LV number: `WS2026-051050-4`, `SS2025-051031-3`; fullname starts with `2026W 051050 …`. A `semester` custom field exists (`core_course_get_courses_by_field`). | Semester grouping/filtering is reliable. "Current semester" = derived from today's date (W: Oct–Feb, S: Mar–Sep). |
| Course structure | `core_course_get_contents` gives sections (with HTML summaries), modules (name, `modname`, `dates[]` with open/close/due labels, `description` for labels, `contents[]` files with size/mtime/url). | One call is enough for a full course outline incl. dates of plugin activities without their own API (grouptool, organizer…). |
| Languages | Names contain `{mlang de}…{mlang}{mlang en}…{mlang}`. Passing `moodlewssettingfilter=true` + `moodlewssettinglang=en` makes Moodle resolve them and return English labels. | Always send both settings; language configurable. |
| Module types in the wild | resource 695, label 186, assign 152, url 107, forum 100, quiz 86, page 80, folder 56, zoom 54, checkmark 41, bigbluebuttonbn 36, feedback 35, offlinequiz 32, book 30, lti 25, choice 24, streamlti 24, moodleoverflow 20, organizer 19, lesson 18, grouptool 14, extserver 12, attendance 11, questionnaire 9, workshop 9, consentform 8, … | `get_activity` needs dedicated handlers for the common ones and a generic fallback for the rest. |
| Activity overview | `core_courseformat_get_overview_information(courseid, modname)` (Moodle 5 "Activities" page) returns per-activity status columns: due date, submission status, grade, "responded", offline-quiz grade, etc. — also for third-party plugins. | Universal "what did I submit / what's my grade" per course without per-plugin code. |
| Deadlines | `core_calendar_get_action_events_by_timesort` = the dashboard timeline (open to-dos only, incl. grouptool registration, quiz close, choice close). `core_calendar_get_calendar_events` needs explicit course ids, otherwise returns nothing; it includes organizer appointments (`eventtype=Appointment`). | Deadlines tool uses the timeline API; calendar tool passes course ids. |
| Quizzes | `mod_quiz_get_user_attempts` exposes attempt state. An attempt in state `inprogress` is **not submitted** — some courses require pressing "Submit" manually. `mod_quiz_get_attempt_review` returns per-question HTML with selected options and correct answers (inside popover attributes). | Deadlines must flag unsubmitted attempts. A quiz-review parser turns the HTML into Q/A text (great for exam prep). |
| Grades | `gradereport_overview_get_course_grades` (final grade for all courses, 1 call) and `gradereport_user_get_grade_items` (items, weights, ranges, feedback HTML). | Two levels: semester overview and per-course detail. |
| Forums | `mod_forum_get_forums_by_courses`, `mod_forum_get_forum_discussions`, `mod_forum_get_discussion_posts` work. `moodleoverflow` has no WS API. | Announcements across courses = news forums of the selected courses. |
| Files | `webservice/pluginfile.php/...?token=` downloads work (no browser download blocking). | Direct download + text extraction (PDF, DOCX, PPTX, IPYNB, HTML, text). |
| Updates | `core_course_get_updates_since` **fails** on this instance (DB error). `core_course_check_updates` works per module. | "What's new" combines `check_updates`, file mtimes, post dates, grade dates, notifications and a local snapshot. |
| Batching | `tool_mobile_call_external_functions` runs several WS functions in one HTTP request (4 calls ≈ 0.3 s). | Used for fan-out (attempts per quiz, overview per module type, …). |
| Broken params | `mod_checkmark_get_checkmarks_by_courses` crashes when `courseids` is given; works without it (returns all). | Call without filter and filter client-side. |
| Mobile plugins | `tool_mobile_get_content` renders attendance and zoom summaries. | Used by `get_activity` for these types. |
| Web-only data | grouptool registration state/free places, moodleoverflow threads, organizer slot lists, download center. Login is SAML SSO (`typeoflogin=3`), so the server cannot log in with a password. | Optional web session via `tool_mobile_get_autologin_key` (needs the *private token* issued together with the token right after a fresh login). Everything else must work without it. |

## 3. Design principles

1. **One call answers one question.** No mandatory discovery chain: user id, course ids, forum ids
   are resolved internally. `course` arguments accept an id, shortname, LV number (`051050`),
   acronym (`SE2`, `DLfNLP`) or part of the name.
2. **Token-efficient, readable output.** Compact Markdown, not raw JSON. Every object carries the id
   needed for the next call (`cmid`, `discussion`, `attempt`) and long text is truncated with an
   explicit hint on how to get the rest.
3. **Absolute + relative dates** in the user's timezone (default `Europe/Vienna`):
   `Mon 05.10.2026 14:00 (in 2 days)`.
4. **Read-only towards Moodle.** The server never submits, posts, marks as read or triggers
   `*_view_*` logging functions. The only side effect is writing downloaded files to a local folder
   the user chose.
5. **Fast.** Async HTTP with connection pooling, request batching, short TTL cache (dedupes calls
   within one conversation turn), on-disk cache for downloaded files.
6. **Helpful failures.** Expected problems (invalid token, unknown course, ambiguous name, feature
   needs web session) come back as tool errors with a concrete next step.
7. **Generic, not univie-only.** Works with any Moodle ≥ 4.0 that enables the mobile web service;
   univie specifics (semester naming) degrade gracefully elsewhere.

## 4. Architecture

```
src/moodle_mcp/
  __main__.py      python -m moodle_mcp
  cli.py           moodle-mcp [serve|setup|check|call|tools]
  browser_login.py setup --browser: Chromium login (optional Playwright), launch-callback decoding/binding
  system_browser.py setup --system-browser: macOS default browser + temporary URL handler
  config.py        settings from env + ~/.config/moodle-mcp/config.json
  client.py        async Moodle WS client: params flattening, settings, errors, retries, batch, cache, downloads
  cache.py         TTL cache
  courses.py       course index: semester parsing, acronyms, resolution
  timeutil.py      timezone, date formatting, parsing of user date input
  html.py          HTML → Markdown/plain text, Moodle-specific cleanup
  quizreview.py    quiz question HTML → Q/A text
  extract.py       file → text (pdf/docx/pptx/xlsx/odt/ipynb/html/text)
  state.py         state dir: snapshots for "what's new", download manifests
  web.py           optional web session (autologin) + page → Markdown
  server.py        MCPServer instance, instructions, prompts
  tools/           one module per tool group, registered on import
```

Runtime: Python ≥ 3.10, `mcp` 2.x (`MCPServer`), `httpx`, `beautifulsoup4`, `markdownify`, `pypdf`.

## 5. Configuration

| Variable / key | Required | Default | Meaning |
|---|---|---|---|
| `MOODLE_URL` / `url` | yes | — | Site root (`https://moodle.univie.ac.at`); a full `/webservice/rest/server.php` URL is accepted too |
| `MOODLE_TOKEN` / `token` | yes | — | Mobile web-service token |
| `MOODLE_PRIVATE_TOKEN` / `private_token` | no | — | Enables the web session (web-only pages) |
| `MOODLE_LANG` / `lang` | no | `en` | Language for Moodle strings and multilang content |
| `MOODLE_TIMEZONE` / `timezone` | no | `Europe/Vienna` | Output timezone |
| `MOODLE_DOWNLOAD_DIR` / `download_dir` | no | `~/Downloads/moodle` | Default target for `download_files` |
| `MOODLE_MCP_STATE_DIR` | no | `~/.local/state/moodle-mcp` | Snapshots, manifests, cookies |
| `MOODLE_MCP_CACHE_DIR` | no | `~/.cache/moodle-mcp` | Downloaded-file cache |

Env vars override the config file. `moodle-mcp setup` writes the config file with mode `0600`.

## 6. Tools

All tools are read-only (annotation `readOnlyHint=true`) except `download_files`
(writes local files, `destructiveHint=false`).

### 6.1 Discovery

**`status()`** — connection check: user, site name/version, token validity, number of WS
functions, current semester, web-session availability, config/state paths.

**`list_courses(semester="current", query=None)`**
- `semester`: `current` · `past` · `all` · a code like `2026W`/`2025S`.
- Output grouped by semester: id, LV number, title, my groups, final grade, last access, favourite.
- Data: `core_enrol_get_users_courses`, `gradereport_overview_get_course_grades`, `core_group_get_course_user_groups(courseid=0)`.

**`get_course(course, section=None, compact=False)`**
- Header: title, semester, LV number, id, link, teachers (`contacts`), my groups, start/end, final grade.
- Body: every section with summary (Markdown) and modules: `type`, `name`, `cmid`, dates,
  files (name, size), label text, URL targets, availability restrictions, hidden/stealth.
- `section` (number or name fragment) shows one section in full; `compact=True` gives only the outline.

**`get_activity(cmid_or_url)`** — full details of one activity. Universal header (course, section,
type, dates, completion, link) plus a type-specific body:

| Type | Body |
|---|---|
| assign | description, attachments, submission settings, my submission (status, files, online text, time), grading status, grade, feedback comments/files, extension |
| quiz | description, open/close, time limit, attempts allowed, grading method, my attempts (state, times, grade), best grade, access rules |
| forum | description + latest discussions |
| resource / folder | files (name, size, modified) |
| page | content as Markdown |
| book | all chapters as Markdown |
| url | external link |
| label | text |
| choice | options, my answer, results if published |
| feedback | open/close, completed? |
| lesson, workshop, glossary, wiki, data, h5pactivity, scorm, lti/streamlti | key settings and (where available) content |
| bigbluebuttonbn | meeting info + recordings |
| zoom, attendance | mobile-rendered summary |
| checkmark | examples and which are checked |
| anything else (grouptool, organizer, moodleoverflow, offlinequiz, …) | description, dates, activity-overview status, related calendar events; page content via web session when enabled |

**`search(query, scope="current", types=None)`**
- Case/diacritics-insensitive search over course structure: section names/summaries, activity
  names/descriptions, label texts, file names, page contents, book chapter titles.
- `scope`: `current` · `all` · semester code · a course.
- Output grouped by course with snippets and `cmid`s.

### 6.2 Time

**`get_deadlines(days=14, course=None, overdue_days=7)`**
- Timeline to-dos (`core_calendar_get_action_events_by_timesort`, paginated) for all courses or one.
- Enriched with status: quiz attempts (**flags `inprogress` = not submitted**), assignment submission status.
- Grouped by day, sorted, with relative time and action required.

**`get_calendar(start=None, end=None, days=7, course=None)`**
- All calendar events (course, group, user, site, organizer appointments) from
  `core_calendar_get_calendar_events` for the current semester's courses (or one course).
- Accepts `YYYY-MM-DD`, `DD.MM.YYYY`, `today`, `tomorrow`, `+3d`, weekday names.

### 6.3 Progress

**`get_grades(course=None, semester="current")`**
- With `course`: all grade items (category, grade, range, percentage, weight, graded date, feedback) + course total.
- Without: final grade per course for the semester(s).

**`get_activity_status(course, types=None)`**
- Moodle 5 activity overview for every module type in the course: due date, submission status,
  grade, responded, etc. Tables per type, noise columns (toggles, buttons) removed.

**`get_quiz_questions(quiz=None, attempt_id=None, page=None, include_images=True)`**
- Questions of the attempt in progress (`mod_quiz_get_attempt_summary`): text, how to answer, all options
  (choices, dropdown options, drag items, drop zones with coordinates, markers, ordering), the saved answer,
  points, time left. Question pictures are returned as images: Moodle files, `data:` images, images on
  other hosts (https only, no private addresses, no Moodle credentials) and inline SVG diagrams drawn to
  PNG (resvg). SVG code listings become code blocks.
- Never starts, saves or submits an attempt. No attempt in progress → points to Moodle / `get_quiz_review`.

**`get_quiz_review(quiz=None, attempt_id=None, which="last", only_incorrect=False)`**
- Question-by-question review: text, my answer, correct answer (if released), feedback, marks.
- `quiz` = cmid/URL; `which` = `last` · `best` · `all`.

### 6.4 Communication

**`get_announcements(course=None, days=30, limit=20)`** — posts from news forums of the current
semester's courses (or one course), newest first, full text, attachments.

**`get_forum(forum=None, course=None, page=0, per_page=25)`** — with `course`: all forums of the
course with discussion counts; with `forum` (cmid/URL): discussions (subject, author, dates, replies, unread, pinned).

**`get_discussion(discussion_id, max_posts=100)`** — whole thread, nested, Markdown, attachments.

**`get_notifications(unread_only=False, limit=20)`** — Moodle notifications.

**`get_messages(conversation_id=None, limit=20)`** — conversations overview or one conversation.

### 6.5 Files

**`list_files(course, section=None, include_videos=True, extensions=None)`** — every file of
resources, folders, pages, books and assignment attachments: section, activity, `cmid`, name,
size, modified.

**`read_file(cmid=None, filename=None, url=None, pages=None, offset=0, max_chars=30000)`**
- Downloads (cached by URL + mtime) and extracts text: PDF (page markers, page ranges), DOCX,
  PPTX (slide markers), XLSX, ODT/ODP, IPYNB, HTML, plain text/code. Images are returned as images.
- Long results are paginated (`offset`) with a "continue with offset=N" hint.

**`download_files(course, dest=None, section=None, include_videos=False, extensions=None, layout="flat", sanitize_names=True, dry_run=False)`**
- Incremental sync into a local folder with a manifest (`.moodle-sync.json`): new and changed files
  are downloaded, unchanged ones skipped, nothing is ever deleted.
- `layout`: `flat` or `sections` (subfolder per section). Name collisions are resolved deterministically.
- Report: new / updated / unchanged / skipped (videos, filters) / failed, with sizes.

### 6.6 Changes

**`get_updates(since=None, course=None)`**
- `since`: date, `3d`, `1w` or omitted = time of the previous `get_updates` run (fallback 7 days).
- Per course: new/changed activities (`core_course_check_updates` + local snapshot diff), new or
  updated files, new forum posts, new grades, new calendar to-dos; plus unread notifications.
- Stores a snapshot and the run time in the state dir.

### 6.7 Web session (optional)

**`fetch_page(url, max_chars=20000)`** — fetches any Moodle page with the web session and returns its
main region as Markdown. Used internally by `get_activity` for web-only plugins. Without a
private token the tool explains how to enable it.

## 7. Prompts

Exposed as slash commands in clients that support MCP prompts:
`weekly_briefing` (deadlines + announcements + updates), `course_sync(course)` (full status of one
course), `exam_prep(course)` (materials + quiz reviews → study plan), `semester_report(semester)`.

## 8. CLI

| Command | Purpose |
|---|---|
| `moodle-mcp` / `moodle-mcp serve` | Run the MCP server on stdio |
| `moodle-mcp setup` | Guided token setup: prints the launch URL, decodes the pasted `moodlemobile://token=…`, validates, writes config (0600), prints client config snippets |
| `moodle-mcp setup --browser` / `--system-browser` | Same, but captures the token automatically: isolated Chromium (optional `browser` extra) or the macOS default browser via a temporary URL handler; the callback must match the site and this attempt's passport |
| `moodle-mcp check` | Diagnostics (config, token, functions, web session) |
| `moodle-mcp tools` | List tools with descriptions |
| `moodle-mcp call <tool> [key=value …]` | Run any tool from the shell (scripting, debugging) |

## 9. Non-functional requirements

- **Performance:** typical tool call < 2 s on a warm cache; fan-out uses batching/concurrency
  (≤ 6 parallel requests).
- **Caching:** in-memory TTL (site info 1 h, course list 10 min, contents/grades/events 60 s);
  file cache keyed by URL + mtime, size-limited.
- **Robustness:** retries with backoff for network errors/5xx; Moodle `exception` payloads mapped to
  readable errors; invalid token → explicit "run `moodle-mcp setup`".
- **Security:** token only in env or a 0600 config file; never logged; download URLs in output are
  shown without the token; downloads restricted to the configured site host.
- **Output limits:** every tool caps its output (default ≈ 25k chars) and says how to page.
- **Compatibility:** Python 3.10–3.14, macOS/Linux/Windows paths.

## 10. Testing & documentation

- Unit tests (pytest) for parsing/formatting: semester detection, course resolution, date parsing,
  HTML → Markdown, quiz review parser, file extraction, manifest logic. Fixtures are anonymised.
- Tool tests against a mocked Moodle (`respx`) through the in-process MCP client.
- Opt-in live smoke test (`MOODLE_LIVE_TESTS=1`) that calls every tool against the real site.
- README: features, install (uv/pip), token setup, Claude Code/Cursor/Claude Desktop config, tool
  reference with examples, troubleshooting, privacy notes. `docs/TOOLS.md`: full tool reference.

## 11. Acceptance criteria

1. Every tool in §6 works against the live instance for current and past courses.
2. A "weekly sync" (deadlines, announcements, grades, new materials download) needs no browser.
3. Unsubmitted quiz attempts are surfaced in `get_deadlines`.
4. Files of a course can be downloaded incrementally and read as text.
5. Tests pass; `moodle-mcp check` is green; README lets a new student set it up in < 10 minutes.
