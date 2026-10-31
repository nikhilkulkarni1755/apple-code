"""Model-facing tools: correct results for good input; hostile input is rejected and changes nothing."""
import time

import pytest

from agent.ledger import Ledger
from agent.tools import make_tools
from tests.test_ledger import snapshot


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "f.py").write_text("import os\nx = 1\n# TODO\n    return a - b\n")
    (root / "crlf.txt").write_bytes(b"a\r\nb\r\nc\r\n")
    (root / "empty.txt").write_text("")
    (root / "bin.dat").write_bytes(b"\xff\xfe\x00")
    (root / "many.txt").write_text("".join(f"line {i}\n" for i in range(1, 501)))
    (root / "sub").mkdir()
    (root / "sub" / "g.py").write_text("y = 2\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.py").write_text("junk = 1\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    (root / "link").symlink_to(outside)
    L = Ledger(root)
    return root, L, {t.name: t.handler for t in make_tools(L)}, outside


def rejects(env, name, **args):
    root, L, t, outside = env
    before, n_ok = snapshot(root), sum(r["status"] == "ok" for r in L.list())
    with pytest.raises(Exception):
        t[name](**args)
    assert snapshot(root) == before and outside.read_text() == "secret\n"
    assert sum(r["status"] == "ok" for r in L.list()) == n_ok


ESCAPES = ["../outside.txt", "/etc/passwd", "link", ".agent/ledger.db", ".AGENT/ledger.db", "sub/../../outside.txt", "", ".", "x\0y", None, 5, ["f.py"]]


# ------------------------------------------------------------------ read tools
def test_read_file_good_inputs(env):
    _, _, t, _ = env
    assert t["read_file"](path="f.py") == "import os\nx = 1\n# TODO\n    return a - b"
    assert t["read_file"](path="f.py", start=2, end=3) == "x = 1\n# TODO"
    assert t["read_file"](path="f.py", start=4, end=999) == "    return a - b"
    assert t["read_file"](path="empty.txt") == "(empty)"
    assert t["read_file"](path="crlf.txt", start=2, end=2) == "b\r"
    assert len(t["read_file"](path="many.txt").splitlines()) == 200
    assert t["read_file"](path="many.txt", start=499).splitlines() == ["line 499", "line 500"]
    assert t["read_file"](path="nope.py").startswith("error")


@pytest.mark.parametrize("path", ESCAPES + ["bin.dat", "sub"])
def test_read_file_rejects_hostile_paths(env, path):
    rejects(env, "read_file", path=path)


@pytest.mark.parametrize("start,end", [(0, 3), (-5, 3), (3, 1), (True, 2), (1.5, 2), ("1", 2), (1, "2"), (None, 2), (10 ** 30, None), (1, -1), (1, 0)])
def test_read_file_rejects_hostile_ranges(env, start, end):
    rejects(env, "read_file", path="f.py", start=start, end=end)


def test_list_files_good_and_hostile(env):
    _, _, t, _ = env
    assert t["list_files"]("*.py").splitlines() == ["f.py", "sub/g.py"]
    assert "node_modules/junk.py" not in t["list_files"]() and "bin.dat" not in t["list_files"]()
    for pattern in ["[", "", "[!", "\\", "*" * 5000, "\0", "../*"]:
        assert t["list_files"](pattern) in ("(none)", t["list_files"](pattern))   # never raises, never lists outside
    assert "outside" not in t["list_files"]("*")
    for bad in (None, 5, ["*"]):
        rejects(env, "list_files", pattern=bad)


def test_grep_good_inputs(env):
    _, _, t, _ = env
    assert t["grep"]("TODO") == "f.py:3: # TODO"
    assert t["grep"]("y", "sub/*") == "sub/g.py:1: y = 2"
    assert t["grep"]("zzz-no-match") == "(no matches)"
    assert len(t["grep"]("line").splitlines()) == 50                    # capped
    assert t["grep"]("junk") == "(no matches)"       # node_modules is skipped
    assert t["grep"]("secret") == "(no matches)"     # a symlink to a file outside the workspace is not followed
    assert "link" not in t["list_files"]()


@pytest.mark.parametrize("pattern", ["(", "[a-", "*abc", "(?P<n", "\\", None, 5, ["a"]])
def test_grep_rejects_invalid_patterns(env, pattern):
    rejects(env, "grep", pattern=pattern)


def test_grep_survives_catastrophic_regex(env):
    root, _, t, _ = env
    (root / "evil.txt").write_text("a" * 40 + "!\n")
    start = time.time()
    with pytest.raises(Exception, match="longer than"):
        t["grep"](r"(a+)+$")
    assert time.time() - start < 10


# ------------------------------------------------------------------ editing tools: good inputs
def test_create_file_good_and_undo(env):
    root, L, t, _ = env
    before = snapshot(root)
    assert t["create_file"](path="new/dir/n.py", content="print(1)\n").startswith("ok")
    assert (root / "new/dir/n.py").read_text() == "print(1)\n"
    L.undo_last()
    assert snapshot(root) == before and not (root / "new").exists()


def test_replace_text_variants(env):
    root, L, t, _ = env
    t["replace_text"](path="f.py", old="return a - b", new="return a + b")
    t["replace_text"](path="f.py", old="import os\nx = 1\n", new="import os\nimport sys\nx = 1\n")       # multi-line
    t["replace_text"](path="f.py", old="# TODO", new="")                                                # deletes the whole line
    t["replace_text"](path="crlf.txt", old="b", new="B")                                                # CRLF untouched
    assert (root / "f.py").read_text() == "import os\nimport sys\nx = 1\n    return a + b\n"
    assert (root / "crlf.txt").read_bytes() == b"a\r\nB\r\nc\r\n"


def test_insert_after_variants(env):
    root, _, t, _ = env
    t["insert_after"](path="f.py", anchor="import os", text="import sys")
    t["insert_after"](path="f.py", anchor="return a - b", text="\n# end\n\n")                           # appends at EOF
    assert (root / "f.py").read_text() == "import os\nimport sys\nx = 1\n# TODO\n    return a - b\n\n# end\n"
    t["insert_after"](path="crlf.txt", anchor="b", text="B2\nB3")
    assert (root / "crlf.txt").read_bytes() == b"a\r\nb\r\nB2\r\nB3\r\nc\r\n"                          # keeps CRLF endings
    t["insert_after"](path="many.txt", anchor="line 500", text="last")
    assert (root / "many.txt").read_text().endswith("line 500\nlast\n")


def test_delete_file_good_and_undo(env):
    root, L, t, _ = env
    before = snapshot(root)
    t["delete_file"](path="sub/g.py")
    assert not (root / "sub" / "g.py").exists()
    L.undo_last()
    assert snapshot(root) == before


# ------------------------------------------------------------------ editing tools: hostile inputs
@pytest.mark.parametrize("path", ESCAPES + ["bin.dat", "sub"])
def test_editing_tools_reject_hostile_paths(env, path):
    rejects(env, "create_file", path=path, content="x\n")
    rejects(env, "replace_text", path=path, old="a", new="b")
    rejects(env, "insert_after", path=path, anchor="a", text="b")
    rejects(env, "delete_file", path=path)


def test_editing_a_missing_file_is_rejected(env):
    rejects(env, "replace_text", path="missing.py", old="a", new="b")
    rejects(env, "insert_after", path="missing.py", anchor="a", text="b")
    rejects(env, "delete_file", path="missing.py")


@pytest.mark.parametrize("old,new", [("", "x"), ("t", "y"), ("zzz", "y"), ("x = 1", "x = 1"), (None, "y"), (5, "y"), ("x = 1", None),
                                     ("x = 1", 5), ("x = 1", ["a"]), ("x = 1", "\ud800"), ("x = 1", "y" * 1_100_000), ("x = 1\n#zzz", "z")])
def test_replace_text_rejects_hostile_snippets(env, old, new):
    rejects(env, "replace_text", path="f.py", old=old, new=new)


@pytest.mark.parametrize("anchor,text", [("", "x"), ("t", "y"), ("zzz", "y"), (None, "y"), (5, "y"), ("x = 1", None), ("x = 1", 5),
                                         ("x = 1", "\ud800"), ("x = 1", "y" * 1_100_000)])
def test_insert_after_rejects_hostile_snippets(env, anchor, text):
    rejects(env, "insert_after", path="f.py", anchor=anchor, text=text)


@pytest.mark.parametrize("content", [None, 5, ["x"], b"x", "\ud800", "y" * 1_100_000])
def test_create_file_rejects_hostile_content(env, content):
    rejects(env, "create_file", path="new.txt", content=content)


def test_create_file_refuses_to_overwrite(env):
    rejects(env, "create_file", path="f.py", content="clobber\n")
    rejects(env, "create_file", path="crlf.txt", content="clobber\n")


def test_multiline_old_text_does_not_match_crlf_files_and_is_rejected(env):
    rejects(env, "replace_text", path="crlf.txt", old="a\nb", new="X")


# ------------------------------------------------------------------ command tools
def test_run_command_and_run_tests_report_results(env):
    root, _, t, _ = env
    out = t["run_command"]("echo hi > made.txt; echo done")
    assert out.startswith("exit 0; changed: made.txt") and out.endswith("done\n")
    (root / "test_x.py").write_text("def test_a():\n    assert 1 == 2\n")
    assert t["run_tests"]("test_x.py").startswith("exit 1")
    assert t["run_tests"]("test_x.py -k nomatch").startswith("exit 5")
    assert "cut" in t["run_command"]("head -c 50000 /dev/zero | tr '\\0' 'a'")                          # output is clipped for the model
    assert t["run_command"]("sleep 0.1; exit 7").startswith("exit 7")


@pytest.mark.parametrize("cmd", ["sudo ls", "rm -rf /", "cat .agent/ledger.db", "", None, 5])
def test_run_command_tool_rejects_dangerous_or_malformed(env, cmd):
    rejects(env, "run_command", cmd=cmd)


@pytest.mark.parametrize("args", ["; sudo ls", "&& rm -rf /", "$(sudo ls)"])
def test_run_tests_tool_cannot_smuggle_denied_commands(env, args):
    rejects(env, "run_tests", args=args)
