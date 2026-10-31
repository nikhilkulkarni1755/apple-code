"""Every hostile input must be rejected with ActionError, change nothing, and leave a 'rejected' record."""
import pytest

from agent.ledger import ActionError, Ledger
from tests.test_ledger import snapshot


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "file.txt").write_text("x\n")
    outside = tmp_path.parent / (tmp_path.name + "_outside")
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n")
    (tmp_path / "link_dir").symlink_to(outside, target_is_directory=True)
    (tmp_path / "link_file").symlink_to(outside / "secret.txt")
    yield tmp_path, Ledger(tmp_path), outside


def reject(ws, action, args):
    root, L, outside = ws
    before, secret = snapshot(root), (outside / "secret.txt").read_text()
    n = len(L.list())
    with pytest.raises(ActionError):
        L.do(action, args)
    assert snapshot(root) == before and (outside / "secret.txt").read_text() == secret
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    assert [r["status"] for r in L.list()[n:]] == ["rejected"]
    assert L.verify() == []


BAD_PATHS = ["", ".", "..", "../x.txt", "/etc/passwd", "sub/../../x.txt", "a.txt/../../x.txt", ".agent", ".agent/ledger.db",
             ".agent/blobs/x", "./.agent/x", "sub/../.agent/x", ".AGENT/x", ".Agent/ledger.db", "x\0y.txt",
             "link_dir/new.txt", "link_dir/secret.txt", "link_file", "sub", "a.txt/child.txt", "n" * 300 + ".txt", None, 5, ["a.txt"], {"p": 1}]


@pytest.mark.parametrize("path", BAD_PATHS, ids=[repr(p)[:30] for p in BAD_PATHS])
@pytest.mark.parametrize("action,extra", [
    ("create_file", {"content": "x\n"}),
    ("insert_lines", {"at_line": 1, "text": "x\n"}),
    ("delete_lines", {"start": 1, "end": 1}),
    ("replace_lines", {"start": 1, "end": 1, "text": "x\n"}),
    ("delete_file", {}),
])
def test_hostile_paths_are_rejected_for_every_action(ws, path, action, extra):
    reject(ws, action, {"path": path, **extra})


BAD_NUMS = [0, -1, -(10 ** 30), 10 ** 30, 99, True, False, 1.5, float("inf"), "1", None, [1], {"n": 1}]


@pytest.mark.parametrize("n", BAD_NUMS, ids=[repr(n)[:20] for n in BAD_NUMS])
def test_hostile_line_numbers_are_rejected(ws, n):
    reject(ws, "insert_lines", {"path": "a.txt", "at_line": n, "text": "x\n"})
    reject(ws, "delete_lines", {"path": "a.txt", "start": n, "end": 2})
    reject(ws, "delete_lines", {"path": "a.txt", "start": 1, "end": n})
    reject(ws, "replace_lines", {"path": "a.txt", "start": n, "end": n, "text": "x\n"})


BAD_TEXT = [None, 5, b"bytes", ["x\n"], {"t": 1}, True, "\ud800\n", "no newline", "", "a\nb", "x" * 1_100_000 + "\n"]


@pytest.mark.parametrize("text", BAD_TEXT, ids=[repr(t)[:20] for t in BAD_TEXT])
def test_hostile_text_is_rejected(ws, text):
    reject(ws, "insert_lines", {"path": "a.txt", "at_line": 1, "text": text})
    reject(ws, "replace_lines", {"path": "a.txt", "start": 1, "end": 1, "text": text})
    if text != "" and text != "no newline" and text != "a\nb":  # create_file may legally take these
        reject(ws, "create_file", {"path": "new.txt", "content": text})


@pytest.mark.parametrize("bad", [None, 5, b"x", ["a"], "\ud800"])
def test_hostile_expected_is_rejected(ws, bad):
    reject(ws, "delete_lines", {"path": "a.txt", "start": 1, "end": 1, "expected": bad})
    reject(ws, "replace_lines", {"path": "a.txt", "start": 1, "end": 1, "text": "x\n", "expected": bad})


@pytest.mark.parametrize("action", [None, "", 5, "CREATE_FILE", "create_file;", "../create_file", ["create_file"], "run_command", "undo"])
def test_hostile_action_names_are_rejected(ws, action):
    reject(ws, action, {"path": "n.txt", "content": "x\n"})


