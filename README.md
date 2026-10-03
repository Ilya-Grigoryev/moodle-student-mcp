# Moodle Student MCP

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP SDK 2.x](https://img.shields.io/badge/MCP-2.x-purple.svg)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An [MCP](https://modelcontextprotocol.io) server that gives Claude (or any MCP client) **read access to
your Moodle as a student**: courses of every semester, deadlines with submission status, grades and
teacher feedback, announcements and forums, quiz reviews, files (read as text or synced to a folder)
and "what changed since last time".

Built and tested against the University of Vienna's Moodle (moodle.univie.ac.at, Moodle 5.2) but works
with any Moodle that has the mobile web service enabled — which is nearly all of them.

```
You:    What do I have to do this week?
Claude: (get_deadlines) Mon 05.10. 14:00 — SE2 Worksheet 1 closes, not attempted yet.
        Wed 07.10. 23:59 — DLfNLP Quiz 01 … Thu 08.10. — your SE2 group-switch registration …
```

## What you can ask

- *"What's due in the next two weeks? Did I forget to submit anything?"* — flags quiz attempts that were started but never submitted.
- *"What's new in Moodle since yesterday?"* — new activities, uploaded slides, forum posts, grades with feedback.
- *"Summarise the announcements of all my courses."*
- *"How is SE2 graded and how many points do I have?"*
- *"Read the slides of lecture 3 in DLfNLP and make me a summary."*
- *"Download all PDFs of Software Engineering 2 into ~/Uni/SE2/materials."*
- *"Show me the questions I got wrong in the last Data Analysis quizzes and explain them."*
- *"Find everything about B-trees in my past courses."*

## Tools

| Tool | What it does |
|---|---|
| `status` | Connection check: user, site, current semester, configuration |
| `list_courses` | Courses by semester (`current`, `past`, `all`, `2025W`…) with LV number, group, final grade |
| `get_course` | Course page: teachers, groups, every section with text, activities (cmid, dates, files, restrictions) |
| `get_activity` | Everything about one activity — assignment submission/grade/feedback, quiz attempts, page & book text, forum discussions, choices, BBB recordings, attendance, checkmarks, … |
| `search` | Search activity names, descriptions, labels, section texts, file names and pages across courses |
| `get_deadlines` | Open to-dos (dashboard timeline) with status: not attempted / **in progress — not submitted** / submitted |
| `get_calendar` | All calendar events in a date range (incl. booked appointments) |
| `get_grades` | Per course: every grade item, %, weight, teacher feedback. Without course: semester overview |
| `get_activity_status` | Moodle's "Activities" overview: due date, submission status, grade per activity — also for plugins (offline quizzes, checkmarks, …) |
| `get_quiz_review` | Question-by-question review of quiz attempts: my answer, ✓/✗, correct answer, feedback |
| `get_announcements` | News-forum posts across current courses, full text |
| `get_forum` / `get_discussion` | Forums of a course, discussions of a forum, whole threads |
| `get_notifications` / `get_messages` | Moodle notifications and private/group messages |
| `list_files` | Every file of a course (resources, folders, pages, books, assignment attachments) |
| `read_file` | Text of PDF (per page), PPTX (per slide + notes), DOCX, XLSX, ODT, notebooks, HTML, code; images are shown to the model |
| `download_files` | Incremental sync of a course's files into a folder (skips unchanged, never deletes or overwrites your edits, videos opt-in) |
| `get_updates` | What changed since your last check (or a date): activities, files, posts, grades, notifications |
| `fetch_page` | Any Moodle page as Markdown via the optional web session |

Full reference with parameters and example output: [docs/TOOLS.md](docs/TOOLS.md). Everyday recipes (morning check, folder sync, exam prep, a sync skill for Claude Code): [docs/WORKFLOWS.md](docs/WORKFLOWS.md).

**Prompts** (slash commands in Claude Code / Claude Desktop): `weekly_briefing`, `course_sync <course>`,
`exam_prep <course> [topic]`, `semester_report [semester]`.

Course arguments are forgiving: `471264`, `051050`, `SE2`, `DLfNLP`, `software engineering 2`, or
`SE1 2025W` for an older run of a course.

## Install

Requires Python ≥ 3.10. With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv tool install git+https://github.com/Ilya-Grigoryev/moodle-student-mcp
# or from a local checkout:  uv tool install /path/to/moodle-student-mcp
```

or with pip / pipx:

```bash
pipx install git+https://github.com/Ilya-Grigoryev/moodle-student-mcp
```

This installs the `moodle-mcp` command. Update later with `uv tool upgrade moodle-student-mcp`
(or `uv tool install --force <path>` for a local checkout).

## Set up your token

Moodle's mobile app uses a personal *web-service token*. You can get the same token from your browser
session — no admin rights needed. Run:

```bash
moodle-mcp setup
```

It asks for your Moodle URL and prints a link like
`https://moodle.univie.ac.at/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=…`.

1. Log in to Moodle in your browser as usual.
2. Open DevTools (F12) → **Network** tab and tick **Preserve log**.
3. Open the printed link in the same tab. Moodle redirects to `moodlemobile://token=…` — the browser
   can't open that, but the request shows up in the Network list (Chrome: the last entry; you may also
   get an "Open app?" prompt). Copy the full `moodlemobile://token=…` URL (right click → *Copy URL*, or
   take it from the `Location` response header of the `launch.php` request).
   Tip: in Firefox the failed redirect usually leaves `moodlemobile://token=…` in the address bar,
   which is the easiest place to copy it from.
