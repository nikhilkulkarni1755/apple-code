import time

import pytest

from agent.ledger import ActionError, Ledger
from agent.runner import pytest_summary, run_tests
from tests.test_ledger import snapshot


@pytest.fixture
def ws(tmp_path):
    return tmp_path, Ledger(tmp_path)


def test_records_command_exit_code_and_full_output(ws):
    _, L = ws
    ok, bad = L.run_command("echo hi; echo err >&2"), L.run_command("exit 3")
    assert (L.get(ok)["exit_code"], L.output(ok)) == (0, "hi\nerr\n")
    assert (L.get(bad)["status"], L.get(bad)["exit_code"]) == ("ok", 3)


def test_timeout_kills_the_whole_process_group(ws):
    _, L = ws
    start = time.time()
    seq = L.run_command("sh -c 'sleep 30 & wait'", timeout=1)
    r = L.get(seq)
    assert time.time() - start < 10
    assert (r["exit_code"], r["error"]) == (None, "timed out after 1s")


@pytest.mark.parametrize("setup,cmd", [
    ({}, "printf 'x\\n' > new.txt"),                                  # created
    ({"f.txt": "a\nb\nc\n"}, "printf 'a\\nB\\nc\\n' > f.txt"),         # changed in the middle
    ({"f.txt": "a\nb\n"}, "echo c >> f.txt"),                          # appended
    ({"f.txt": "a\nb\n"}, "printf '' > f.txt"),                        # truncated to empty
    ({"f.txt": ""}, "echo a > f.txt"),                                 # filled from empty
    ({"f.txt": "a\nb"}, "printf 'a\\nb\\nc' > f.txt"),                 # no trailing newline
    ({"f.txt": "a\n", "g.txt": "b\n"}, "rm f.txt; mkdir d; echo z > d/g.txt; mv g.txt h.txt"),
])
def test_file_changes_made_by_a_command_are_recorded_and_undoable(ws, setup, cmd):
    root, L = ws
    for name, text in setup.items():
        (root / name).write_text(text)
    before = snapshot(root)
    seq = L.run_command(cmd)
    assert snapshot(root) != before
    changes = [r for r in L.list() if r["cause"] == seq]
    assert changes and all(r["status"] == "ok" for r in changes)
    assert L.verify() == []
    L.rewind(seq - 1)
    assert snapshot(root) == before
    assert L.verify() == []


def test_line_diff_of_a_command_edit_is_exact(ws):
    root, L = ws
    (root / "f.txt").write_text("a\nb\nc\n")
    seq = L.run_command("printf 'a\\nB\\nc\\n' > f.txt")
    r = [r for r in L.list() if r["cause"] == seq][0]
    assert (r["action"], r["start_line"], r["removed"], r["added"]) == ("replace_lines", 2, "b\n", "B\n")


def test_cache_and_tool_directories_are_ignored(ws):
    root, L = ws
    seq = L.run_command("mkdir -p __pycache__ .venv .pytest_cache; echo x > __pycache__/a; echo x > .venv/b; echo x > .pytest_cache/c")
    assert [r for r in L.list() if r["cause"] == seq and r["path"]] == []  # no file rows (the new .venv is an env row)


def test_binary_files_are_ignored_not_fatal(ws):
    _, L = ws
    seq = L.run_command("head -c 64 /dev/urandom > blob.bin; printf '\\377\\376' > bad.txt")
    assert L.get(seq)["status"] == "ok" and [r for r in L.list() if r["cause"] == seq] == []


def test_command_that_never_finished_is_recovered_as_error(ws):
    root, L = ws

    class Boom:
        def run(self, *a):
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        L.run_command("echo hi", executor=Boom())
    assert L.list()[-1]["status"] == "pending"
    assert "crashed" in Ledger(root).list()[-1]["error"]


def test_replay_links_to_the_original(ws):
    _, L = ws
    a = L.run_command("echo hi")
    b = L.replay(a)
    assert L.get(b)["replay_of"] == a and L.output(b) == "hi\n"
    with pytest.raises(ActionError, match="not a command"):
        L.replay(L.do("create_file", {"path": "x.txt", "content": "x\n"}))


def test_commands_cannot_be_undone_but_do_not_block_undo_last(ws):
    root, L = ws
    edit = L.do("create_file", {"path": "x.txt", "content": "x\n"})
    cmd = L.run_command("echo hi")
    with pytest.raises(ActionError, match="cannot be undone"):
        L.undo(cmd)
    assert L.undo_last() > cmd and not (root / "x.txt").exists() and L.get(edit)["reverted_by"]


def test_pytest_summary_parsing():
    assert pytest_summary("..\n2 passed in 0.01s\n") == {"passed": 2}
    assert pytest_summary("= 1 failed, 2 passed, 1 skipped, 1 error in 0.50s =") == {"failed": 1, "passed": 2, "skipped": 1, "errors": 1}
    assert pytest_summary("no tests ran in 0.00s") == {}
    assert pytest_summary("garbage") == {}


def test_add_test_run_undo_rerun_workflow(ws):
    root, L = ws
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    first = run_tests(L)
    L.do("insert_lines", {"path": "test_calc.py", "at_line": 6, "text": "\n\ndef test_new():\n    assert add(2, 2) == 5\n"})
    second = run_tests(L)
    L.undo_last()
    third = run_tests(L)
    assert [L.get(s)["exit_code"] for s in (first, second, third)] == [0, 1, 0]
    assert pytest_summary(L.output(second)) == {"failed": 1, "passed": 1}
    assert "pytest: {'passed': 1} -> {'failed': 1, 'passed': 1}" in L.diff(first, second)
    assert "test_new" in L.diff(first, second)
    assert L.diff(first, third).splitlines()[:2] == ["exit code: 0 -> 0", "pytest: {'passed': 1} -> {'passed': 1}"]
    assert L.verify() == []
