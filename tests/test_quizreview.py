"""Quiz review rendering on synthetic HTML that mirrors Moodle's markup."""

from moodle_mcp.quizreview import is_wrong, render_question

MULTICHOICE = """
<div class="que multichoice"><div class="info"><h3 class="no">Question 1</h3></div><div class="content">
<div class="formulation clearfix"><h4 class="accesshide">Question text</h4>
<input type="hidden" name="q1:1_:sequencecheck" value="2" />
<div class="qtext"><p>Which are prime?</p></div>
<fieldset class="ablock"><legend class="prompt">Select one or more:</legend><div class="answer">
<div class="r0 correct"><input type="checkbox" name="q1:1_choice0" checked="checked" disabled="disabled" />
 <div class="d-flex" data-region="answer-label"><span class="answernumber">a. </span><div class="flex-fill"><p>2</p></div></div>
 <i class="icon fa-check" title="Correct"></i></div>
<div class="r1 incorrect"><input type="checkbox" name="q1:1_choice1" checked="checked" disabled="disabled" />
 <div class="d-flex" data-region="answer-label"><span class="answernumber">b. </span><div class="flex-fill"><p>4</p></div></div>
 <i class="icon fa-xmark" title="Incorrect"></i></div>
<div class="r0"><input type="checkbox" name="q1:1_choice2" disabled="disabled" />
 <div class="d-flex" data-region="answer-label"><span class="answernumber">c. </span><div class="flex-fill"><p>7</p></div></div></div>
</div></fieldset></div>
<div class="outcome clearfix"><h4 class="accesshide">Feedback</h4><div class="feedback">
<div class="specificfeedback">Your answer is partially correct.</div>
<div class="rightanswer">The correct answers are: 2, 7</div></div></div></div></div>
"""

SELECT_CLOZE = """
<div class="content"><div class="formulation clearfix"><div class="qtext"><table><tr><td>Uses downward closure</td><td>
<span class="subquestion"><label class="subq accesshide">Answer 1</label>
<select class="select form-select incorrect" disabled="disabled"><option value="">&nbsp;</option>
<option value="0">FP Growth</option><option selected="selected" value="1">Apriori</option><option value="2">Both</option></select>
<a class="feedbacktrigger" data-bs-content="&lt;span&gt;Incorrect&lt;br /&gt;The correct answer is: Both&lt;br /&gt;Mark 0.00 out of 1.00&lt;/span&gt;" href="#"><i title="Incorrect"></i></a>
</span></td></tr></table></div></div></div>
"""

TEXT_INPUT = """
<div class="content"><div class="formulation clearfix"><div class="qtext"><p>2+2?</p></div>
<div class="ablock"><label>Answer:</label><span class="answer"><input type="text" value="5" class="form-control incorrect" readonly="readonly"/>
<i class="icon" title="Incorrect"></i></span></div></div>
<div class="outcome"><div class="feedback"><div class="generalfeedback">Basic arithmetic.</div><div class="rightanswer">The correct answer is: 4</div></div></div></div>
"""

DRAGDROP = """
<div class="content"><div class="formulation clearfix"><div class="qtext"><p>A basis is <span class="place1 drop active group1">&#160;<span class="accesshide">Blank 1</span></span>
<i class="icon" title="Correct"></i> of vectors.</p></div>
<div class="answercontainer"><div class="draggrouphomes1"><span class="draghome choice1 group1">a set</span> <span class="draghome choice2 group1">a list</span></div></div>
<input type="hidden" class="placeinput place1 group1" name="q:p1" value="1" /></div></div>
"""


def q(html: str, qtype: str, mark: str = "1.00", maxmark: float = 2) -> dict:
    return {"html": html, "type": qtype, "questionnumber": "1", "mark": mark, "maxmark": maxmark, "status": "Partially correct",
            "state": "gradedpartial"}


def test_multichoice():
    out = render_question(q(MULTICHOICE, "multichoice"))
    assert "### Q1 · multichoice · 1.00/2 · Partially correct" in out
    assert "- [x]✓ a. 2" in out
    assert "- [x]✗ b. 4" in out
    assert "- [ ] c. 7" in out
    assert "The correct answers are: 2, 7" in out
    assert "sequencecheck" not in out


def test_select_with_popover_answer():
    out = render_question(q(SELECT_CLOZE, "multianswer"))
    assert "[Apriori ✗ — correct: Both]" in out


def test_text_input():
    out = render_question(q(TEXT_INPUT, "numerical"))
    assert "[5 ✗]" in out
    assert "**Explanation:** Basic arithmetic." in out
    assert "The correct answer is: 4" in out


def test_drag_and_drop():
    out = render_question(q(DRAGDROP, "ddwtos"))
    assert "[a set ✓]" in out
    assert "a list" not in out


def test_without_feedback_and_is_wrong():
    out = render_question(q(TEXT_INPUT, "numerical"), include_feedback=False)
    assert "correct answer" not in out
    assert is_wrong({"state": "gradedwrong"})
    assert not is_wrong({"state": "gradedright", "mark": "2", "maxmark": 2})