4. Paste it into `moodle-mcp setup`. It decodes the token, verifies it and saves it to
   `~/.config/moodle-mcp/config.json` (permissions `600`).

Then check everything: `moodle-mcp check`.

Tokens stay valid until you revoke them (Moodle → Preferences → Security keys) or the site expires them.

## Connect your MCP client

**Claude Code**

```bash
claude mcp add moodle -s user -- moodle-mcp
```

(use the absolute path from `which moodle-mcp` if `moodle-mcp` is not on the PATH of your shell).

**Claude Desktop** — `claude_desktop_config.json`:

```json
{ "mcpServers": { "moodle": { "command": "/absolute/path/to/moodle-mcp" } } }
```

**Cursor** — `~/.cursor/mcp.json`:

```json
{ "mcpServers": { "moodle": { "command": "/absolute/path/to/moodle-mcp" } } }
```

Instead of the config file you can pass everything as environment variables in the client config
(`"env": {"MOODLE_URL": "…", "MOODLE_TOKEN": "…"}`).

## Configuration

Environment variables override `~/.config/moodle-mcp/config.json` (keys in brackets).

| Variable (`key`) | Default | Meaning |
|---|---|---|
| `MOODLE_URL` (`url`) | — | Site root, e.g. `https://moodle.univie.ac.at` (the old `/webservice/rest/server.php` form works too) |
| `MOODLE_TOKEN` (`token`) | — | Web-service token |
| `MOODLE_PRIVATE_TOKEN` (`private_token`) | — | Optional, enables the web session (see below) |
| `MOODLE_LANG` (`lang`) | `en` | Language for Moodle strings and multi-language content |
| `MOODLE_TIMEZONE` (`timezone`) | `Europe/Vienna` | Timezone for all dates |
| `MOODLE_DOWNLOAD_DIR` (`download_dir`) | `~/Downloads/moodle` | Default folder for `download_files` |
| `MOODLE_EXCLUDE_COURSES` (`exclude_courses`) | — | Courses to leave out of cross-course overviews (ids, LV numbers, names; comma-separated or a JSON list) |
| `MOODLE_MCP_CONFIG` | `~/.config/moodle-mcp/config.json` | Config file location |
| `MOODLE_MCP_STATE_DIR` / `MOODLE_MCP_CACHE_DIR` | `~/.local/state/moodle-mcp` / `~/.cache/moodle-mcp` | Snapshots for `get_updates`, file cache |

Courses you hid in Moodle's *My courses* ("Remove from view") are excluded from overviews too. They
stay reachable by name or id.

## Use it from the shell

Every tool can be called directly — handy for scripts, cron jobs or debugging:

```bash
moodle-mcp tools                                  # list tools and parameters
moodle-mcp call get_deadlines days=7
moodle-mcp call get_course course=SE2 section=3
moodle-mcp call download_files course=SE2 dest=~/Uni/SE2/materials
moodle-mcp call search --json '{"query": "b-tree", "scope": "all"}'
```

## Web session (optional, experimental)

A few plugins have no web-service API at all (group self-registration `grouptool`, appointment
slots `organizer`, `moodleoverflow` forums). The server still reports their dates, status and calendar
events. To read their pages too, it can open a normal web session using Moodle's app auto-login, which
needs the *private token* that Moodle only hands out right after a fresh login:

1. Log out of Moodle and log in again.
2. Immediately run `moodle-mcp setup` and follow the steps — the pasted URL then contains the private
   token as well, and `fetch_page` / `get_activity` start showing those pages.

Moodle allows one auto-login every 6 minutes; the session cookie is cached in the state directory.

## Privacy & safety

- **Read-only.** The server never submits, posts, marks as read or changes anything in Moodle. The
  only write is `download_files` saving files to a folder you choose.
- Requests go only to your Moodle site. Files are downloaded only from that host.
- The token lives in your MCP client config or in a `600` config file and is never logged or shown
  in tool output.
- Remember that whatever a tool returns is sent to the LLM you are using.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Moodle rejected the token (invalidtoken)` | Token expired or revoked — run `moodle-mcp setup` again |
| `Moodle is not configured` | Run `moodle-mcp setup` or set `MOODLE_URL` + `MOODLE_TOKEN` |
| `… matches several courses` | Use the course id from `list_courses`, or add a semester: `"SE1 2025W"` |
| `Review … is not available` | The teacher hides quiz reviews (often until the quiz closes) |
| Lecture times missing in `get_calendar` | Many universities keep timetables outside Moodle (e.g. u:space) |
| Another Moodle than univie | Works the same; semester detection falls back to course start dates |

## Development

```bash
git clone https://github.com/Ilya-Grigoryev/moodle-student-mcp && cd moodle-student-mcp
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest            # unit + tool tests against a fake Moodle
.venv/bin/ruff check src tests
.venv/bin/python scripts/live_smoke.py   # optional: every tool against your real Moodle
```

Layout: `client.py` (async WS client: batching, cache, retries, downloads), `courses.py` (semesters,
course resolution), `tools/` (one module per tool group), `quizreview.py`, `extract.py`, `html.py`.
The design and the analysis it is based on are in [docs/SPEC.md](docs/SPEC.md).

## License

MIT — see [LICENSE](LICENSE).
