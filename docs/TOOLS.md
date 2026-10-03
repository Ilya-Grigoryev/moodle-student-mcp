# Tool reference

Generated from the server's tool schemas by `scripts/gen_tools_md.py`. All tools are read-only towards Moodle; `download_files` writes to a local folder.

Conventions: `course` accepts a course id, LV number (`051050`), acronym (`SE2`), shortname or part of the title, optionally with a semester (`SE1 2025W`). `cmid` is the activity id shown in every listing. Dates accept `YYYY-MM-DD`, `DD.MM.YYYY`, `today`, `tomorrow`, weekday names, `+3d`, `-1w`.

## Discovery

### `status`

Check the Moodle connection: user, site, available features, current semester and configuration.

### `list_courses`

List enrolled courses grouped by semester with id, LV number, group, final grade and last access.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `semester` | str | "current" | 'current', 'past', 'all' or a semester code like 2026W / 2025S |
| `query` | str \| null | null | Optional filter on title/shortname/LV number |

Example output:

```markdown
## 2026W (4)
| id | LV | Course | My groups | Grade | Last access |
|---|---|---|---|---|---|
| 471264 | 051050 | Software Engineering 2 | WS2026-051050-1 | – | Sat 03.10.2026 |
| 469121 | 052316 | Deep Learning for Natural Language Processing | WS2026-052316-1 | – | Sat 03.10.2026 |
```

### `get_course`

Course overview: teachers, groups, grade, then every section with summary and activities (type, name, cmid, dates, files, restrictions). Use the cmids with get_activity/read_file.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `course` | str \| int | required | Course id, LV number (051050), acronym (SE2), shortname or part of the title; add e.g. '2025W' to pick an older run |
| `section` | str \| int \| null | null | Show only this section (number or part of its name), untruncated |
| `compact` | bool | false | Outline only: no summaries, labels, file lists |

Example output:

```markdown
# Software Engineering 2 (2026W, LV 051050)
id 471264 · WS2026-051050-4 · https://moodle.univie.ac.at/course/view.php?id=471264
Teachers: … · My groups: WS2026-051050-1 · Period: Thu 01.10.2026 – Sun 28.02.2027

## 3 · Introductory Quiz
**Taking part in this quiz is mandatory. …**
- quiz **Introductory Quiz** · cmid 19971691 · Opened: Thu 01.10.2026 00:00 · Closes: Fri 09.10.2026 23:59 (in 6 days)

## 5 · Lectures
- folder **01 Software Quality** · cmid 19971694
  files: 01_software_quality.pdf (617 KB), 01_software_quality.mp4 (32.3 MB), …
```

### `get_activity`

Everything about one activity: description, dates, files and type-specific details — assignment submission/grade/feedback, quiz attempts, page/book text, forum discussions, choice options, BBB recordings, attendance, checkmarks… Plugins without an API get status, events and description.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `cmid` | str \| int | required | Activity id (cmid) or a Moodle activity URL (…/mod/<type>/view.php?id=…) |

### `search`

Search section names/summaries, activity names and descriptions, labels, file names and page texts across courses (not the contents of files — use read_file for that). Returns matches grouped by course with cmids and snippets.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | str | required | Words to find (all must match; case/diacritics-insensitive) |
| `scope` | str | "current" | 'current', 'all', a semester code (2025W) or one course reference |
| `limit` | int | 40 | Max results |

## Time

### `get_deadlines`

To-dos with deadlines (dashboard timeline): assignments, quizzes, group registrations, choices, feedbacks… with submission status — flags quiz attempts that were started but NOT submitted.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `days` | int | 14 | How many days ahead |
| `course` | str \| int \| null | null | Limit to one course (id/LV/acronym/name) |
| `overdue_days` | int | 7 | Also show open items that became due in the last N days |

Example output:

```markdown
# Deadlines: next 14 days — 6 upcoming
⚠ 1 quiz attempt(s) started but not submitted — they will not count until submitted.

## Mon 05.10.2026 (in 2 days)
- **14:00** · Software Engineering 2 · quiz **1st Worksheet - Software Quality** closes · cmid 19971720 · ⚠ attempt 1 IN PROGRESS — NOT SUBMITTED (started 03.10. 11:02)

## Thu 08.10.2026 (in 5 days)
- **23:59** · Software Engineering 2 · grouptool **Switch pair registration** registration deadline · cmid 19971692 · action: Register
```

### `get_calendar`

