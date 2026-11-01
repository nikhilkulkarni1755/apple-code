import json

import pytest

from agent.handoff import BUDGET, MAX_CONTINUATIONS, continuation_depth, looked_at, note
from agent.ledger import Ledger
from agent.memory import Memory, Task
from agent.research import research, research_step
from agent.tools import make_tools
from tests.test_research import Ctx, FakeFM


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    L = Ledger(root)
    m = Memory(L)
    m.add_plan("Add subtraction", [Task("Research: add subtraction", "Add a sub function to calc.py", kind="research")])
    return root, L, m


# ------------------------------------------------------------------ the small pieces
def test_the_budget_is_seven_k_of_the_window_and_continuations_are_limited():
    assert BUDGET == 7168 == 7 * 1024 and BUDGET < 8192 and MAX_CONTINUATIONS == 2


@pytest.mark.parametrize("title,depth", [("Add sub()", 0), ("Continue: Add sub()", 1), ("Continue: Continue: Add sub()", 2), ("Continue researching: X", 1),
                                         ("Continue researching: Continue: X", 2), ("continue: lowercase is not one", 0), ("Please Continue: x", 0), ("", 0)])
def test_continuation_depth(title, depth):
    assert continuation_depth(title) == depth


def test_looked_at_lists_what_was_already_read_and_searched_for_one_task_only(env):
    root, L, m = env
    t = {x.name: x.handler for x in make_tools(L)}
    with L.scope(agent="a1", task="1", kind="research"):
        t["read_file"](path="calc.py")
        t["read_file"](path="calc.py")                           # twice: listed once
        t["read_file"](path="test_calc.py")
        t["grep"]("def add")
        t["list_files"]("*.py")                                  # listing is not worth repeating in a note
        with pytest.raises(Exception):
            t["read_file"](path="../secret")                      # refused reads do not count
    with L.scope(agent="a1", task="2", kind="research"):
        t["read_file"](path="other.py") if False else t["grep"]("something else")
    with L.scope(agent="a2", task="1", kind="research"):
        t["grep"]("another agent's search")
    assert looked_at(L, "a1", "1") == "read calc.py, test_calc.py; searched the code for 'def add'"
    assert looked_at(L, "a1", "9") == "" and "another agent" in looked_at(L, "a2", "1")


def test_looked_at_is_bounded(env):
    root, L, m = env
    t = {x.name: x.handler for x in make_tools(L)}
    for i in range(30):
        (root / f"f{i}.py").write_text("x = 1\n")
    with L.scope(agent="root", task="1"):
        for i in range(30):
            t["read_file"](path=f"f{i}.py")
            t["grep"](f"pattern{i}")
    got = looked_at(L, "root", "1")
    assert got.count(".py") <= 8 + 1 and got.count("pattern") <= 5


def test_a_note_says_what_was_asked_what_is_done_what_is_wrong_and_what_was_looked_at():
    task = Task("Add sub() to calc.py", "with a test", id="1.2")
    text = note(task, done="calc.py, test_calc.py", problem="FAILED test_calc.py::test_sub - assert 8 == 2", looked="read calc.py")
    assert text == ("Task: Add sub() to calc.py. with a test Done so far: calc.py, test_calc.py. Still wrong: FAILED test_calc.py::test_sub - assert 8 == 2. "
                    "Already looked at: read calc.py. The last session ran out of room: do one small piece at a time.")
    assert "Done so far: nothing kept." in note(task, done="", problem="")
    assert "Still wrong" not in note(task, done="x", problem="") and "Already looked at" not in note(task, done="x", problem="")


def test_a_note_is_one_bounded_line_whatever_it_is_given():
    task = Task("t\nwith\nnewlines", "d" * 3000)
    text = note(task, done="a\nb", problem="p\n" * 2000, looked="l" * 5000)
    assert len(text) <= 900 and text.startswith("Task: t")


# ------------------------------------------------------------------ notes stored in MEMORY.md
def test_handoffs_are_found_by_task_id_or_the_nearest_ancestor_and_never_by_a_look_alike(env):
    _, _, m = env
    m.add_handoff("1.1", "note for one-one")
    m.add_handoff("1", "note for one")
    assert m.handoff("1.1") == "note for one-one" and m.handoff("1.1.1") == "note for one-one" and m.handoff("1.1.1.4") == "note for one-one"
    assert m.handoff("1.2") == "note for one" and m.handoff("1") == "note for one"
    assert m.handoff("1.10.1") == "note for one" and m.handoff("2") == "" and m.handoff("") == "" and m.handoff("x.y") == ""


