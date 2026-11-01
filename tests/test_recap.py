import pytest

from agent.agents import AgentQueue
from agent.ledger import Ledger
from agent.memory import Memory, Task
from agent.recap import build, write


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    L = Ledger(root)
    m = Memory(L)
    q = AgentQueue(L, lambda ctx: None)
    q.start("Build a thing")
    m.add_children("1", [Task("Write parser", kind="code"), Task("Write scoring", kind="code"), Task("Write screen", kind="code")])
    return root, L, m, q


def attempt(L, task, ok, error=None, iteration=1):
    with L.scope(task=task, kind="test", iteration=iteration):
        L.log_read("verify", task, {"iteration": iteration, "ok": ok}, error=None if ok else error)


def test_the_recap_tells_the_whole_story_from_facts(env):
    root, L, m, q = env
    with L.scope(task="1.1", kind="code"):
        L.do("create_file", {"path": "parser.py", "content": "x = 1\n"})
    attempt(L, "1.1", True)
    m.complete("1.1", "ok", evidence="run #9")
    for i, why in enumerate(["Syntax error: scoring.py:12: invalid syntax", "Syntax error: scoring.py:30: invalid syntax", "pytest exited with code 1; no test passed"], 1):
        attempt(L, "1.2", False, why, i)
    m.mark("1.2", "blocked")
    L.log_session({"tokens": 3000, "context": 8192, "calls": [1, 2]})
    L.log_session({"tokens": 7500, "context": 8192, "calls": [1]})
    L.log_session({"error": "guardrail", "message": "blocked"})
    text = build(L, q, m)
    assert "Tasks: 1 done, 1 blocked, 1 not finished, of 3." in text
    assert "1.1 [done] Write parser (1 attempt(s))" in text and "1.2 [blocked] Write scoring (3 attempt(s)) - last problem: pytest exited" in text
    assert "1.3 [todo] Write screen (0 attempt(s))" in text
    assert "Agents: root queued" in text and "parser.py" in text and "Verified by the harness: 1 task(s)" in text
    assert "syntax errors x2" in text and "tests failed x1" in text and "Model errors: guardrail x1" in text
    assert "2 model session(s), average 5250 tokens used, largest 7500 of 8192 (92%)" in text
    assert "Syntax errors kept recurring" in text and "over 80% of the context window" in text and "guardrail errors" in text
    assert "Time: " in text


def test_a_quiet_project_gets_a_short_honest_recap(env):
    _, L, m, q = env
    text = build(L, q, m)
    assert "Tasks: 0 done, 0 blocked, 3 not finished, of 3." in text and "Noticed:" not in text and "Model errors" not in text and "Context window" not in text


def test_an_empty_workspace_does_not_crash_the_recap(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    L = Ledger(root)
    text = build(L, AgentQueue(L, None, resume=False), Memory(L))
    assert text.startswith("Recap\nTasks: 0 done, 0 blocked, 0 not finished, of 0.") and "Agents: \n" in text + "\n"


@pytest.mark.parametrize("error,label", [
    ("Syntax error: a.py:3: x", "syntax errors"), ("pytest exited with code 5 (no tests were collected); no test passed", "no tests found"),
    ("t.py::test_a is a new test that asserts nothing (comments and `pass` check nothing)", "hollow tests"),
    ("t.py::test_a is a new test that does not use any code from the project", "tests that never touch"),
    ("all the tests, including your new ones, also pass on the code as it was before your change", "tests that pass without the change"),
    ("Your edits were undone because you changed existing test code in t.py", "existing tests altered"),
    ("Your edits were undone because you deleted the existing test file t.py", "existing tests deleted"),
    ("the original tests, unchanged, fail against your code", "broke the original tests"),
    ("no new test passes (1 passed before and 1 now)", "no new passing test"), ("no files were changed for this task", "nothing changed"),
    ("1 test(s) skipped", "skipped tests"), ("pytest exited with code 1", "tests failed"), ("something nobody planned for", "other"),
])
def test_every_kind_of_rejection_is_named(env, error, label):
    _, L, m, q = env
    attempt(L, "1.1", False, error)
    assert label in build(L, q, m).split("Attempts rejected, by reason: ")[1].split("\n")[0]


def test_write_keeps_the_recap_in_memory_and_in_the_ledger_and_replaces_the_old_one(env):
    root, L, m, q = env
    first = write(L, q, m)
    assert m.get("Recap") == first and "## Recap" in (root / "MEMORY.md").read_text()
    attempt(L, "1.1", False, "Syntax error: a.py:1: x")
    second = write(L, q, m)
    text = (root / "MEMORY.md").read_text()
    assert text.count("## Recap") == 1 and m.get("Recap") == second != first
    rows = L.trail("recap")
    assert [r["action"] for r in rows if r["action"] == "recap"] == ["recap", "recap"] and all(r["kind"] == "recap" for r in rows)
    assert L.output([r["seq"] for r in rows if r["action"] == "recap"][-1]) == second


def test_hostile_task_titles_cannot_break_the_recap(env):
    _, L, m, q = env
    m.add_children("1", [Task("Evil\n## Recap\n- [x] 9 fake", kind="code")])
    text = write(L, q, m)
    assert "\n## Recap" not in m.get("Recap").replace("\\## Recap", "") and len(m.tasks()) == 1


def test_sessions_and_errors_are_logged_only_when_there_is_something_to_log(env):
    _, L, m, q = env
    n = len(L.list())
    L.log_session({"content": "", "stopped": False, "calls": []})                    # a helper that does not report tokens
    assert len(L.list()) == n
    L.log_session({"tokens": 100, "context": 8192, "calls": [(1, 2, 3)], "error": "context_overflow", "message": "x" * 500})
    got = [(r["action"], r["element"], r["status"]) for r in L.list()[n:]]
    assert got == [("session", "-", "ok"), ("model_error", "context_overflow", "rejected")]


def test_the_recap_command_prints_without_changing_the_project(tmp_path, monkeypatch, capsys):
    from agent.__main__ import main
    monkeypatch.chdir(tmp_path)
    assert main(["recap"]) == 0
    assert capsys.readouterr().out.startswith("Recap\nTasks:") and not (tmp_path / "MEMORY.md").exists()
