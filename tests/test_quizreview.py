"""Quiz review rendering on synthetic HTML that mirrors Moodle's markup."""

import pytest

from moodle_mcp.quizreview import is_wrong, render_question, svg_to_png

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


# ---------------------------------------------------------------- attempt in progress (mode="attempt")

def attempt_q(html: str, qtype: str, **extra) -> dict:
    return {"html": f'<div class="content"><div class="formulation clearfix">{html}</div></div>', "type": qtype,
            "questionnumber": "3", "number": 3, "maxmark": 2, "status": "Not yet answered", **extra}


def test_attempt_single_choice_keeps_italic_text():
    html = """<div class="qtext"><p>Pick one</p><p><i><font color="gray">Wähle eine</font></i></p></div>
<fieldset class="ablock"><legend class="prompt">Select one:</legend><div class="answer">
<div class="r0"><input type="radio" name="q:3_answer" value="0" checked="checked" />
 <div data-region="answer-label"><span class="answernumber">a. </span><p>Yes <i>ja</i></p></div></div>
<div class="r1"><input type="radio" name="q:3_answer" value="1" />
 <div data-region="answer-label"><span class="answernumber">b. </span><p>No</p></div></div>
</div><div class="qtype_multichoice_clearchoice"><input type="radio" name="q:3_answer" value="-1" />
<label><a href="#">Clear my choice</a></label></div></fieldset>"""
    out = render_question(attempt_q(html, "multichoice", flagged=True), mode="attempt")
    assert out.startswith("### Q3 · multichoice · 2 pt · Not yet answered · flagged")
    assert "*How to answer: choose one option.*" in out
    assert "*Wähle eine*" in out
    assert "- [x] a. Yes *ja*" in out and "- [ ] b. No" in out
    assert "Clear my choice" not in out and "Select one" not in out


def test_review_drops_icons_but_keeps_italic():
    out = render_question(q(MULTICHOICE.replace("<p>Which are prime?</p>", "<p>Which are <i>prime</i>?</p>"), "multichoice"))
    assert "Which are *prime*?" in out
    assert "fa-check" not in out and "- [x]✓ a. 2" in out


def test_attempt_dropdowns_list_options():
    match = """<div class="qtext">Match</div><table><tr><td class="text">Apriori</td><td class="control">
<select><option value="0">Choose...</option><option value="1">downward closure</option><option value="2" selected="selected">tree</option></select>
</td></tr><tr><td class="text">FP-Growth</td><td class="control">
<select><option value="0">Choose...</option><option value="1">downward closure</option><option value="2">tree</option></select>
</td></tr></table>"""
    out = render_question(attempt_q(match, "match"), mode="attempt")
    assert "[▾ tree]" in out and "[▾ ___]" in out
    assert out.count("Options for every dropdown: downward closure | tree") == 1
    assert "*How to answer: pick the matching option for every item.*" in out

    gaps = """<div class="qtext"><p>The <span class="control group1"><select><option value="">&nbsp;</option>
<option value="1">cat</option><option value="2">dog</option></select></span> chases the
<span class="control group2"><select><option value="">&nbsp;</option><option value="1">mouse</option></select></span>.</p></div>"""
    out = render_question(attempt_q(gaps, "gapselect"), mode="attempt")
    assert "[▾ ___ — options: cat | dog]" in out and "[▾ ___ — options: mouse]" in out


def test_attempt_text_and_essay():
    short = '<div class="qtext"><p>2+2?</p></div><div class="ablock"><label>Answer:</label><input type="text" value="" /></div>'
    assert "Answer: [___]" in render_question(attempt_q(short, "numerical"), mode="attempt")
    essay = """<div class="qtext"><p>Explain.</p></div><div class="ablock"><div class="answer">
<textarea name="q:3_answer">&lt;p&gt;Because &lt;b&gt;reasons&lt;/b&gt;&lt;/p&gt;</textarea></div>
<div class="attachments"><div class="filemanager">You can drag and drop files here to add them.</div></div></div>"""
    out = render_question(attempt_q(essay, "essay"), mode="attempt")
    assert "[answer text: Because reasons]" in out
    assert "[file upload field — attach files in Moodle]" in out and "drag and drop" not in out


