import random

import pytest

from agent.ledger import _SKIP_DIRS, ActionError, ConflictError, Ledger


def snapshot(root):
    """Every file the ledger tracks (it deliberately ignores .agent, caches, .venv and node_modules)."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file() and not _SKIP_DIRS & set(p.relative_to(root).parts)
            and p.name != ".DS_Store" and not p.name.startswith("._")}


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    (tmp_path / "tail.txt").write_text("x\nno newline")
    return tmp_path, Ledger(tmp_path)


ATOMIC = [
    ("create_file", {"path": "pkg/new.py", "content": "print(1)\n"}),
    ("insert_lines", {"path": "a.txt", "at_line": 2, "text": "ins1\nins2\n"}),
    ("insert_lines", {"path": "a.txt", "at_line": 4, "text": "end\n"}),
    ("delete_lines", {"path": "a.txt", "start": 1, "end": 2}),
    ("delete_lines", {"path": "tail.txt", "start": 2, "end": 2}),
    ("replace_lines", {"path": "a.txt", "start": 2, "end": 3, "text": "TWO\n"}),
    ("replace_lines", {"path": "tail.txt", "start": 2, "end": 2, "text": "tail"}),
    ("delete_file", {"path": "a.txt"}),
]


@pytest.mark.parametrize("action,args", ATOMIC)
def test_every_action_undoes_to_identical_bytes(ws, action, args):
    root, L = ws
    before = snapshot(root)
    L.do(action, args)
    assert snapshot(root) != before
    assert L.verify() == []
    L.undo_last()
    assert snapshot(root) == before
    assert L.verify() == []


def test_records_exact_line_diff(ws):
    _, L = ws
    seq = L.do("replace_lines", {"path": "a.txt", "start": 2, "end": 3, "text": "TWO\n"})
    r = L.get(seq)
    assert (r["status"], r["start_line"], r["removed"], r["added"]) == ("ok", 2, "two\nthree\n", "TWO\n")
    assert r["pre_hash"] != r["post_hash"]


def test_redo_reapplies_and_a_redo_can_be_undone(ws):
    root, L = ws
    before = snapshot(root)
    L.do("insert_lines", {"path": "a.txt", "at_line": 1, "text": "top\n"})
    after = snapshot(root)
    L.undo_last()
    assert snapshot(root) == before
    L.redo()
    assert snapshot(root) == after
    L.undo_last()  # undoes the redo
    assert snapshot(root) == before
    L.redo()
    assert snapshot(root) == after
    with pytest.raises(ActionError, match="nothing to redo"):
        L.redo()
    assert L.verify() == []


def test_undo_refuses_after_external_modification(ws):
    root, L = ws
    seq = L.do("insert_lines", {"path": "a.txt", "at_line": 1, "text": "top\n"})
    (root / "a.txt").write_text("changed outside\n")
    with pytest.raises(ConflictError):
        L.undo(seq)
    assert (root / "a.txt").read_text() == "changed outside\n"
    assert any("differs" in p for p in L.verify())


@pytest.mark.parametrize("action,args,msg", [
    ("create_file", {"path": "a.txt", "content": "x"}, "already exists"),
    ("create_file", {"path": "../out.txt", "content": "x"}, "outside workspace"),
    ("create_file", {"path": ".agent/ledger.db", "content": "x"}, "outside workspace"),
    ("insert_lines", {"path": "nope.txt", "at_line": 1, "text": "x\n"}, "does not exist"),
    ("insert_lines", {"path": "a.txt", "at_line": 9, "text": "x\n"}, "out of range"),
    ("insert_lines", {"path": "a.txt", "at_line": 1, "text": "no newline"}, "join lines"),
    ("insert_lines", {"path": "tail.txt", "at_line": 3, "text": "x\n"}, "join lines"),
    ("insert_lines", {"path": "a.txt", "at_line": 1, "text": ""}, "empty"),
    ("delete_lines", {"path": "a.txt", "start": 3, "end": 2}, "out of range"),
    ("delete_lines", {"path": "a.txt", "start": 1, "end": 1, "expected": "wrong\n"}, "expected"),
    ("delete_lines", {"path": "a.txt", "start": 1}, "bad arguments"),
    ("explode", {"path": "a.txt"}, "unknown action"),
])
def test_bad_actions_are_rejected_recorded_and_change_nothing(ws, action, args, msg):
    root, L = ws
    before = snapshot(root)
    with pytest.raises(ActionError, match=msg):
        L.do(action, args)
    assert snapshot(root) == before
    assert L.list()[-1]["status"] == "rejected"
    assert L.verify() == []


def test_crash_after_write_is_recovered_as_applied(ws, monkeypatch):
    root, L = ws

    def boom(self, seq, reverts):
        raise RuntimeError("crash")

    monkeypatch.setattr(Ledger, "_finalize", boom)
    with pytest.raises(RuntimeError):
        L.do("insert_lines", {"path": "a.txt", "at_line": 1, "text": "top\n"})
    assert L.list()[-1]["status"] == "pending"
    monkeypatch.undo()
    L2 = Ledger(root)
    assert L2.list()[-1]["status"] == "ok"
    assert L2.verify() == []
    L2.undo_last()
    assert (root / "a.txt").read_text() == "one\ntwo\nthree\n"


def test_crash_before_write_is_recovered_as_not_applied(ws, monkeypatch):
    root, L = ws

    def boom(self, full, text):
        raise RuntimeError("crash")

    monkeypatch.setattr(Ledger, "_write", boom)
    with pytest.raises(RuntimeError):
        L.do("insert_lines", {"path": "a.txt", "at_line": 1, "text": "top\n"})
    monkeypatch.undo()
    L2 = Ledger(root)
    assert L2.list()[-1]["status"] == "error"
    assert L2.verify() == []
    assert (root / "a.txt").read_text() == "one\ntwo\nthree\n"


def test_verify_detects_tampered_ledger(ws):
    _, L = ws
    L.do("insert_lines", {"path": "a.txt", "at_line": 1, "text": "top\n"})
    with L.db:
        L.db.execute("UPDATE actions SET args=? WHERE seq=1", ('{"path": "a.txt", "at_line": 1, "text": "TOP\\n"}',))
    assert L.verify()


@pytest.mark.parametrize("seed", range(20))
def test_random_sequences_then_full_rewind_restore_start(ws, seed):
    root, L = ws
    start = snapshot(root)
    rng = random.Random(seed)
    names = ["a.txt", "tail.txt", "n1.py", "pkg/n2.py", "pkg/sub/n3.py"]
    for _ in range(60):
        files = sorted(snapshot(root))
        path = rng.choice(names)
        text = "".join(f"r{rng.randint(0, 99)}\n" for _ in range(rng.randint(1, 3)))
        n = len((root / path).read_text().splitlines(True)) if path in files else 0
        a, b = sorted((rng.randint(1, max(n, 1)), rng.randint(1, max(n, 1))))
        action, args = rng.choice([
            ("create_file", {"path": path, "content": text}),
            ("insert_lines", {"path": path, "at_line": rng.randint(1, n + 1), "text": text}),
            ("delete_lines", {"path": path, "start": a, "end": b}),
            ("replace_lines", {"path": path, "start": a, "end": b, "text": text}),
            ("delete_file", {"path": path}),
        ])
        try:
            L.do(action, args)
        except ActionError:
            pass
        if rng.random() < 0.15:
            try:
                L.undo_last()
            except ActionError:
                pass
        if rng.random() < 0.1:
            try:
                L.redo()
            except ActionError:
                pass
    assert L.verify() == []
    L.rewind(0)
    assert snapshot(root) == start
    assert L.verify() == []


def test_a_file_changed_outside_the_ledger_is_recorded_not_refused(tmp_path):
    L = Ledger(tmp_path)
    L.do("create_file", {"path": "a.py", "content": "x = 1\n"})
    assert L.sync_external() == []
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "b.py").write_text("untracked\n")
    (seq,) = L.sync_external()
    r = L.get(seq)
    assert r["element"] == "modified externally" and r["task_id"] is None and "x = 2" in r["added"]
    assert L.sync_external() == []
    L.undo(seq)
    assert (tmp_path / "a.py").read_text() == "x = 1\n" and (tmp_path / "b.py").exists()
    (tmp_path / "a.py").unlink()
    assert len(L.sync_external()) == 1 and not (tmp_path / "a.py").exists()


def test_paths_a_task_wrote_are_listed_but_not_its_iteration_zero_spec_tests(tmp_path):
    L = Ledger(tmp_path)
    with L.scope(task="1", iteration=0):
        L.do("create_file", {"path": "test_spec.py", "content": "x\n"})
    with L.scope(task="1", iteration=1):
        L.do("create_file", {"path": "m.py", "content": "x\n"})
    L.do("create_file", {"path": "by_hand.py", "content": "x\n"})
    assert L.written_by_tasks() == {"m.py"}
