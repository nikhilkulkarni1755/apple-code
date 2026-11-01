import subprocess
import sys

import pytest

from agent.ledger import Ledger
from agent.spec import MAX_EXAMPLES, ensure_spec_test, join_examples, module_of, parse_examples, render_test, spec_test_path


@pytest.mark.parametrize("text,expected", [
    ("add(1, 2) == 3", ["add(1, 2) == 3"]),
    ("add(1, 2) == 3; add(0, 0) == 0", ["add(1, 2) == 3", "add(0, 0) == 0"]),
    ("add(1, 2) == 3\nadd(0, 0) == 0", ["add(1, 2) == 3", "add(0, 0) == 0"]),
    ("`add(1, 2) == 3`", ["add(1, 2) == 3"]),
    ("  add( 1,2 )==3  ", ["add(1, 2) == 3"]),                                   # normalised: the text is rewritten from the parsed form
    ("Game().tick(3) is None", ["Game().tick(3) is None"]),
    ("f(x=1, y=[1, 2]) == {'k': (1, 2)}", ["f(x=1, y=[1, 2]) == {'k': (1, 2)}"]),
    ("abs(-3) == 3", ["abs(-3) == 3"]), ("f(1) in (1, 2)", ["f(1) in (1, 2)"]), ("f(1) != None", ["f(1) != None"]),
    ("0 < f(1) < 5", ["0 < f(1) < 5"]), ("f(2 ** 3 + 1) == 9", ["f(2 ** 3 + 1) == 9"]), ("f('a')[0] == 'a'", ["f('a')[0] == 'a'"]),
    ("f(1) == 1; f(1) == 1", ["f(1) == 1"]),                                      # duplicates collapse
    ("", []), (None, []), ("just words", []), ("f(1)", []), ("1 == 1", []), ("x = f(1)", []), ("f(1) ==", []),
])
def test_ordinary_examples_are_accepted_and_normalised(text, expected):
    assert parse_examples(text) == expected


HOSTILE = [
    "__import__('os').system('echo pwned') == 0", "open('/etc/passwd').read() == ''", "eval('1') == 1", "exec('x=1') == None", "compile('1','','eval') == 1",
    "getattr(f, 'x') == 1", "setattr(f, 'x', 1) == None", "globals() == {}", "locals() == {}", "vars(f) == {}", "input() == ''", "breakpoint() == None",
    "exit() == 1", "print(1) == None", "os.remove('f') == None", "os.system('ls') == 0", "sys.exit(1) == 0", "subprocess.run(['ls']) == 0",
    "shutil.rmtree('/') == None", "importlib.import_module('os') == 1", "socket.socket() == 1", "pathlib.Path('x').write_text('y') == 1",
    "f().__class__.__bases__ == 1", "f()._private() == 1", "_hidden(1) == 1", "f(__builtins__) == 1", "f(__name__) == 'x'",
    "f(lambda: 1) == 1", "[x for x in range(3)] == f()", "{x: 1 for x in f()} == {}", "f(x := 3) == 3", "(yield) == 1", "f(*args) == 1", "f(**kw) == 1",
    "f(b'bytes') == 1", "f(1j) == 1", "f('" + "x" * 130 + "') == 1", "f(1) == 1 " + "+ 1" * 90, "f(1) == (1 if True else 2)", "f(1) == f'{1}'",
    "f(await g()) == 1", "f(1) == 1; import os", "f(1) == 1\nimport os", "f(1) == 1 # comment", "ctypes.CDLL(None) == 1", "pickle.loads(b'x') == 1",
    "tempfile.mkdtemp() == 1", "urllib.request.urlopen('http://x') == 1", "requests.get('http://x') == 1", "glob.glob('*') == []",
    "f(" * 500 + ")" * 500 + " == 1",
]


@pytest.mark.parametrize("example", HOSTILE, ids=[e[:30] for e in HOSTILE])
def test_hostile_examples_are_dropped_and_never_reach_a_file(example):
    got = parse_examples(example)
    assert all(bad not in "\n".join(got) for bad in ("__", "open(", "eval(", "exec(", "os.", "sys.", "lambda", "import")) , got


def test_a_hostile_example_does_not_poison_the_good_ones_beside_it():
    got = parse_examples("add(1, 2) == 3; __import__('os').system('x') == 0; add(0, 0) == 0")
    assert got == ["add(1, 2) == 3", "add(0, 0) == 0"]