def test_attempt_drag_words_into_text():
    html = """<div class="qtext"><p>A basis is <span class="place1 drop active group1">&#160;<span class="accesshide">Blank 1</span></span>
of vectors, written <span class="place2 drop active group1">&#160;</span>.</p></div>
<div class="draghomes"><span class="draghome choice1 group1">a set</span> <span class="draghome choice2 group1 infinite">a list</span></div>
<input type="hidden" class="placeinput place1 group1" value="2" /><input type="hidden" class="placeinput place2 group1" value="0" />"""
    out = render_question(attempt_q(html, "ddwtos"), mode="attempt")
    assert "[blank 1: a list]" in out and "[blank 2: ___]" in out
    assert "Choices to drag into the blanks: a set | a list (reusable)" in out


DD_IMAGE = """<div class="qtext"><p>Build the tree.</p></div><div class="ddarea"><div class="droparea">
<img class="dropbackground img-fluid w-100" src="https://moodle.example.edu/pluginfile.php/1/qtype_ddimageortext/bgimage/9/6/5/tree.png" alt="Background image for dragging markers onto" />
<input type="hidden" class="placeinput place1 group1" value="2" /><input type="hidden" class="placeinput place2 group1" value="" />
<div class="dropzones" data-place-info="{&quot;1&quot;:{&quot;no&quot;:&quot;1&quot;,&quot;text&quot;:&quot;root&quot;,&quot;group&quot;:&quot;1&quot;,&quot;xy&quot;:[&quot;139&quot;,&quot;17&quot;]},&quot;2&quot;:{&quot;no&quot;:&quot;2&quot;,&quot;text&quot;:&quot;&quot;,&quot;group&quot;:&quot;1&quot;,&quot;xy&quot;:[&quot;69&quot;,&quot;70&quot;]}}"></div></div>
<div class="draghomes"><div class="group1 draghome choice1">{}</div><div class="group1 draghome choice2 infinite">L:1</div>
<img class="group1 draghome choice3" src="https://moodle.example.edu/pluginfile.php/1/qtype_ddimageortext/dragimage/9/6/5/leaf.png" alt="leaf" /></div></div>"""


def test_attempt_drag_onto_image():
    out = render_question(attempt_q(DD_IMAGE, "ddimageortext"), mode="attempt")
    assert "![Background image](https://moodle.example.edu/pluginfile.php/1/qtype_ddimageortext/bgimage/9/6/5/tree.png)" in out
    assert "- zone 1 at (139, 17) «root»: L:1" in out
    assert "- zone 2 at (69, 70): ___" in out
    assert "Items to drag: {} | L:1 (reusable) | ![leaf](https://moodle.example.edu/pluginfile.php/1/qtype_ddimageortext/dragimage/9/6/5/leaf.png)" in out
    review = render_question(attempt_q(DD_IMAGE, "ddimageortext"))
    assert "- zone 2 at (69, 70): (empty)" in review and "Items to drag" not in review


