# Moodle MCP Server

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Hey Uni Wien students — you'll like this.** This MCP server connects **our Moodle** (moodle.univie.ac.at) with an LLM. Use Cursor (or another MCP client), and you can ask in plain language about your courses, materials, and deadlines; the AI uses Moodle's API through this server to answer.

## What it can do

| Tool | Description |
|------|-------------|
| **get_site_info** | Your Moodle identity: `userid`, `username`, `fullname`. Use `userid` for the other tools. |
| **get_enrolled_courses** | List of courses you're enrolled in (recent ones, up to 12). |
| **get_course_contents** | Course structure: sections, module names, and links to materials (resources, PDFs, etc.). |
| **get_course_assignments** | Assignments for a course: descriptions and due dates (when Moodle exposes them via the API). |

## Example: asking the LLM about a course

Once the server is configured in Cursor, you can ask in chat. The LLM will call the Moodle tools and summarize the result. Example with a fictional course:

**You:** What's in course 780012 and what do I need to hand in?

**LLM** (after calling `get_course_contents` and `get_course_assignments`):

> **Course:** 2025W 123456 Introduction to Machine Learning  
> **Course ID:** 780012  
>
> **Structure — 5 sections:**  
> 1. **General** — Announcements forum  
> 2. **Week 1–2: Basics** — Slides, reading list, link to first lecture  
> 3. **Week 3–4: Supervised Learning** — Lecture notes, practice sheet 1  
> 4. **Assignments** — Assignment 1 and 2 descriptions and submission links  
> 5. **Exam** — Scope and dates  
>
> **Assignments:**  
> - **Assignment 1 — Linear regression** (due 2026-04-01): Implement and report.  
> - **Assignment 2 — Classification** (due 2026-05-01): Train a classifier on the provided dataset.

---

**Note:** This project has just started and will be actively developed when time allows. Many more features could be added (e.g. calendars, grades, forums), and the server could be used together with tools like Notion for planning and notes.

## Requirements

- **Python 3.10+**
- Uni Wien Moodle account and a [web service token](https://docs.moodle.org/en/Web_services#Creating_a_token) (if your faculty/admin has enabled it)

## Installation

```bash
git clone https://github.com/Ilya-Grigoryev/moodle-student-mcp.git
cd moodle-student-mcp
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -e .
```

Without editable install:

```bash
pip install -r requirements.txt
# Run with: PYTHONPATH=src python -m moodle_mcp.main
```

## Configuration (Cursor)

The server needs **MOODLE_URL** and **MOODLE_TOKEN** in the environment. In Cursor you set them in the MCP config so the token never lives in a file.

### Getting your token

You use your existing browser session, no extra software and no “real” hack - Moodle simply puts the token in a redirect URL, and you capture it.

1. **Be logged in to Moodle** in your browser (e.g. via Uni Wien SSO).
2. **Open DevTools** (F12) → **Network** tab, enable **Preserve log**.
3. **Open the mobile launch URL** in the same browser (same site as your Moodle):  
   `https://moodle.univie.ac.at/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345`
4. **Catch the redirect:** Moodle will redirect to a URL that starts with `moodlemobile://token=...`. In the Network tab, find that redirect (or copy the target URL from the address bar if it briefly appears there). The token is in that URL.
5. **Extract the key:** The value is often Base64‑encoded. Decode it and take the 32‑character token (the core part Moodle uses for the REST API). That’s your **MOODLE_TOKEN**.

So: you “trick” Moodle into issuing a mobile token for your current session and read it from the redirect — no admin rights required.

### Cursor MCP setup

1. **MOODLE_URL** for Uni Wien: `https://moodle.univie.ac.at/webservice/rest/server.php`.
2. Open Cursor **Settings → MCP** (or edit `~/.cursor/mcp.json` on macOS).
3. Add a server entry like this (use your paths and token):

```json
"moodle-student-mcp": {
  "command": "/path/to/moodle-student-mcp/.venv/bin/python",
  "args": ["-m", "moodle_mcp.main"],
  "cwd": "/path/to/moodle-student-mcp",
  "env": {
    "PYTHONPATH": "/path/to/moodle-student-mcp/src",
    "MOODLE_URL": "https://moodle.univie.ac.at/webservice/rest/server.php",
    "MOODLE_TOKEN": "your_ws_token_here"
  }
}
```

Use the **full path** to the repo and to the venv's Python. Save and reload MCP (or restart Cursor).

## Running

With Cursor you don't run the server yourself — Cursor starts it from `mcp.json`. For local testing:

```bash
MOODLE_URL=... MOODLE_TOKEN=... PYTHONPATH=src python -m moodle_mcp.main
```

## Project structure

| File | Purpose |
|------|---------|
| **config.py** | Reads `MOODLE_URL` and `MOODLE_TOKEN` from the environment (e.g. from Cursor's mcp.json). |
| **client.py** | HTTP client and `call_moodle_api` with Moodle error handling. |
| **tools.py** | FastMCP server and the four tools. |
| **main.py** | Entry point (`mcp.run()`). |

## License

MIT — see [LICENSE](LICENSE).