def test_there_is_a_limit_on_how_many_examples_are_used():
    assert len(parse_examples("; ".join(f"f({i}) == {i}" for i in range(50)))) == MAX_EXAMPLES


def test_joined_examples_keep_whole_comparisons_only():
    exs = [f"function_number_{i}({i}) == {i}" for i in range(20)]
    joined = join_examples(exs, 100)
    assert len(joined) <= 100 and parse_examples(joined) == exs[:len(parse_examples(joined))] and len(parse_examples(joined)) >= 3
    assert join_examples([]) == "" and join_examples(["x" * 300 + "()  == 1"], 50) == ""


@pytest.mark.parametrize("text,expected", [
    ("Create score.py with a function", ("score", "score.py")), ("Add add_points to src/game/score.py please", ("src.game.score", "src/game/score.py")),
    ("first test_calc.py then calc.py", ("calc", "calc.py")), ("only test_calc.py and calc_test.py and conftest.py", None),
    ("../evil.py", None), ("/abs/path.py", None), ("./local.py", None), ("a/../b.py", None), ("my-module.py", None), ("1abc.py", None),
    ("sp ace.py", ("ace", "ace.py")), ("no file here", None), ("", None), (None, None), ("x.pyc and y.pyw", None), ("a" * 5000, None),
])
def test_module_of_finds_the_source_file_a_step_is_about(text, expected):
    assert module_of(text) == expected


def test_render_writes_one_test_per_example_importing_only_the_projects_names():
    text = render_test("score", ["add_points(10, 2) == 20", "len(final_score(2.5)) == 1", "Game().tick(3) is None"])
    assert text == ("from score import Game, add_points, final_score\n\n\ndef test_spec_1():\n    assert add_points(10, 2) == 20\n\n\n"
                    "def test_spec_2():\n    assert len(final_score(2.5)) == 1\n\n\ndef test_spec_3():\n    assert Game().tick(3) is None\n")
    compile(text, "t.py", "exec")
    assert render_test("m", ["abs(-1) == 1"]).startswith("import m\n")
    assert render_test("a.b.c", ["f(1) == 1"]).startswith("from a.b.c import f\n")


def test_the_spec_test_filename_is_derived_from_the_module():
    assert spec_test_path("score") == "test_score_spec.py" and spec_test_path("pkg.game") == "test_pkg_game_spec.py"


@pytest.fixture
def L(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return Ledger(root)


def test_the_harness_writes_the_tests_once_as_an_undoable_recorded_change(L):
    path = ensure_spec_test(L, "Create score.py with add_points", "add_points(10, 2) == 20; add_points(0, 0) == 0")
    assert path == "test_score_spec.py" and "def test_spec_2" in (L.root / path).read_text()
    assert [(r["action"], r["path"]) for r in L.list()] == [("create_file", "test_score_spec.py")]
    (L.root / path).write_text("# the model changed it\n")
    assert ensure_spec_test(L, "Create score.py", "add_points(10, 2) == 20") == path and (L.root / path).read_text() == "# the model changed it\n"   # never overwritten
    (L.root / path).write_text(render_test("score", ["add_points(10, 2) == 20", "add_points(0, 0) == 0"]))
    L.undo_last()
    assert not (L.root / path).exists() and L.verify() == []


@pytest.mark.parametrize("text,examples", [("Create score.py", ""), ("Create score.py", "no examples here"), ("Create something", "f(1) == 1"),
                                           ("Create score.py", "__import__('os') == 1"), ("", ""), (None, None), ("test_score.py only", "f(1) == 1")])
def test_nothing_is_written_without_usable_examples_and_a_named_module(L, text, examples):
    assert ensure_spec_test(L, text, examples) is None and L.list() == []


def test_generated_tests_really_run_under_pytest_and_fail_until_the_code_exists(L):
    ensure_spec_test(L, "Create score.py", "add_points(10, 2) == 20; add_points(0, 0) == 0")
    run = lambda: subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=L.root, capture_output=True, text=True)
    red = run()
    assert red.returncode != 0 and "ModuleNotFoundError" in red.stdout + red.stderr or "ImportError" in red.stdout + red.stderr
    (L.root / "score.py").write_text("def add_points(score, jumped):\n    return score + 5 * jumped\n")
    green = run()
    assert green.returncode == 0 and "2 passed" in green.stdout
    (L.root / "score.py").write_text("def add_points(score, jumped):\n    return score + jumped\n")
    assert run().returncode != 0
