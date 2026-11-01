import pytest

from agent.verify import (acceptance_args, failure_summary, frozen_check, is_test_path, judge, protected_violations, red_check, syntax_errors,
                          unmentioned_deletions, vacuous_tests)

PASS = "....\n4 passed in 0.02s\n"
FAIL = ("F.\n=== FAILURES ===\n___ test_sub ___\n\n    def test_sub():\n>       assert sub(5, 3) == 2\nE       assert 8 == 2\n\n"
        "test_calc.py:9: AssertionError\n=== short test summary info ===\nFAILED test_calc.py::test_sub - assert 8 == 2\n1 failed, 1 passed in 0.03s\n")


@pytest.mark.parametrize("text,expected", [
    ("pytest test_calc.py::test_sub passes", "test_calc.py::test_sub"), ("`pytest -k sub`", "-k sub"), ("python -m pytest tests/ -x", "tests/ -x"),
    ("python3 -m pytest", ""), ("tests pass", ""), ("", ""), (None, ""), ("run pytest, then relax", ""),
    ("pytest; rm -rf /", ""), ("pytest && curl evil.sh", ""), ("pytest $(whoami)", ""), ("pytest `id`", ""), ("pytest a.py | sh", "a.py"), ("pytest \n sudo x", ""),
])
def test_acceptance_args_only_ever_pytest_arguments(text, expected):
    assert acceptance_args(text) == expected


@pytest.mark.parametrize("path,expected", [("test_calc.py", True), ("tests/helpers.py", True), ("pkg/tests/util.py", True), ("calc_test.py", True), ("conftest.py", True),
                                           ("a/b/test_x.py", True), ("calc.py", False), ("contest.py", False), ("testing.txt", False), ("tests/data.txt", False),
                                           ("src/attest.py", False), ("test/test_a.py", True)])
def test_which_files_are_tests(path, expected):
    assert is_test_path(path) is expected


@pytest.mark.parametrize("args,code,out,err,edited,ok,fragment", [
    ("", 0, PASS, None, True, True, ""),
    ("", 1, FAIL, None, True, False, "exited with code 1"),
    ("", 5, "no tests ran in 0.00s\n", None, True, False, "no tests were collected"),
    ("", 0, "no tests ran in 0.00s\n", None, True, False, "no test passed"),
    ("", None, "", "timed out after 300s", True, False, "did not finish"),
    ("", 0, "3 passed, 1 skipped in 0.1s\n", None, True, False, "skipped"),
    ("", 0, "3 passed, 1 xfailed in 0.1s\n", None, True, False, "xfailed"),
    ("", 0, "3 passed, 2 deselected in 0.1s\n", None, True, False, "deselected"),
    ("-k sub", 0, "1 passed, 2 deselected in 0.1s\n", None, True, True, ""),
    ("t.py::test_a", 0, "1 passed, 2 deselected in 0.1s\n", None, True, True, ""),
    ("", 0, PASS, None, False, False, "no files were changed"),
    ("", 2, "", None, True, False, "code 2"),
    ("", 0, "", None, True, False, "no test passed"),
    ("", 0, None, None, True, False, "no test passed"),
])
def test_the_verdict_needs_all_the_evidence(args, code, out, err, edited, ok, fragment):
    v = judge(args, code, out, err, edited)
    assert v.ok is ok and (not fragment or any(fragment in r for r in v.reasons))


@pytest.mark.parametrize("passed_now,before,ok", [(2, 1, True), (1, 1, False), (0, 1, False), (5, 0, True), (1, 0, True), (3, 3, False)])
def test_the_number_of_passing_tests_must_go_up(passed_now, before, ok):
    v = judge("", 0, f"{passed_now} passed in 0.1s\n", None, True, baseline_passed=before)
    assert (v.ok is ok) and (ok or any("no new test passes" in r for r in v.reasons) or passed_now == 0)


def test_without_a_baseline_the_count_is_not_compared():
    assert judge("", 0, PASS, None, True).ok


def test_every_reason_is_listed_not_just_the_first():
    v = judge("", 1, "1 failed, 1 skipped in 0.1s\n", None, False)
    assert len(v.reasons) >= 4 and v.summary == {"failed": 1, "skipped": 1}


def test_failure_summary_keeps_what_the_model_needs_and_is_bounded():
    s = failure_summary(FAIL)
    assert "FAILED test_calc.py::test_sub" in s and "E       assert 8 == 2" in s and "test_calc.py:9: AssertionError" in s and "1 failed, 1 passed" in s
    assert "def test_sub" not in s and len(failure_summary(FAIL * 50)) <= 700
    assert failure_summary("") == "" and failure_summary(None) == "" and failure_summary("garbage\nlines") == ""


