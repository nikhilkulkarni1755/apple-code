import pytest

from agent.evidence import gather, outline
from agent.ledger import Ledger


@pytest.fixture
def L(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return Ledger(root)


def put(L, name, text):
    f = L.root / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)


def test_outline_lists_functions_classes_methods_and_constants():
    src = ("LIMIT = 3\nname = 'x'\n\n\ndef add(a, b=2, *args, key=None, **kw):\n    return a + b\n\n\nasync def fetch(url):\n    pass\n\n\n"
           "class Game:\n    SPEED = 5\n    class Inner:\n        pass\n\n    def tick(self, seconds):\n        pass\n\n    @property\n    def score(self):\n        return 0\n\n\nclass Empty:\n    pass\n")
    assert outline(src) == ["LIMIT", "def add(a, b, key)", "def fetch(url)", "class Game: Inner, tick(self, seconds), score(self), SPEED", "class Empty"]
    assert outline("") == [] and outline("def broken(:\n") is None and outline("x = \x00") is None


def test_an_empty_project_says_so(L):
    assert gather(L, "anything") == "The project has no files yet."
    put(L, "MEMORY.md", "# notes\n")
    assert gather(L, "anything") == "The project has no files yet."          # the agent's own notes are not the project


def test_the_outline_describes_code_tests_and_other_files(L):
    put(L, "calc.py", "def add(a, b):\n    return a + b\n")
    put(L, "pkg/game.py", "class Game:\n    def tick(self, s):\n        pass\n")
    put(L, "test_calc.py", "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    put(L, "README.md", "# hi\n")
    put(L, "broken.py", "def x(:\n")
    put(L, "empty.py", "")
    text = gather(L, "do something")
    assert "  calc.py: def add(a, b)" in text and "  pkg/game.py: class Game: tick(self, s)" in text and "  broken.py: (does not parse)" in text
    assert "  empty.py: (empty)" in text and "Tests: test_calc.py" in text and "Other files: README.md" in text
    assert "test_add" not in text.split("Tests:")[0]                                       # tests are listed, not outlined


def test_files_the_task_names_are_included_in_full_when_small(L):
    put(L, "calc.py", "def add(a, b):\n    return a + b\n")
    put(L, "other.py", "SECRET_FILE_CONTENT = 1\n")
    text = gather(L, "Add sub() to calc.py, following add")
    assert "Contents of calc.py:\ndef add(a, b):\n    return a + b" in text and "SECRET_FILE_CONTENT" not in text.split("Contents of")[1]


def test_big_files_are_outlined_but_not_pasted(L):
    put(L, "big.py", "def f():\n    return 1\n" + "# filler\n" * 200)
    text = gather(L, "change big.py")
    assert "big.py: def f()" in text and "Contents of" not in text


def test_the_budget_is_respected_however_large_the_project(L):
    for i in range(80):
        put(L, f"module_{i}.py", "".join(f"def function_number_{j}(argument_one, argument_two):\n    pass\n" for j in range(10)))
    text = gather(L, "touch module_1.py", budget=1800)
    assert len(text) <= 1800 and text.startswith("Code in the project:")
    assert len(gather(L, "x", budget=300)) <= 300


@pytest.mark.parametrize("task_text", ["read ../../etc/passwd", "open /etc/hosts and .agent/ledger.db", "show link.txt", "edit MEMORY.md", "a\0b.py x.py\n../x.py",
                                       "x" * 10_000, "", "..", "node_modules/pkg/index.js"])
def test_hostile_task_text_cannot_make_it_read_anything_outside_the_project(L, tmp_path, task_text):
    (tmp_path / "outside.txt").write_text("OUTSIDE SECRET")
    (L.root / "link.txt").symlink_to(tmp_path / "outside.txt")
    put(L, "calc.py", "def add(a, b):\n    return a + b\n")
    put(L, "MEMORY.md", "private plan SECRET_PLAN\n")
    text = gather(L, task_text)
    assert "OUTSIDE SECRET" not in text and "SECRET_PLAN" not in text and "root:" not in text and "def add(a, b)" in text
