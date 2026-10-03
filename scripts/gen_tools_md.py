"""Generate docs/TOOLS.md from the registered tools (parameters come from the real schemas)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from moodle_mcp.server import mcp

GROUPS = [
    ("Discovery", ["status", "list_courses", "get_course", "get_activity", "search"]),
    ("Time", ["get_deadlines", "get_calendar"]),
    ("Progress", ["get_grades", "get_activity_status", "get_quiz_review"]),
    ("Communication", ["get_announcements", "get_forum", "get_discussion", "get_notifications", "get_messages"]),
    ("Files", ["list_files", "read_file", "download_files"]),
    ("Changes", ["get_updates"]),
    ("Web session (optional)", ["fetch_page"]),
]

EXAMPLES: dict[str, str] = {
    "list_courses": """\
## 2026W (4)
| id | LV | Course | My groups | Grade | Last access |
|---|---|---|---|---|---|
| 471264 | 051050 | Software Engineering 2 | WS2026-051050-1 | – | Sat 03.10.2026 |
| 469121 | 052316 | Deep Learning for Natural Language Processing | WS2026-052316-1 | – | Sat 03.10.2026 |""",
    "get_course": """\
# Software Engineering 2 (2026W, LV 051050)
id 471264 · WS2026-051050-4 · https://moodle.univie.ac.at/course/view.php?id=471264
Teachers: … · My groups: WS2026-051050-1 · Period: Thu 01.10.2026 – Sun 28.02.2027

## 3 · Introductory Quiz
**Taking part in this quiz is mandatory. …**
- quiz **Introductory Quiz** · cmid 19971691 · Opened: Thu 01.10.2026 00:00 · Closes: Fri 09.10.2026 23:59 (in 6 days)

## 5 · Lectures
- folder **01 Software Quality** · cmid 19971694
  files: 01_software_quality.pdf (617 KB), 01_software_quality.mp4 (32.3 MB), …""",
    "get_deadlines": """\
# Deadlines: next 14 days — 6 upcoming
⚠ 1 quiz attempt(s) started but not submitted — they will not count until submitted.

## Mon 05.10.2026 (in 2 days)
- **14:00** · Software Engineering 2 · quiz **1st Worksheet - Software Quality** closes · cmid 19971720 · ⚠ attempt 1 IN PROGRESS — NOT SUBMITTED (started 03.10. 11:02)

## Thu 08.10.2026 (in 5 days)
- **23:59** · Software Engineering 2 · grouptool **Switch pair registration** registration deadline · cmid 19971692 · action: Register""",
    "get_grades": """\
# Grades — 2026S Mensch-Computer-Interaktion [462852]
Course total: **81.50** (range 0–100, 82%)
| Item | Grade | Range | % | Weight | Graded |
|---|---|---|---|---|---|
| A1 | 9.00 | 0–10 | 90% | 10.00 % | Tue 14.04.2026 |
| Literaturtest · cmid 19812895 | 7.50 | 0–10 | 75% | 10.00 % | Sat 18.04.2026 |
## Feedback
- **A2**: Clear analysis; comments in the code are missing.""",
    "get_quiz_review": """\
### Q1 · multianswer · 2.00/3 · Partially correct
| It uses the downward closure property. | [Apriori ✗ — correct: Both] |
| It has a bigger IO-overhead. | [Apriori ✓] |

### Q2 · multichoice · 2.00/2 · Correct
- [x]✓ a. If an itemset is infrequent, then all its supersets are also infrequent.
- [ ] b. If an itemset is infrequent, then all its supersets are frequent.""",
    "read_file": """\
# 01_software_quality.pdf
cmid 19971694 «01 Software Quality» · 617 KB · 13 pages

--- page 2/13 ---
Overview: Software Quality
▪ Software has both external and internal quality characteristics …""",
    "download_files": """\
# Download — 2026W 051050 Software Engineering 2 → ~/Uni/SE2/materials
Downloaded 5 new + 0 updated files (2.0 MB); 0 unchanged.
- new: 01_software_quality.pdf (617 KB)
- new: SW_Quality_worksheet.pdf (530 KB)
Skipped: 2 videos (60.7 MB; include_videos=true to get them)""",
    "get_updates": """\
# What's new since Sat 26.09.2026 13:17 (your last check)
## Deep Learning for Natural Language Processing [469121]
- NEW quiz «❓Quiz 01» · cmid 20015750 · section 1 «Week 1» · Closes: Wed 07.10.2026 23:59
- file 01_Intro_NLP_Embeddings.pdf (5.5 MB) in «📓 Lecture Slides 01 …» · cmid 20014865 · 30.09. 16:53
- post in «📢 Announcements»: **Welcome …** by …, 30.09. 17:04 · discussion 4028142 — Dear Students, …""",
}


def _type(schema: dict[str, Any]) -> str:
    if "anyOf" in schema:
        return " \\| ".join(_type(s) for s in schema["anyOf"])
    t = schema.get("type", "any")
    if t == "array":
        return f"list[{_type(schema.get('items', {}))}]"
    if "enum" in schema:
        return " \\| ".join(json.dumps(v) for v in schema["enum"])
    return {"string": "str", "integer": "int", "boolean": "bool", "null": "null", "number": "float"}.get(t, t)


async def main() -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    lines = ["# Tool reference", "",
             "Generated from the server's tool schemas by `scripts/gen_tools_md.py`. All tools are read-only "
             "towards Moodle; `download_files` writes to a local folder.", "",
             "Conventions: `course` accepts a course id, LV number (`051050`), acronym (`SE2`), shortname or part of "
             "the title, optionally with a semester (`SE1 2025W`). `cmid` is the activity id shown in every listing. "
             "Dates accept `YYYY-MM-DD`, `DD.MM.YYYY`, `today`, `tomorrow`, weekday names, `+3d`, `-1w`.", ""]
    documented = set()
    for group, names in GROUPS:
        lines.append(f"## {group}\n")
        for name in names:
            tool = tools[name]
            documented.add(name)
            lines.append(f"### `{name}`\n")
            lines.append(" ".join((tool.description or "").split()) + "\n")
            props = (tool.input_schema or {}).get("properties", {})
            required = set((tool.input_schema or {}).get("required", []))
            if props:
                lines.append("| Parameter | Type | Default | Description |\n|---|---|---|---|")
                for pname, p in props.items():
                    default = "required" if pname in required else json.dumps(p.get("default"))
                    lines.append(f"| `{pname}` | {_type(p)} | {default} | {p.get('description', '')} |")
                lines.append("")
            if name in EXAMPLES:
                lines.append("Example output:\n\n```markdown\n" + EXAMPLES[name] + "\n```\n")
    missing = set(tools) - documented
    if missing:
        raise SystemExit(f"Tools missing from GROUPS: {sorted(missing)}")
    prompts = await mcp.list_prompts()
    lines.append("## Prompts\n")
    for p in prompts:
        args = ", ".join(a.name + ("" if a.required else "?") for a in p.arguments or [])
        lines.append(f"- **`{p.name}`**({args}) — {p.description}")
    out = Path(__file__).resolve().parent.parent / "docs" / "TOOLS.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
