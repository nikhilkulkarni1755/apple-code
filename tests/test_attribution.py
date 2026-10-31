"""Every ledger record says which agent and task it belongs to, what kind of work it was, and what it acted on; plus .DS_Store handling."""
import sqlite3

import pytest

from agent.ledger import _SCHEMA, ActionError, Ledger
from agent.tools import make_tools
from tests.test_ledger import snapshot


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.py").write_text("import os\nx = 1\n")
    (root / "b.py").write_text("y = 2\n")
    L = Ledger(root)
    return root, L, {t.name: t.handler for t in make_tools(L)}


def rows(L, **where):
    return [r for r in L.list() if all(r[k] == v for k, v in where.items())]


def test_every_record_carries_agent_task_kind_and_element(env):
    root, L, t = env
    with L.scope(agent="a1", task="1.2", kind="research"):
        t["grep"]("import", "*.py")
        t["read_file"](path="a.py")
        t["list_files"]("*.py")
    with L.scope(agent="a1", task="1.3", kind="code"):
        t["replace_text"](path="a.py", old="x = 1", new="x = 2")
        t["run_command"]("echo hi > out.txt")
    got = [(r["action"], r["element"], r["agent"], r["task_id"], r["kind"]) for r in L.list() if r["status"] == "ok" and r["cause"] is None]
    assert got == [("grep", "*.py", "a1", "1.2", "research"), ("read_file", "a.py", "a1", "1.2", "research"), ("list_files", "*.py", "a1", "1.2", "research"),
                   ("replace_lines", "a.py", "a1", "1.3", "code"), ("run_command", "echo hi > out.txt", "a1", "1.3", "code")]
    observed = rows(L, action="create_file")[0]                   # the file the command made inherits the command's attribution
    assert (observed["element"], observed["agent"], observed["task_id"], observed["cause"] is not None) == ("out.txt", "a1", "1.3", True)


def test_default_attribution_is_the_root_agent_with_no_task(env):
    _, L, t = env
    t["read_file"](path="a.py")
    r = L.list()[-1]
    assert (r["agent"], r["task_id"], r["kind"]) == ("root", None, None)


def test_scopes_nest_and_restore(env):
    _, L, t = env
    with L.scope(agent="a1", task="1"):
        with L.scope(task="1.1", kind="research"):
            t["read_file"](path="a.py")
        t["read_file"](path="b.py")
    t["read_file"](path="a.py")
    assert [(r["agent"], r["task_id"], r["kind"]) for r in L.list()] == [("a1", "1.1", "research"), ("a1", "1", None), ("root", None, None)]


def test_scope_is_restored_even_when_the_block_raises(env):
    _, L, _ = env
    with pytest.raises(RuntimeError):
        with L.scope(agent="a9", task="9"):
            raise RuntimeError
    assert L._scope == {"agent": "root", "task_id": None, "kind": None}


def test_rejected_calls_are_logged_too_with_their_error(env):
    _, L, t = env
    with L.scope(task="2", kind="research"):
        with pytest.raises(Exception):
            t["read_file"](path="../etc/passwd")
        with pytest.raises(Exception):
            t["grep"]("(")
        with pytest.raises(ActionError):
            t["replace_text"](path="a.py", old="nope", new="x")
    assert [(r["action"], r["status"], r["task_id"], r["kind"], bool(r["error"])) for r in L.list()] == [
        ("read_file", "rejected", "2", "research", True), ("grep", "rejected", "2", "research", True), ("replace_text", "rejected", "2", "research", True)]


def test_read_results_are_stored_in_full_while_the_model_sees_a_clipped_copy(env):
    root, L, t = env
    (root / "big.txt").write_text("".join(f"line number {i} with some padding text\n" for i in range(1, 150)))
    shown = t["read_file"](path="big.txt")
    logged = L.output(L.list()[-1]["seq"])
    assert len(logged) > len(shown) and "cut" in shown and "line number 149" in logged


