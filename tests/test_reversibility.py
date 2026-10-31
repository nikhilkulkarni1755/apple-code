"""Every state-changing tool call must be reversible: undo restores the exact bytes, redo restores the change."""
import random
import shutil

import pytest

from agent.envstate import env_state
from agent.ledger import Ledger
from agent.memory import Memory, Task
from agent.tools import make_tools
from tests.test_ledger import snapshot

CALC = "def add(a, b):\n    return a + b\n"
TEST_CALC = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n"


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    (root / "test_calc.py").write_text(TEST_CALC)
    (root / "notes.txt").write_text("one\ntwo\nthree")          # no trailing newline
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("X = 1\n")
    L = Ledger(root)
    return root, L, {t.name: t.handler for t in make_tools(L)}


# ---- the scenario from the spec: edit a test case, run, reverse, run again ----
def test_editing_a_test_case_then_reversing_restores_the_file_and_the_result(proj):
    root, L, t = proj
    original = (root / "test_calc.py").read_bytes()
    assert t["run_tests"]().startswith("exit 0")
    t["replace_text"](path="test_calc.py", old="add(1, 2) == 3", new="add(1, 2) == 4")        # edit an existing test case
    assert t["run_tests"]().startswith("exit 1")
    L.undo_last()                                    # test runs are not undoable effects, so this reverses the edit
    assert (root / "test_calc.py").read_bytes() == original
    assert t["run_tests"]().startswith("exit 0")
    L.redo()
    assert b"== 4" in (root / "test_calc.py").read_bytes() and t["run_tests"]().startswith("exit 1")
    assert L.verify() == []


def test_adding_a_test_case_then_reversing_restores_the_file_and_the_result(proj):
    root, L, t = proj
    original = (root / "test_calc.py").read_bytes()
    t["insert_after"](path="test_calc.py", anchor="assert add(0, 0) == 0", text="\n\ndef test_new():\n    assert add(2, 2) == 5")
    assert t["run_tests"]().startswith("exit 1") and "test_new" in L.output(L.list()[-1]["seq"])
    L.undo_last()
    assert (root / "test_calc.py").read_bytes() == original and t["run_tests"]().startswith("exit 0")


def test_deleting_a_test_case_then_reversing(proj):
    root, L, t = proj
    original = (root / "test_calc.py").read_bytes()
    t["replace_text"](path="test_calc.py", old="def test_zero():\n    assert add(0, 0) == 0\n", new="")
    assert "test_zero" not in (root / "test_calc.py").read_text()
    L.undo_last()
    assert (root / "test_calc.py").read_bytes() == original


def test_breaking_source_then_reversing_fixes_the_tests(proj):
    root, L, t = proj
    t["replace_text"](path="calc.py", old="a + b", new="a - b")
    assert t["run_tests"]().startswith("exit 1")
    L.undo_last()
    assert t["run_tests"]().startswith("exit 0") and (root / "calc.py").read_text() == CALC


# ---- each tool, one by one ----
OPS = {
    "create_file": lambda t: t["create_file"](path="deep/new/file.txt", content="hello\n"),
    "create_empty_file": lambda t: t["create_file"](path="empty.txt", content=""),
    "delete_file": lambda t: t["delete_file"](path="calc.py"),
    "delete_file_in_dir": lambda t: t["delete_file"](path="pkg/mod.py"),
    "replace_text": lambda t: t["replace_text"](path="calc.py", old="a + b", new="b + a"),
    "replace_text_multiline": lambda t: t["replace_text"](path="test_calc.py", old="def test_add():\n    assert add(1, 2) == 3\n", new="def test_add():\n    pass\n"),
    "replace_text_delete_line": lambda t: t["replace_text"](path="notes.txt", old="two", new=""),
    "replace_last_line_without_newline": lambda t: t["replace_text"](path="notes.txt", old="three", new="3\n4"),
    "insert_after": lambda t: t["insert_after"](path="calc.py", anchor="def add(a, b):", text="    # note"),
    "insert_after_last_line_without_newline": lambda t: t["insert_after"](path="notes.txt", anchor="three", text="four"),
    "run_command_creates_files": lambda t: t["run_command"]("mkdir -p out/sub && echo a > out/a.txt && echo b > out/sub/b.txt"),
    "run_command_edits_and_deletes": lambda t: t["run_command"]("echo extra >> calc.py && rm notes.txt && : > pkg/mod.py"),
    "run_command_renames": lambda t: t["run_command"]("mv calc.py renamed.py"),
    "run_command_truncates": lambda t: t["run_command"](": > calc.py"),
    "build_step_writes_output": lambda t: t["run_command"]("mkdir dist && cp calc.py dist/calc.min.py"),
}


@pytest.mark.parametrize("op", OPS)
def test_every_state_changing_tool_is_reversible(proj, op):
    root, L, t = proj
    before = snapshot(root)
    OPS[op](t)
    after = snapshot(root)
    assert after != before
    while True:                                       # one command can leave several records; undo until nothing is left to undo
        try:
            L.undo_last()
        except Exception:
            break
    assert snapshot(root) == before, op
    assert L.verify() == []
    while True:
        try:
            L.redo()
        except Exception:
            break
    assert snapshot(root) == after, op
    assert L.verify() == []


