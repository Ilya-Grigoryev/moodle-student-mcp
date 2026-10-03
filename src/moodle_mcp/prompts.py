"""Ready-made prompts (shown as slash commands in clients that support MCP prompts)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .toolkit import mcp


@mcp.prompt(title="Weekly Moodle briefing")
def weekly_briefing() -> str:
    """What is due, what is new, what needs action — across all current courses."""
    return (
        "Give me my Moodle briefing.\n"
        "1. Call get_deadlines(days=14) and get_updates().\n"
        "2. Call get_announcements(days=7).\n"
        "Then summarise in this order: (a) urgent — due within 3 days, especially quiz attempts that are started but "
        "NOT submitted; (b) the rest of the next two weeks; (c) new materials worth reading; (d) new grades and "
        "feedback; (e) announcements that require an action from me. Be concise, use dates with weekdays."
    )


@mcp.prompt(title="Sync one course")
def course_sync(course: Annotated[str, Field(description="Course id, LV number, acronym or name")]) -> str:
    """Full status of one course: structure, deadlines, submissions, grades, announcements, materials."""
    return (
        f"Build a complete, up-to-date status of the Moodle course '{course}'.\n"
        f"Use get_course('{course}'), get_activity_status('{course}'), get_grades(course='{course}'), "
        f"get_deadlines(course='{course}', days=150), get_announcements(course='{course}', days=120) and "
        f"list_files('{course}').\n"
        "Report: how the course works and is graded (from section texts/labels), every deadline with my status, "
        "points so far, important announcements, and which materials exist. Point out anything I still need to do."
    )


@mcp.prompt(title="Exam preparation")
def exam_prep(
    course: Annotated[str, Field(description="Course id, LV number, acronym or name")],
    topic: Annotated[str, Field(description="Optional focus topic")] = "",
) -> str:
    """Study plan from the course's slides and my past quiz mistakes."""
    focus = f" with focus on '{topic}'" if topic else ""
    return (
        f"Help me prepare for the exam in '{course}'{focus}.\n"
        f"1. get_course('{course}') and search(query='exam', scope='{course}') to find exam rules, scope and dates.\n"
        f"2. list_files('{course}', extensions=['pdf','pptx']) and read the most relevant slides with read_file.\n"
        "3. For quizzes I attempted, call get_quiz_review(quiz=<cmid>, only_incorrect=true) to find my weak spots.\n"
        "Then give me: exam facts (date, format, allowed aids, scope), a prioritised topic list, my weak spots, "
        "and 10 practice questions with answers."
    )


@mcp.prompt(title="Semester report")
def semester_report(
    semester: Annotated[str, Field(description="'current', 'past', 'all' or a code like 2026S")] = "current",
) -> str:
    """Grades and completion across a semester."""
    return (
        f"Make a report of my semester '{semester}': call list_courses(semester='{semester}') and "
        f"get_grades(semester='{semester}'), then get_grades(course=…) for courses with grades. "
        "Summarise per course: final/current grade, graded vs. open items, and anything still missing."
    )