def test_trail_collects_a_task_and_everything_nested_below_it(env):
    _, L, t = env
    for task in ("1", "1.1", "1.1.1", "1.2", "10", "2"):
        with L.scope(agent="a1", task=task):
            t["read_file"](path="a.py")
    with L.scope(agent="a2", task="1.1"):
        t["read_file"](path="b.py")
    assert [r["task_id"] for r in L.trail("1")] == ["1", "1.1", "1.1.1", "1.2", "1.1"]          # "10" is not under "1"
    assert [r["task_id"] for r in L.trail("1.1", agent="a1")] == ["1.1", "1.1.1"]
    assert L.trail("7") == []


def test_undo_task_refuses_when_another_agent_edited_the_same_file_afterwards(env):
    root, L, t = env
    with L.scope(agent="a1", task="3"):
        t["replace_text"](path="b.py", old="y = 2", new="y = 8")
    with L.scope(agent="a2", task="5"):
        t["replace_text"](path="b.py", old="y = 8", new="y = 9")
    with pytest.raises(ActionError):
        L.undo_task("a1", "3")
    assert (root / "b.py").read_text() == "y = 9\n"
    L.undo_task("a2", "5")
    L.undo_task("a1", "3")
    assert (root / "b.py").read_text() == "y = 2\n"


def test_undo_task_restores_files_when_nothing_conflicts(env):
    root, L, t = env
    before = snapshot(root)
    with L.scope(agent="a1", task="3"):
        t["replace_text"](path="a.py", old="x = 1", new="x = 9")
        t["create_file"](path="new.txt", content="n\n")
        t["run_command"]("echo built > made.txt")
    with L.scope(agent="a2", task="3"):
        t["replace_text"](path="b.py", old="y = 2", new="y = 8")
    assert len(L.undo_task("a1", "3")) == 3
    assert (root / "b.py").read_text() == "y = 8\n" and (root / "a.py").read_text() == "import os\nx = 1\n" and not (root / "new.txt").exists()
    L.undo_task("a2", "3")
    assert snapshot(root) == before and L.undo_task("a1", "3") == []   # nothing left to undo


def test_ledgers_created_before_attribution_are_migrated(tmp_path):
    root = tmp_path / "old"
    (root / ".agent").mkdir(parents=True)
    db = sqlite3.connect(root / ".agent" / "ledger.db")
    db.execute(_SCHEMA.replace(",\n    agent TEXT, task_id TEXT, kind TEXT, element TEXT)", ")"))   # the schema before attribution existed
    db.execute("INSERT INTO actions(ts, action, args, status) VALUES (1, 'old_action', '{}', 'ok')")
    db.commit()
    db.close()
    L = Ledger(root)
    L.log_read("grep", "*.py", {"pattern": "x"}, "result")
    last = L.list()[-1]
    assert last["element"] == "*.py" and last["agent"] == "root" and L.list()[0]["action"] == "old_action"


# ---------------------------------------------------------------- .DS_Store and other macOS metadata
def test_ds_store_files_are_never_tracked_listed_or_searched(env):
    root, L, t = env
    for d in (root, root / "sub"):
        d.mkdir(exist_ok=True)
        (d / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        (d / "._a.py").write_bytes(b"\x00\x05\x16\x07 AppleDouble")
    assert snapshot(root).keys() == {"a.py", "b.py"}
    assert "DS_Store" not in t["list_files"]() and "._" not in t["list_files"]()
    assert t["grep"]("Bud1") == "(no matches)"
    seq = L.run_command("touch sub/.DS_Store ._junk && echo hi > real.txt")
    assert [r["element"] for r in L.list() if r["cause"] == seq] == ["real.txt"]       # Finder files created by a command are not recorded
    assert L.verify() == []


def test_a_command_that_edits_ds_store_leaves_no_record_and_rewind_ignores_it(env):
    root, L, _ = env
    L.run_command("echo junk > .DS_Store")
    assert [r for r in L.list() if r["cause"]] == []
    L.rewind(0)
    assert (root / ".DS_Store").exists()                     # it is not the ledger's to remove or restore


def test_the_project_itself_contains_no_ds_store_files():
    from pathlib import Path
    project = Path(__file__).resolve().parent.parent
    found = [str(p.relative_to(project)) for p in project.rglob(".DS_Store") if ".venv" not in p.parts]
    assert found == [], f"run ./build.sh to remove: {found}"