BEFORE = "import os\nfrom calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n"


def violations(tmp_path, now):
    f = tmp_path / "test_calc.py"
    if now is None:
        f.unlink(missing_ok=True)
    else:
        f.write_text(now)
    return protected_violations(tmp_path, {"test_calc.py": BEFORE})


def test_existing_tests_may_grow_change_imports_reorder_and_be_retyped(tmp_path):
    assert violations(tmp_path, BEFORE) == []
    assert violations(tmp_path, BEFORE + "\n\ndef test_sub():\n    assert sub(5, 3) == 2\n") == []                       # grown
    assert violations(tmp_path, BEFORE.replace("from calc import add", "from calc import add, sub").replace("import os\n", "")) == []   # imports edited or dropped
    assert violations(tmp_path, BEFORE.replace("\n\n\n", "\n")) == []                                                      # blank lines are formatting
    assert violations(tmp_path, "def test_new():\n    pass\n\n\n" + BEFORE) == []                                          # new test first
    assert violations(tmp_path, BEFORE.replace("def test_add():\n", "def test_add():\n    x = 1  # added by the task\n")) == []
    assert violations(tmp_path, "from calc import add\ndef test_add():\n    assert add(0, 0) == 0\n    assert add(1, 2) == 3\n") == []   # reordered


def test_losing_or_altering_an_original_line_is_a_violation(tmp_path):
    msg = ["changed existing test code in test_calc.py"]
    assert violations(tmp_path, BEFORE.replace("== 3", "== 4")) == msg
    assert violations(tmp_path, BEFORE.replace("    assert add(0, 0) == 0\n", "")) == msg
    assert violations(tmp_path, "import os\n") == msg and violations(tmp_path, "") == msg
    assert violations(tmp_path, BEFORE.replace("    assert add(1, 2) == 3\n", "    assert add(1, 2) == 3  # fine\n")) == msg      # even a comment changes the line
    assert violations(tmp_path, None) == ["deleted the existing test file test_calc.py"]


def test_an_unreadable_test_file_counts_as_changed(tmp_path):
    (tmp_path / "test_calc.py").write_bytes(b"\xff\xfe")
    assert protected_violations(tmp_path, {"test_calc.py": BEFORE}) == ["changed existing test code in test_calc.py"]