All calendar events in a date range (deadlines, openings, course/group/personal events, booked appointments), grouped by day.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `start` | str \| null | null | Start date: YYYY-MM-DD, DD.MM.YYYY, today, tomorrow, monday, +3d … (default today) |
| `end` | str \| null | null | End date (exclusive). Default start + days |
| `days` | int | 7 | Range length when end is not given |
| `course` | str \| int \| null | null | Only this course; default all courses of the current semester |

## Progress

### `get_grades`

Grades. With `course`: every grade item (grade, range, %, weight, date, teacher feedback) and the course total. Without: one line per course of the semester(s) with total and number of graded items.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `course` | str \| int \| null | null | Course (id/LV/acronym/name) for the detailed report; omit for an overview |
| `semester` | str | "current" | 'current', 'past', 'all' or a semester code like 2026W / 2025S |
| `only_graded` | bool | false | Detailed report: hide items without a grade yet |

Example output:

```markdown
# Grades — 2026S Mensch-Computer-Interaktion [462852]
Course total: **81.50** (range 0–100, 82%)
| Item | Grade | Range | % | Weight | Graded |
|---|---|---|---|---|---|
| A1 | 9.00 | 0–10 | 90% | 10.00 % | Tue 14.04.2026 |
| Literaturtest · cmid 19812895 | 7.50 | 0–10 | 75% | 10.00 % | Sat 18.04.2026 |
## Feedback
- **A2**: Clear analysis; comments in the code are missing.
```

### `get_activity_status`

Moodle's 'Activities' overview for a course: per activity due date, submission status, grade, responded, phase… — works for plugins without their own API too (offline quizzes, checkmarks…).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `course` | str \| int | required | Course id, LV number (051050), acronym (SE2), shortname or part of the title; add e.g. '2025W' to pick an older run |
| `types` | list[str] \| null | null | Activity types to include, e.g. ['assign','quiz']; default: all types with status info |
| `show_unavailable` | bool | false | Also list activities you cannot access (other groups etc.) |

### `get_quiz_review`

Review a quiz attempt question by question: question text, my answer ([x] choices / [filled gaps] ✓✗), marks, the correct answer and feedback when the teacher released them. Great for exam prep.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `quiz` | str \| int \| null | null | Quiz cmid or URL |
| `attempt_id` | int \| null | null | A specific attempt id (from get_activity) |
| `which` | "last" \| "best" \| "all" | "last" | Which finished attempt(s) to show |
| `only_incorrect` | bool | false | Only questions that were not fully correct |
| `include_feedback` | bool | true | Include feedback, explanations and correct answers |

Example output:

```markdown
### Q1 · multianswer · 2.00/3 · Partially correct
| It uses the downward closure property. | [Apriori ✗ — correct: Both] |
| It has a bigger IO-overhead. | [Apriori ✓] |

### Q2 · multichoice · 2.00/2 · Correct
- [x]✓ a. If an itemset is infrequent, then all its supersets are also infrequent.
- [ ] b. If an itemset is infrequent, then all its supersets are frequent.
```

## Communication

### `get_announcements`

Announcements (news forum posts) with full text, newest first — across all current courses or one course.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `course` | str \| int \| null | null | One course; default: all courses of the current semester |
| `days` | int | 30 | Only posts from the last N days |
| `limit` | int | 20 | Max posts |

### `get_forum`

List the forums of a course (with discussion/unread counts), or the discussions of one forum.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `forum` | str \| int \| null | null | Forum cmid or URL → list its discussions |
| `course` | str \| int \| null | null | Course → list all its forums |
| `page` | int | 0 | Discussion page (0 = newest) |
| `per_page` | int | 25 |  |

### `get_discussion`

A whole discussion thread: every post (nested), author, date, text and attachments.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `discussion_id` | int | required | Discussion id (from get_forum / get_announcements) |
| `max_posts` | int | 100 |  |

### `get_notifications`

Moodle notifications (graded submissions, quiz reminders, forum digests, …), newest first.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `unread_only` | bool | false | Only unread notifications |
| `limit` | int | 20 |  |

### `get_messages`

Private and group messages: list of conversations with the latest message, or one conversation.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `conversation_id` | int \| null | null | Show this conversation; omit to list conversations |
| `limit` | int | 20 |  |
| `unread_only` | bool | false | List only conversations with unread messages |

## Files

### `list_files`

All files of a course (resources, folders, pages, books, assignment attachments) with section, activity cmid, size and modification date. Read one with read_file(cmid, filename).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `course` | str \| int | required | Course id, LV number (051050), acronym (SE2), shortname or part of the title; add e.g. '2025W' to pick an older run |
| `section` | str \| int \| null | null | Only this section (number or name part) |
| `include_videos` | bool | true | Include video files |
| `extensions` | list[str] \| null | null | Only these extensions, e.g. ['pdf','zip'] |

