import pytest

from agent.ledger import Ledger
from agent.tools import make_tools
from tests.test_ledger import snapshot


@pytest.fixture
def env(tmp_path):
    (tmp_path / "f.py").write_text("import os\nx = 1\n# TODO\n    return a - b\n")
    (tmp_path / "nonl.txt").write_text("a\nb")
    L = Ledger(tmp_path)
    return tmp_path, L, {t.name: t.handler for t in make_tools(L)}


def test_replace_text_matches_inside_a_line_and_is_undoable(env):
    root, L, t = env
    before = snapshot(root)
    t["replace_text"](path="f.py", old="return a - b", new="return a + b")
    assert (root / "f.py").read_text().endswith("    return a + b\n")
    L.undo_last()
    assert snapshot(root) == before


@pytest.mark.parametrize("old,new", [("t", "y"), ("zzz", "y"), ("x = 1", "x = 1")])
def test_replace_text_needs_exactly_one_match_and_a_change(env, old, new):
    root, _, t = env
    before = snapshot(root)
    with pytest.raises(Exception):
        t["replace_text"](path="f.py", old=old, new=new)
    assert snapshot(root) == before


def test_deleting_text_removes_the_whole_line_but_keeps_surrounding_text(env):
    root, _, t = env
    t["replace_text"](path="f.py", old="# TODO", new="")
    assert (root / "f.py").read_text() == "import os\nx = 1\n    return a - b\n"
    t["replace_text"](path="f.py", old="x = 1", new="")
    t["replace_text"](path="f.py", old="return ", new="")
    assert (root / "f.py").read_text() == "import os\n    a - b\n"


def test_insert_after_adds_new_lines_and_can_append_to_a_file_without_final_newline(env):
    root, L, t = env
    t["insert_after"](path="f.py", anchor="import os", text="import sys")
    assert (root / "f.py").read_text().startswith("import os\nimport sys\nx = 1\n")
    t["insert_after"](path="nonl.txt", anchor="b", text="c\n")
    assert (root / "nonl.txt").read_text() == "a\nb\nc\n"
    L.rewind(0)
    assert (root / "nonl.txt").read_text() == "a\nb" and L.verify() == []


def test_read_tools(env):
    _, _, t = env
    assert t["read_file"](path="f.py", start=2, end=3) == "x = 1\n# TODO"
    assert "f.py" in t["list_files"]("*.py") and "nonl.txt" not in t["list_files"]("*.py")
    assert t["grep"]("TODO") == "f.py:3: # TODO"
    assert t["read_file"](path="nope.py").startswith("error")


def test_create_file_drops_a_markdown_fence_around_code(env):
    root, L, t = env
    t["create_file"](path="a.py", content="```python\ndef f():\n    return 1\n```\n```,path:")
    t["create_file"](path="b.md", content="```\nkept\n```\n")
    assert (root / "a.py").read_text() == "def f():\n    return 1\n" and (root / "b.md").read_text() == "```\nkept\n```\n"


def test_a_missed_replace_shows_the_start_of_the_file_so_the_model_can_copy_it(env):
    root, L, t = env
    with pytest.raises(Exception, match="found 0 times.*\n.*import os"):
        t["replace_text"](path="f.py", old="not in the file", new="y")
    with pytest.raises(Exception, match=r"found 11 times\)$"):
        t["replace_text"](path="f.py", old=" ", new="y")