@pytest.mark.parametrize("args", [None, [], "a.txt", 5, {}, {"content": "x"}, {"path": "n.txt"}, {"path": "a.txt", "at_line": 1}])
def test_malformed_args_are_rejected(ws, args):
    reject(ws, "create_file", args)
    reject(ws, "insert_lines", args)


def test_files_that_are_not_text_are_rejected_not_clobbered(ws):
    root, _, _ = ws
    (root / "bin.dat").write_bytes(b"\xff\xfe\x00binary")
    reject(ws, "insert_lines", {"path": "bin.dat", "at_line": 1, "text": "x\n"})
    reject(ws, "delete_file", {"path": "bin.dat"})
    assert (root / "bin.dat").read_bytes() == b"\xff\xfe\x00binary"


def test_oversized_resulting_file_is_rejected(ws):
    root, L, _ = ws
    (root / "big.txt").write_text(("y" * 99 + "\n") * 9_000)  # 900 KB, within the limit
    L.do("insert_lines", {"path": "big.txt", "at_line": 1, "text": "z" * 50_000 + "\n"})
    reject(ws, "insert_lines", {"path": "big.txt", "at_line": 1, "text": "z" * 100_000 + "\n"})


@pytest.mark.parametrize("bad", [None, "abc", -1, 0, 99999, 1.5, [1], True])
def test_undo_of_nonsense_seq_is_rejected(ws, bad):
    root, L, _ = ws
    L.do("insert_lines", {"path": "a.txt", "at_line": 1, "text": "x\n"})
    before = snapshot(root)
    with pytest.raises(ActionError):
        L.undo(bad)
    assert snapshot(root) == before


def test_double_undo_and_undoing_a_rejected_record_are_refused(ws):
    root, L, _ = ws
    seq = L.do("insert_lines", {"path": "a.txt", "at_line": 1, "text": "x\n"})
    L.undo(seq)
    with pytest.raises(ActionError):
        L.undo(seq)
    with pytest.raises(ActionError):
        L.do("delete_file", {"path": "zzz"})
    with pytest.raises(ActionError):
        L.undo(L.list()[-1]["seq"])
    L.redo()
    with pytest.raises(ActionError, match="nothing to redo"):
        L.redo()


# ---- inputs that look odd but are legal: must work and be reversible ----
LEGAL = ["x\n", "tab\there\n", "unicode ✓ é 日本語 🎉\n", "\n", "\n\n\n", "crlf\r\nline\r\n", "lone\rcr\n", "nul\0inside\n", "﻿bom\n",
         "quote ' \" back\\slash {} %s $x `cmd`\n", "x" * 100_000 + "\n"]


@pytest.mark.parametrize("text", LEGAL, ids=[repr(t)[:20] for t in LEGAL])
def test_odd_but_legal_text_roundtrips_and_reverses_exactly(ws, text):
    root, L, _ = ws
    before = snapshot(root)
    L.do("create_file", {"path": "new/ünï.txt", "content": text})
    assert (root / "new" / "ünï.txt").read_bytes() == text.encode()
    L.do("insert_lines", {"path": "a.txt", "at_line": 2, "text": text})
    L.do("replace_lines", {"path": "a.txt", "start": 1, "end": 1, "text": text})
    assert L.verify() == []
    L.rewind(0)
    assert snapshot(root) == before and L.verify() == []


def test_symlink_inside_workspace_is_followed_safely(ws):
    root, L, _ = ws
    (root / "alias.txt").symlink_to(root / "a.txt")
    L.do("insert_lines", {"path": "alias.txt", "at_line": 1, "text": "via alias\n"})
    assert (root / "a.txt").read_text().startswith("via alias\n")
    L.undo_last()
    assert (root / "a.txt").read_text() == "one\ntwo\nthree\n"


def test_crlf_file_edit_keeps_the_other_lines_byte_identical(ws):
    root, L, _ = ws
    (root / "w.txt").write_bytes(b"a\r\nb\r\nc\r\n")
    L.do("replace_lines", {"path": "w.txt", "start": 2, "end": 2, "text": "B\r\n"})
    assert (root / "w.txt").read_bytes() == b"a\r\nB\r\nc\r\n"
    L.undo_last()
    assert (root / "w.txt").read_bytes() == b"a\r\nb\r\nc\r\n"