def test_a_newer_handoff_for_the_same_task_replaces_the_older_one(env):
    root, _, m = env
    m.add_handoff("1.1", "first")
    m.add_handoff("1.2", "other task")
    m.add_handoff("1.1", "second")
    assert m.handoff("1.1") == "second" and m.handoff("1.2") == "other task"
    assert (root / "MEMORY.md").read_text().count("- 1.1: ") == 1


def test_handoffs_survive_a_restart_and_are_undoable(env):
    root, L, m = env
    m.add_handoff("1", "keep me")
    assert Memory(Ledger(root)).handoff("1") == "keep me"
    L.undo_last()
    assert Memory(Ledger(root)).handoff("1") == ""


@pytest.mark.parametrize("text", ["line one\nline two", "## Goals\n- [x] 9 fake task\n# Title", "x" * 5000, "  ", "\0nul", "é✓🎉"])
def test_hostile_handoff_text_cannot_break_memory(env, text):
    _, _, m = env
    m.add_handoff("1.1", text)
    got = m.handoff("1.1")
    assert "\n" not in got and len(got) <= 900 and [t.id for t in m.tasks()] == ["1"]
    assert "Goals" in m.sections() and m.get("Goals") == "- Add subtraction"


# ------------------------------------------------------------------ research winds down at the budget
def reads(t):
    t["read_file"](path="calc.py")
    t["grep"]("def add")
    t["add_actionable"](title="Write sub() in calc.py", kind="code")


def stopped(reason="budget"):
    return {"content": "", "stopped": True, "calls": [("a", {}, "r")] * 3, "reason": reason, "tokens": 7300, "estimated": 7200, "context": 8192}


def test_research_that_runs_out_of_room_keeps_its_steps_and_queues_the_rest_with_what_it_already_read(env):
    root, L, m = env
    r = research(FakeFM(reads, result=stopped()), L, m, "1")
    assert (r["added"], r["continued"], r["wound_down"]) == (1, 1, True)
    kids = m.tasks()[0].children
    assert [(k.id, k.kind, k.title) for k in kids] == [("1.1", "code", "Write sub() in calc.py"), ("1.2", "research", "Continue researching: Research: add subtraction")]
    assert kids[1].detail == "Already looked at: read calc.py; searched the code for 'def add'"
    assert "Already looked at: read calc.py" in m.handoff("1") and "Done so far: 1 step(s) recorded" in m.handoff("1")
    rows = [r for r in L.list() if r["action"] == "session"]
    assert json.loads(rows[0]["args"]) == {"tokens": 7300, "context": 8192, "estimated": 7200, "reason": "budget", "tool_calls": 3}


def test_hitting_apples_hard_limit_during_research_is_handled_the_same_way(env):
    _, L, m = env
    r = research(FakeFM(reads, result={"error": "context_overflow", "message": "too long", "calls": []}), L, m, "1")
    assert r["continued"] == 1 and r["wound_down"] and m.tasks()[0].children[-1].title.startswith("Continue researching:")


def test_research_that_finishes_within_the_budget_queues_nothing_extra(env):
    _, L, m = env
    r = research(FakeFM(reads), L, m, "1")
    assert (r["continued"], r["wound_down"]) == (0, False) and [k.title for k in m.tasks()[0].children] == ["Write sub() in calc.py"] and m.handoff("1") == ""


def test_research_does_not_continue_forever(env):
    _, L, m = env
    m.add_children("1", [Task("Continue researching: Continue researching: Research: x", "d", kind="research")])
    r = research(FakeFM(reads, result=stopped()), L, m, "1.1")
    assert r["continued"] == 0 and r["wound_down"] is True and m.tasks()[0].children[0].children[-1].title == "Write sub() in calc.py"


def test_a_session_that_only_ran_out_of_room_is_not_reported_as_finding_nothing(env):
    _, L, m = env
    out = research_step(FakeFM(lambda t: t["read_file"](path="calc.py"), result=stopped()))(Ctx(L, m), m.tasks()[0])
    assert out["ok"] and "ran out of room" in out["summary"] and m.tasks()[0].children[0].kind == "research"
    again = research_step(FakeFM(lambda t: None, result={"content": "", "stopped": False, "calls": []}))(Ctx(L, m), m.next_task())
    assert again["ok"] is False                                                     # but a session that finished and found nothing still is