def test_failing_and_rejected_calls_leave_nothing_to_undo(proj):
    root, L, t = proj
    before = snapshot(root)
    for call in (lambda: t["replace_text"](path="calc.py", old="nope", new="x"), lambda: t["create_file"](path="calc.py", content="x"),
                 lambda: t["delete_file"](path="ghost.py"), lambda: t["run_command"]("sudo ls"), lambda: t["run_command"]("exit 3"),
                 lambda: t["run_tests"]("-k nomatch")):
        try:
            call()
        except Exception:
            pass
    assert snapshot(root) == before
    with pytest.raises(Exception, match="nothing to undo"):
        L.undo_last()


def test_memory_and_plan_changes_are_reversible_together_with_code(proj):
    root, L, t = proj
    m = Memory(L)
    before = snapshot(root)
    m.add_plan("Add subtraction", [Task("Write sub", done_when="pytest passes")])
    t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b")
    m.complete("1", "added sub", evidence="run #1")
    L.rewind(0)
    assert snapshot(root) == before and L.verify() == []


def test_undo_redo_cycles_stay_consistent(proj):
    root, L, t = proj
    t["replace_text"](path="calc.py", old="a + b", new="a * b")
    t["create_file"](path="x.txt", content="x\n")
    done = snapshot(root)
    for _ in range(10):
        L.undo_last(), L.undo_last()
        L.redo(), L.redo()
        assert snapshot(root) == done and L.verify() == []
    L.rewind(0), L.rewind(0)                         # second rewind is a no-op
    assert (root / "calc.py").read_text() == CALC and not (root / "x.txt").exists()


# ---- fuzz: random tool calls, many of them hostile ----
HOSTILE = [None, 5, "", "..", "/etc/passwd", "x\0y", ".agent/ledger.db", ".AGENT/x", "a" * 300, ["x"], {"p": 1}, True, -1, 10 ** 30, "\ud800"]
PATHS = ["calc.py", "test_calc.py", "notes.txt", "pkg/mod.py", "new.txt", "d/e/f.txt", "empty.txt", "pkg", "missing.py"]
SNIPS = ["a + b", "return", "\n", "X = 1", "one", "def add", "assert", "zzz", "three", "e"]
SAFE_COMMANDS = ["echo hi > made.txt", "touch t1 t2", "mkdir -p q/r && echo z > q/r/z.txt", "rm -f notes.txt", "echo more >> calc.py",
                 "cp calc.py copy.py", "mv pkg/mod.py pkg/moved.py 2>/dev/null; true", "printf '' > empty2.txt", "exit 1", "python3 -c \"print('x'*3000)\""]


def random_call(rng, t):
    pick = lambda good: rng.choice(good) if rng.random() < 0.75 else rng.choice(HOSTILE)
    text = rng.choice(["x\n", "line\n", "a\nb\n", "no newline", "", "é✓\n"])
    name = rng.choice(["create_file", "delete_file", "replace_text", "insert_after", "run_command", "run_tests", "read_file", "grep", "list_files"])
    if name == "create_file":
        return t[name](path=pick(PATHS), content=pick([text]))
    if name == "delete_file":
        return t[name](path=pick(PATHS))
    if name == "replace_text":
        return t[name](path=pick(PATHS), old=pick(SNIPS), new=pick([text, ""]))
    if name == "insert_after":
        return t[name](path=pick(PATHS), anchor=pick(SNIPS), text=pick([text]))
    if name == "run_command":
        return t[name](cmd=pick(SAFE_COMMANDS) if rng.random() < 0.9 else rng.choice(["sudo ls", "rm -rf /", "cat .agent/x", None, ""]))
    if name == "run_tests":
        return t[name](args=pick(["", "-k nomatch", "test_calc.py"]))
    if name == "read_file":
        return t[name](path=pick(PATHS), start=pick([1, 2]), end=pick([None, 3]))
    return t[name]("e") if name == "grep" else t[name]("*")


@pytest.mark.parametrize("seed", range(30))
def test_fuzzed_tool_calls_never_escape_and_always_rewind_cleanly(proj, seed):
    root, L, t = proj
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n")
    (root / "link").symlink_to(outside / "secret.txt")
    start = snapshot(root)
    rng = random.Random(seed)
    for _ in range(40):
        try:
            random_call(rng, t)
        except Exception:
            pass                                     # rejections are fine; corruption is not
        if rng.random() < 0.1:
            try:
                L.undo_last()
            except Exception:
                pass
        if rng.random() < 0.05:
            try:
                L.redo()
            except Exception:
                pass
    assert (outside / "secret.txt").read_text() == "secret\n" and [p.name for p in outside.iterdir()] == ["secret.txt"]
    assert L.verify() == [] and not any(r["status"] == "pending" for r in L.list())
    L.rewind(0)
    assert snapshot(root) == start and L.verify() == []
    assert (root / ".agent" / "ledger.db").exists()


# ---- environment changes rewind together with files (real uv) ----
@pytest.mark.skipif(not shutil.which("uv"), reason="uv not installed")
def test_venv_and_files_rewind_together(proj):
    root, L, t = proj
    start = snapshot(root)
    t["run_command"]("uv venv")
    t["create_file"](path="requirements.txt", content="# none\n")
    t["run_command"]("echo built > build.log")
    assert env_state(root)["venv"]
    L.rewind(0)
    assert snapshot(root) == start and env_state(root) == {"venv": False, "pip": {}, "npm": None}