### `read_file`

Download a Moodle file and return its text: PDF (page markers), PPTX (slides + notes), DOCX, XLSX, ODT/ODP, Jupyter notebooks, HTML, source code/text. Images are returned as images. Long texts are paged.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `cmid` | str \| int \| null | null | Activity cmid holding the file (resource, folder, assign, page…) |
| `filename` | str \| null | null | File name (or part of it) when the activity has several files |
| `url` | str \| null | null | Alternatively a Moodle file URL (…/pluginfile.php/…) e.g. from a post or page |
| `pages` | str \| null | null | PDF pages / PPTX slides to read, e.g. '1-5,9' (default all) |
| `offset` | int | 0 | Character offset to continue a long text |
| `max_chars` | int | 30000 | Max characters to return |

Example output:

```markdown
# 01_software_quality.pdf
cmid 19971694 «01 Software Quality» · 617 KB · 13 pages

--- page 2/13 ---
Overview: Software Quality
▪ Software has both external and internal quality characteristics …
```

### `download_files`

Sync a course's files into a local folder: downloads new and changed files, skips unchanged ones (tracked in .moodle-sync.json), never deletes anything and never overwrites files you edited (a newer Moodle version is saved next to them). Videos are skipped unless requested.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `course` | str \| int | required | Course id, LV number (051050), acronym (SE2), shortname or part of the title; add e.g. '2025W' to pick an older run |
| `dest` | str \| null | null | Target folder, absolute or relative to your home directory (created if missing). Default: <download_dir>/<semester> <course> |
| `section` | str \| int \| null | null | Only this section (number or name part) |
| `activity` | str \| int \| null | null | Only files of this activity (cmid) |
| `include_videos` | bool | false | Also download videos (can be large) |
| `extensions` | list[str] \| null | null | Only these extensions, e.g. ['pdf'] |
| `layout` | str | "flat" | 'flat' (all files in one folder) or 'sections' (subfolder per section) |
| `sanitize_names` | bool | true | Replace spaces in file names with '_' |
| `dry_run` | bool | false | Only show what would be downloaded |

Example output:

```markdown
# Download — 2026W 051050 Software Engineering 2 → ~/Uni/SE2/materials
Downloaded 5 new + 0 updated files (2.0 MB); 0 unchanged.
- new: 01_software_quality.pdf (617 KB)
- new: SW_Quality_worksheet.pdf (530 KB)
Skipped: 2 videos (60.7 MB; include_videos=true to get them)
```

## Changes

### `get_updates`

What changed in Moodle: new/changed activities, new or updated files, new forum posts, new grades (with feedback) and new notifications — per course, since the last check or a date.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `since` | str \| null | null | Since when: a date (YYYY-MM-DD / DD.MM.YYYY), '3d', '1w'… Default: since the previous get_updates call (else 7 days) |
| `course` | str \| int \| null | null | Only this course; default all courses of the current semester |
| `remember` | bool | true | Store this check as the new 'last seen' point |

Example output:

```markdown
# What's new since Sat 26.09.2026 13:17 (your last check)
## Deep Learning for Natural Language Processing [469121]
- NEW quiz «❓Quiz 01» · cmid 20015750 · section 1 «Week 1» · Closes: Wed 07.10.2026 23:59
- file 01_Intro_NLP_Embeddings.pdf (5.5 MB) in «📓 Lecture Slides 01 …» · cmid 20014865 · 30.09. 16:53
- post in «📢 Announcements»: **Welcome …** by …, 30.09. 17:04 · discussion 4028142 — Dear Students, …
```

## Web session (optional)

### `fetch_page`

Fetch a Moodle page as Markdown via the optional web session — for things without a web-service API (group registration, appointment slots, moodleoverflow threads…). Needs MOODLE_PRIVATE_TOKEN.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `url` | str | required | Any page of your Moodle site, e.g. …/mod/grouptool/view.php?id=… |
| `max_chars` | int | 20000 |  |

## Prompts

- **`weekly_briefing`**() — What is due, what is new, what needs action — across all current courses.
- **`course_sync`**(course) — Full status of one course: structure, deadlines, submissions, grades, announcements, materials.
- **`exam_prep`**(course, topic?) — Study plan from the course's slides and my past quiz mistakes.
- **`semester_report`**(semester?) — Grades and completion across a semester.