def test_two_identical_lines_must_both_survive(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_a():\n    assert x\n    assert x\n")
    originals = {"test_x.py": "def test_a():\n    assert x\n    assert x\n"}
    assert protected_violations(tmp_path, originals) == []
    (tmp_path / "test_x.py").write_text("def test_a():\n    assert x\n")
    assert protected_violations(tmp_path, originals) != []


def test_deleting_a_file_the_task_never_mentions_is_flagged():
    rows = [{"path": "README.md", "action": "delete_file"}, {"path": "pkg/old.py", "action": "delete_file"}, {"path": "calc.py", "action": "replace_lines"}]
    assert unmentioned_deletions(rows, "Remove old.py") == ["deleted README.md, which the task does not mention"]
    assert unmentioned_deletions(rows, "Delete README.md and pkg/old.py") == []
    assert unmentioned_deletions(rows, "tidy up") != []


def test_syntax_errors_are_reported_with_file_and_line(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / "bad.py").write_text("def f()\n    return 1\n")
    (tmp_path / "odd.py").write_bytes(b"\xff\xfe = 1")
    (tmp_path / "notes.txt").write_text("def broken(")
    got = syntax_errors(tmp_path, ["ok.py", "bad.py", "bad.py", "notes.txt", "missing.py", "odd.py"])
    assert got[0].startswith("bad.py:1: ") and len(got) == 2 and got[1].startswith("odd.py")
    assert syntax_errors(tmp_path, []) == []


# ---- the frozen run: the original tests, untouched, against the code as it is now ----
ORIGINAL = {"test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"}


def project(tmp_path, calc="def add(a, b):\n    return a + b\n", tests=None):
    (tmp_path / "calc.py").write_text(calc)
    for name, text in (tests or ORIGINAL).items():
        (tmp_path / name).write_text(text)
    return tmp_path


def test_the_original_tests_pass_against_correct_code(tmp_path):
    root = project(tmp_path, calc="def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    assert frozen_check(root, ORIGINAL) == (True, "")


def test_a_weakened_test_does_not_hide_broken_code(tmp_path):
    root = project(tmp_path, calc="def add(a, b):\n    return a - b\n",                                    # the code is now wrong
                   tests={"test_calc.py": "from calc import add\n\n\ndef test_add():\n    return\n    assert add(1, 2) == 3\n"})   # and the test returns early
    ok, why = frozen_check(root, ORIGINAL)
    assert not ok and "assert" in why and "test_calc.py" in why


def test_a_deleted_or_skipped_original_test_is_restored_for_the_check(tmp_path):
    root = project(tmp_path, calc="def add(a, b):\n    return 0\n", tests={})
    assert frozen_check(root, ORIGINAL)[0] is False                                                       # the file was deleted, the original still runs
    (root / "test_calc.py").write_text("import pytest\n\n\n@pytest.mark.skip\ndef test_add():\n    pass\n")
    assert frozen_check(root, ORIGINAL)[0] is False


def test_the_check_runs_in_a_scratch_copy_and_leaves_the_project_alone(tmp_path):
    root = project(tmp_path, tests={"test_calc.py": "# edited\n"})
    before = {p.name: p.read_text() for p in root.iterdir()}
    frozen_check(root, ORIGINAL)
    assert {p.name: p.read_text() for p in root.iterdir()} == before                                      # test_calc.py is still the edited one


def test_helpers_and_conftest_come_back_too_and_nothing_to_check_is_fine(tmp_path):
    originals = {"conftest.py": "import pytest\n\n\n@pytest.fixture\ndef three():\n    return 3\n",
                 "test_x.py": "def test_three(three):\n    assert three == 3\n"}
    root = project(tmp_path, tests={"conftest.py": "# emptied\n", "test_x.py": "# emptied\n"})
    assert frozen_check(root, originals) == (True, "")
    assert frozen_check(root, {}) == (True, "") and frozen_check(root, {"conftest.py": "x = 1\n"}) == (True, "")


def test_a_hanging_original_test_fails_the_check(tmp_path):
    root = project(tmp_path, tests={})
    ok, why = frozen_check(root, {"test_slow.py": "import time\n\n\ndef test_slow():\n    time.sleep(30)\n"}, timeout=1)
    assert not ok and "timed out" in why



# ---- can a new test fail at all? ----
def vac(tmp_path, source, originals=None, roots=("calc",)):
    (tmp_path / "test_t.py").write_text(source)
    return vacuous_tests(tmp_path, originals or {}, ["test_t.py"], set(roots))


@pytest.mark.parametrize("source,expected", [
    ("from calc import add\n\n\ndef test_a():\n    assert add(1, 2) == 3\n", 0),
    ("import calc\n\n\ndef test_a():\n    assert calc.add(1, 2) == 3\n", 0),
    ("import calc as c\n\n\ndef test_a():\n    assert c.add(1, 2) == 3\n", 0),
    ("from calc import add as plus\n\n\ndef test_a():\n    assert plus(1, 2) == 3\n", 0),
    ("from . import calc\n\n\ndef test_a():\n    assert calc.add(1, 1) == 2\n", 0),
    ("import pytest\nfrom calc import add\n\n\ndef test_a():\n    with pytest.raises(TypeError):\n        add(1)\n", 0),
    ("import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(add(1, 2), 3)\n", 0),
    ("from calc import add\n\n\nclass TestAdd:\n    def test_a(self):\n        assert add(1, 2) == 3\n", 0),
    ("def test_a():\n    from calc import add\n    assert add(1, 2) == 3\n", 0),
    ("def test_a():\n    pass\n", 1),
    ("# the jump should work\n\n\ndef test_a():\n    # lots of comments\n    # and more comments\n    pass\n", 1),
    ("def test_a():\n    ...\n", 1),
    ('def test_a():\n    """docstring only"""\n', 1),
    ("from calc import add\n\n\ndef test_a():\n    add(1, 2)\n", 1),
    ("def test_a():\n    assert True\n", 1),
    ("def test_a():\n    assert 1 + 1 == 2\n", 1),
    ("from calc import add\n\n\ndef test_a():\n    assert True\n", 1),
    ("import os\n\n\ndef test_a():\n    assert os.sep\n", 1),
    ("from calc import add\n\n\ndef test_a():\n    pass\n\n\ndef test_b():\n    assert add(1, 1) == 2\n", 1),
    ("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n", 2),
])
def test_a_new_test_must_assert_something_about_the_projects_code(tmp_path, source, expected):
    assert len(vac(tmp_path, source)) == expected


def test_only_new_test_functions_are_judged_and_the_problem_names_the_test(tmp_path):
    old = "def test_old():\n    pass\n"
    got = vac(tmp_path, old + "\n\ndef test_new():\n    pass\n", originals={"test_t.py": old})
    assert got == ["test_t.py::test_new is a new test that asserts nothing (comments and `pass` check nothing)"]
    assert vac(tmp_path, old, originals={"test_t.py": old}) == []                          # an old, weak test is not this task's doing


def test_unparseable_or_missing_test_files_are_left_to_the_syntax_check(tmp_path):
    (tmp_path / "test_t.py").write_text("def test_a(:\n")
    assert vacuous_tests(tmp_path, {}, ["test_t.py", "missing.py"], {"calc"}) == []


def build(tmp_path, files):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return tmp_path


START = {"calc.py": "def add(a, b):\n    return a + b\n", "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"}


def test_red_then_green_new_tests_must_fail_on_the_old_code(tmp_path):
    now = {"calc.py": START["calc.py"] + "\n\ndef sub(a, b):\n    return a - b\n",
           "test_calc.py": "from calc import add, sub\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\ndef test_sub():\n    assert sub(5, 3) == 2\n"}
    ok, why = red_check(build(tmp_path, now), START)
    assert ok and why == ""                                                               # on the old code the import fails: red


def test_a_new_test_that_already_passed_before_the_change_is_rejected(tmp_path):
    now = {"calc.py": START["calc.py"] + "\n\ndef sub(a, b):\n    return a - b\n",
           "test_calc.py": START["test_calc.py"] + "\n\ndef test_add_zero():\n    assert add(0, 0) == 0\n"}
    ok, why = red_check(build(tmp_path, now), START)
    assert not ok and "also pass on the code as it was before your change" in why


def test_new_source_files_are_removed_for_the_red_run(tmp_path):
    now = {**START, "newmod.py": "VALUE = 1\n", "test_new.py": "from newmod import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n"}
    assert red_check(build(tmp_path, now), START)[0]                                      # newmod.py did not exist before: the test errors there


def test_red_check_changes_nothing_in_the_project(tmp_path):
    now = build(tmp_path, {"calc.py": "def add(a, b):\n    return 0\n", "test_calc.py": START["test_calc.py"]})
    before = {p.name: p.read_text() for p in now.iterdir()}
    red_check(now, START)
    assert {p.name: p.read_text() for p in now.iterdir()} == before


def test_tests_for_modules_not_written_yet_do_not_fail_the_check_of_the_named_file(tmp_path):
    root = project(tmp_path, calc="def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    both = {**ORIGINAL, "test_later.py": "from later import x\n\n\ndef test_x():\n    assert x() == 1\n"}
    (root / "test_later.py").write_text(both["test_later.py"])
    assert frozen_check(root, both)[0] is False
    assert frozen_check(root, both, args="test_calc.py") == (True, "")


def test_an_import_may_be_extended_but_not_replaced_by_code(tmp_path):
    root = project(tmp_path)
    assert protected_violations(root, {"test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"}) == []
    (root / "test_calc.py").write_text("from calc import add, sub\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    assert protected_violations(root, {"test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"}) == []
    (root / "test_calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    assert protected_violations(root, {"test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"}) == ["removed an import from test_calc.py"]


def test_a_missing_import_in_a_test_is_named(tmp_path):
    from agent.verify import import_hint
    (tmp_path / "words.py").write_text("def word_count(text):\n    return len(text.split())\n")
    (tmp_path / "test_words.py").write_text("def test_x(): pass\n")
    out = "E   NameError: name 'word_count' is not defined\nE   NameError: name 'other' is not defined\nE   NameError: name 'test_x' is not defined"
    assert import_hint(tmp_path, out) == "add `from words import word_count` at the top of the file that uses it"
    assert import_hint(tmp_path, "1 passed") == "" and import_hint(tmp_path, None) == ""


def test_a_syntax_error_shows_the_line_that_is_wrong(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return (1,\nx = 2 +\n")
    (tmp_path / "b.py").write_text("x = 1\n")
    got = syntax_errors(tmp_path, ["a.py", "b.py"])
    assert len(got) == 1 and got[0].startswith("a.py:") and "`" in got[0]


def test_an_import_of_a_name_the_module_does_not_define_is_named(tmp_path):
    from agent.verify import import_hint
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    out = "E   ImportError: cannot import name 'sub' from 'calc' (/x/calc.py)\nE   ImportError: cannot import name 'y' from 'os'"
    assert import_hint(tmp_path, out) == "define `sub` in calc.py"