def test_attempt_markers_and_ordering():
    marker = """<div class="qtext"><p>Mark Vienna.</p></div><div class="ddarea"><div class="droparea">
<img class="dropbackground" src="https://moodle.example.edu/pluginfile.php/1/qtype_ddmarker/bgimage/9/2/5/map.png" /></div>
<div class="draghomes"><span class="marker choice0"><img class="target" src="https://moodle.example.edu/theme/image.php/x" alt="" /><span class="markertext">Vienna</span></span>
<span class="marker choice1 infinite"><span class="markertext">Linz</span></span></div>
<input type="hidden" class="choices choice0 noofdrags1" value="120,45" /><input type="hidden" class="choices choice1" value="" /></div>"""
    out = render_question(attempt_q(marker, "ddmarker"), mode="attempt")
    assert "- Vienna: (120,45)" in out and "- Linz: not placed" in out
    ordering = """<div class="qtext"><p>Sort by size.</p></div><div class="ablock"><div class="answer ordering">
<ul class="sortablelist"><li class="sortableitem">Byte</li><li class="sortableitem">Bit</li></ul></div></div>"""
    out = render_question(attempt_q(ordering, "ordering"), mode="attempt")
    assert "Current order (top to bottom):" in out and "1. Byte\n2. Bit" in out


CODE_SVG = """<svg style="width: 100%; height: 50px;" xmlns="http://www.w3.org/2000/svg"><g font-family="monospace">
<text x="0" y="14" xml:space="preserve"><tspan fill="#007020">int</tspan>&#160;x&#160;=&#160;1;</text>
<text x="0" y="33" xml:space="preserve">&#160;&#160;return&#160;x;</text></g></svg>"""
DIAGRAM_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="200px" height="80px" viewBox="0 0 200 80"><rect x="0" y="0" width="80" height="40" fill="#fff" stroke="#000"/>
<switch><foreignObject width="100%" height="100%"><div xmlns="http://www.w3.org/1999/xhtml"><b>Car</b><br></div></foreignObject><text x="5" y="20">Car</text></switch>
<path d="M 80 20 L 120 20" stroke="#000"/><text x="120" y="20">Wheel</text>
<a xlink:href="https://desk.draw.io/x"><text x="50" y="75">Viewer does not support full SVG 1.1</text></a></svg>"""


def test_inline_svg_code_and_diagram():
    html = f'<div class="qtext"><p>What does it return?</p>{CODE_SVG}<p>Model:</p><span>{DIAGRAM_SVG}</span></div>'
    sink: list[str] = []
    out = render_question(attempt_q(html, "shortanswer"), mode="attempt", pictures=sink)
    assert "```\nint x = 1;\n  return x;\n```" in out
    assert "![diagram](picture:0)" in out
    assert len(sink) == 1 and 'viewBox="0 0 200 80"' in sink[0]  # raw SVG, case preserved
    review = render_question(attempt_q(html, "shortanswer"))
    assert "[diagram — labels: Car, Wheel]" in review and "Viewer does not support" not in review


def test_svg_to_png():
    pytest.importorskip("resvg_py")
    png = svg_to_png(DIAGRAM_SVG.replace(' xmlns="http://www.w3.org/2000/svg"', ""))
    assert png.startswith(b"\x89PNG")
    with pytest.raises(RuntimeError):
        svg_to_png("<svg><broken")


def test_pictures_from_other_hosts_and_data_uris():
    html = """<div class="qtext"><p>Does it violate LSP?</p>
<img src="https://images.example.org/class.png" width="600" />
<img src="data:image/png;base64,iVBORw0KGgo=" alt="Version [2]" />
<img src="https://moodle.example.edu/theme/image.php/boost/core/1/i/flagged" alt="" />
<img src="https://moodle.example.edu/pluginfile.php/1/question/questiontext/9/1/5/uml.png" alt="uml" /></div>"""
    pics: list[str] = []
    out = render_question(attempt_q(html, "multichoice"), mode="attempt", pictures=pics)
    assert pics == ["https://images.example.org/class.png", "data:image/png;base64,iVBORw0KGgo="]
    assert "![picture](picture:0)" in out and "![Version 2](picture:1)" in out
    assert "![uml](https://moodle.example.edu/pluginfile.php/1/question/questiontext/9/1/5/uml.png)" in out
    assert "theme/image.php" not in out
    review = render_question(attempt_q(html, "multichoice"))
    assert "[image]" in review and "[image: Version 2]" in review
