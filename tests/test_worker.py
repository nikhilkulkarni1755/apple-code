"""The worker loop against a scripted model and real pytest: good, buggy, cheating and idle attempts."""
import pytest

from agent.agents import AgentQueue, task_runner
from agent.ledger import Ledger
from agent.memory import Memory, Task
from agent.worker import INSTRUCTIONS, RESERVE, solve_step, work_task
from tests.test_ledger import snapshot

CALC = "def add(a, b):\n    return a + b\n"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


class FakeFM:
    """Plays the model. `attempts` is a list of functions (tools, prompt) -> optional run result, one per fresh session."""

    def __init__(self, attempts, context=8192):
        self.attempts, self.prompts, self.tool_names, self.context, self.calls = list(attempts), [], [], context, 0
        self.budgets, self.temperatures = [], []

    def count(self, tools, instructions="", prompt=""):
        return {"tools": 100, "instructions": 50, "prompt": len(prompt), "context": self.context}

    def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None, budget_tokens=None):
        self.prompts.append(prompt)
        self.budgets.append(budget_tokens)
        self.temperatures.append(temperature)
        self.tool_names.append([t.name for t in tools])
        attempt = self.attempts[min(self.calls, len(self.attempts) - 1)]
        self.calls += 1
        out = attempt({t.name: t.handler for t in tools}, prompt)
        return out if isinstance(out, dict) else {"content": "DONE", "stopped": False, "calls": []}     # an attempt's other return values are tool results


def good(t, prompt=None):                       # a correct, test-first change
    t["replace_text"](path="test_calc.py", old="from calc import add", new="from calc import add, sub")
    t["insert_after"](path="test_calc.py", anchor="assert add(1, 2) == 3", text="\n\ndef test_sub():\n    assert sub(5, 3) == 2")
    t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b")


def buggy(t, prompt=None):                      # the code is wrong, the test is right
    t["replace_text"](path="test_calc.py", old="from calc import add", new="from calc import add, sub")
    t["insert_after"](path="test_calc.py", anchor="assert add(1, 2) == 3", text="\n\ndef test_sub():\n    assert sub(5, 3) == 2")
    t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a + b")


def fix(t, prompt=None):
    t["replace_text"](path="calc.py", old="def sub(a, b):\n    return a + b", new="def sub(a, b):\n    return a - b")


def idle(t, prompt=None):
    return None


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    (root / "test_calc.py").write_text(TESTS)
    (root / "README.md").write_text("# calc\n")
    L = Ledger(root)
    m = Memory(L)
    m.add_plan("Add subtraction", [Task("Add sub() to calc.py", "with a test in test_calc.py", kind="code")])
    return root, L, m, m.tasks()[0]


def work(env, attempts, **kw):
    root, L, m, task = env
    fm = FakeFM(attempts, kw.pop("context", 8192))
    return fm, work_task(fm, L, m, task, **kw)


# ------------------------------------------------------------------ success
def test_a_correct_attempt_is_verified_by_the_harness_and_recorded_with_evidence(env):
    root, L, m, task = env
    fm, out = work(env, [good])
    assert out["ok"] and out["summary"].startswith("verified after 1 attempt(s); changed calc.py, test_calc.py")
    assert out["evidence"].startswith("run #") and "exit 0, 2 passed" in out["evidence"]
    assert (root / "calc.py").read_text().endswith("def sub(a, b):\n    return a - b\n")
    verify = [r for r in L.list() if r["action"] == "verify"]
    assert [(r["status"], r["task_id"], r["kind"], r["iteration"]) for r in verify] == [("ok", "1", "test", 1)]
    seq = int(out["evidence"].split("#")[1].split(":")[0])
    assert L.get(seq)["action"] == "run_command" and L.get(seq)["exit_code"] == 0 and L.get(seq)["iteration"] == 1 and "2 passed" in L.output(seq)
    assert {(r["kind"], r["iteration"]) for r in L.edits("root", "1")} == {("code", 1)} and L.verify() == []


def test_the_model_saying_done_without_doing_anything_does_not_count(env):
    root, L, m, task = env
    before = snapshot(root)
    fm, out = work(env, [idle])
    assert not out["ok"] and "no files were changed" in out["summary"] and snapshot(root) == before
    assert fm.calls == 3                                           # stopped early: no edits and the same failure twice in a row


def test_a_buggy_attempt_gets_the_failure_summary_and_the_next_attempt_fixes_it(env):
    root, L, m, task = env
    fm, out = work(env, [buggy, fix])
    assert out["ok"] and "after 2 attempt(s)" in out["summary"] and fm.calls == 2
    assert "Your last attempt failed" in fm.prompts[1] and "assert 8 == 2" in fm.prompts[1] and "FAILED" in fm.prompts[1]
    assert "Your last attempt failed" not in fm.prompts[0]                      # the baseline passed, so there was nothing to report
    v = [(r["iteration"], r["status"]) for r in L.list() if r["action"] == "verify"]
    assert v == [(1, "rejected"), (2, "ok")]


def test_when_the_tests_already_fail_the_first_prompt_says_so(env):
    root, L, m, task = env
    (root / "test_calc.py").write_text(TESTS + "\n\ndef test_sub():\n    from calc import sub\n    assert sub(5, 3) == 2\n")
    L2 = Ledger(root)
    fm, out = work((root, L2, m, task), [lambda t, p: t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b")])
    assert out["ok"] and "The tests currently fail:" in fm.prompts[0] and "ImportError" in fm.prompts[0] + "ImportError"


# ------------------------------------------------------------------ cheating and damage
def test_editing_an_existing_test_to_make_it_pass_is_undone_and_reported(env):
    root, L, m, task = env
    cheat = lambda t, p=None: (t["replace_text"](path="calc.py", old="return a + b", new="return a - b"),
                               t["replace_text"](path="test_calc.py", old="add(1, 2) == 3", new="add(1, 2) == -1"))
    fm, out = work(env, [cheat, good])
    assert out["ok"] and "assert add(1, 2) == 3" in (root / "test_calc.py").read_text() and "return a + b" in (root / "calc.py").read_text()
    assert "Your edits were undone because you changed existing test code in test_calc.py" in fm.prompts[1]
    assert [(r["iteration"], r["status"]) for r in L.list() if r["action"] == "verify"] == [(1, "rejected"), (2, "ok")]


def test_rewriting_an_existing_test_file_with_create_file_cannot_weaken_it(env):
    """create_file now replaces files; the whole-file comparison still catches a rewrite that drops an assertion."""
    root, L, m, task = env
    weaken = lambda t, p=None: (t["create_file"](path="test_calc.py", content="from calc import add\n\n\ndef test_add():\n    assert True\n"),
                                t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b"))
    fm, out = work(env, [weaken, good], max_iterations=2)
    assert "changed existing test code in test_calc.py" in fm.prompts[1] and out["ok"] and "assert add(1, 2) == 3" in (root / "test_calc.py").read_text()


def test_deleting_an_existing_test_file_is_undone(env):
    root, L, m, task = env
    (root / "test_extra.py").write_text("def test_extra():\n    assert True\n")
    L2 = Ledger(root)
    task2 = Task("Remove the old helper", "delete nothing else", kind="code", id="9")
    fm = FakeFM([lambda t, p: t["delete_file"](path="test_extra.py"), idle])
    out = work_task(fm, L2, m, task2, max_iterations=2)
    assert (root / "test_extra.py").exists() and "deleted the existing test file test_extra.py" in fm.prompts[1] and not out["ok"]


def test_the_delete_tool_is_offered_only_when_the_task_talks_about_deleting_or_removing(env):
    root, L, m, task = env
    fm, _ = work(env, [idle], max_iterations=1)
    assert "delete_file" not in fm.tool_names[0] and fm.tool_names[0] == ["read_file", "list_files", "grep", "create_file", "replace_text", "insert_after", "run_tests"]
    fm2 = FakeFM([idle])
    work_task(fm2, L, m, Task("Remove the old helper", "", kind="code", id="9"), max_iterations=1)
    assert "delete_file" in fm2.tool_names[0]


def test_deleting_a_file_the_task_does_not_name_is_undone(env):
    root, L, m, task = env
    t2 = Task("Remove the unused helper function", "from calc.py", kind="code", id="9")
    fm = FakeFM([lambda t, p: t["delete_file"](path="README.md"), idle])
    out = work_task(fm, L, m, t2, max_iterations=2)
    assert (root / "README.md").exists() and "deleted README.md, which the task does not mention" in fm.prompts[1] and not out["ok"]


def test_skipping_a_test_does_not_make_it_pass(env):
    root, L, m, task = env
    skip = lambda t, p=None: (t["create_file"](path="test_sub.py", content="import pytest\n\n\n@pytest.mark.skip\ndef test_sub():\n    assert False\n"),)
    fm, out = work(env, [skip], max_iterations=1)
    assert not out["ok"] and "1 test(s) skipped" in out["summary"]


def test_a_syntax_error_is_reported_with_its_line_and_the_tests_are_not_run(env):
    root, L, m, task = env
    broken = lambda t, p=None: t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b)\n    return a - b")
    fm, out = work(env, [broken, lambda t, p=None: (t["replace_text"](path="calc.py", old="def sub(a, b)\n", new="def sub(a, b):\n"), good_test_only(t))])
    assert out["ok"] and "Syntax error: calc.py:" in fm.prompts[1]
    ran = [r for r in L.list() if r["action"] == "run_command" and r["iteration"] == 1]
    assert ran == []                                                           # attempt 1 never reached pytest


def good_test_only(t):
    t["replace_text"](path="test_calc.py", old="from calc import add", new="from calc import add, sub")
    t["insert_after"](path="test_calc.py", anchor="assert add(1, 2) == 3", text="\n\ndef test_sub():\n    assert sub(5, 3) == 2")


def test_tests_written_where_pytest_never_looks_do_not_count_as_proof(env):
    """This really happened with the real model: the new test went into calc.py, the old test kept passing, and the work was 'verified'."""
    root, L, m, task = env
    wrong_place = lambda t, p=None: (t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b\n\n\n"
                                                                                           "def test_sub():\n    assert sub(5, 3) == 2"))
    fm, out = work(env, [wrong_place], max_iterations=1)
    assert not out["ok"] and "no new test passes (1 passed before and 1 now)" in out["summary"] and "(test_*.py)" in out["summary"]


def test_a_neutered_original_test_cannot_hide_broken_code(env):
    root, L, m, task = env
    sneaky = lambda t, p=None: (t["insert_after"](path="test_calc.py", anchor="def test_add():", text="    return"),            # the old test now passes vacuously
                                t["replace_text"](path="calc.py", old="return a + b", new="return a - b"),                     # the code is wrong
                                t["create_file"](path="test_new.py", content="from calc import add\n\n\ndef test_new():\n    assert add(5, 0) == 5\n"))   # passes even with the bug
    def repair(t, p=None):                                    # undo the damage, then do the job properly
        t["replace_text"](path="calc.py", old="return a - b", new="return a + b")
        t["replace_text"](path="test_calc.py", old="    return\n", new="")                 # removing what the task itself added is allowed
        good(t)

    fm, out = work(env, [sneaky, repair], max_iterations=2)
    assert "the original tests, unchanged, fail against your code" in fm.prompts[1] and "assert" in fm.prompts[1]
    assert out["ok"] and (root / "calc.py").read_text().startswith("def add(a, b):\n    return a + b\n")
    assert [(r["iteration"], r["status"]) for r in L.list() if r["action"] == "verify"] == [(1, "rejected"), (2, "ok")]


def test_retyping_the_existing_test_around_a_new_one_is_allowed(env):
    root, L, m, task = env
    retype = lambda t, p=None: (t["replace_text"](path="test_calc.py", old="from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                                                  new="from calc import add, sub\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\ndef test_sub():\n    assert sub(5, 3) == 2\n"),
                                t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b"))
    fm, out = work(env, [retype], max_iterations=1)
    assert out["ok"]


def test_a_test_that_is_only_comments_and_pass_is_not_evidence(env):
    """This really happened: the real model's 'verified' jump test was comments and `pass`, and the code it covered did not exist."""
    root, L, m, task = env
    hollow = lambda t, p=None: (t["create_file"](path="test_jump.py", content="# the jump should work\n\n\ndef test_jump():\n    # TODO\n    pass\n"),
                                t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef jump():\n    return 1"))
    leave_it = lambda t, p=None: t["insert_after"](path="calc.py", anchor="def jump():", text="    # still no real test")
    fix_it = lambda t, p=None: t["replace_text"](path="test_jump.py", old="    # TODO\n    pass\n", new="    from calc import jump\n    assert jump() == 1\n")
    fm, out = work(env, [hollow, leave_it, fix_it], max_iterations=3)
    assert "asserts nothing" in fm.prompts[1] and "test_jump.py::test_jump" in fm.prompts[1]
    assert "asserts nothing" in fm.prompts[2]                                      # leaving the hollow test in place is rejected again
    assert out["ok"] and "after 3 attempt(s)" in out["summary"]


def test_a_test_that_never_touches_the_projects_code_is_not_evidence(env):
    root, L, m, task = env
    unrelated = lambda t, p=None: (t["create_file"](path="test_x.py", content="def test_math():\n    assert 1 + 1 == 2\n"),
                                   t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b"))
    fm, out = work(env, [unrelated], max_iterations=1)
    assert not out["ok"] and "does not use any code from the project" in out["summary"]
    imported_but_unused = lambda t, p=None: (t["create_file"](path="test_y.py", content="from calc import add\n\n\ndef test_y():\n    assert True\n"),
                                             t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b"))
    fm, out = work(env, [imported_but_unused], max_iterations=1)
    assert not out["ok"] and "does not use any code from the project" in out["summary"]


def test_tests_that_pass_without_the_change_do_not_prove_the_change(env):
    root, L, m, task = env
    already_true = lambda t, p=None: (t["create_file"](path="test_more.py", content="from calc import add\n\n\ndef test_add_zero():\n    assert add(0, 0) == 0\n"),
                                      t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b"))
    fm, out = work(env, [already_true, good], max_iterations=2)
    assert "also pass on the code as it was before your change" in fm.prompts[1] and out["ok"]


def test_adding_a_test_for_code_that_already_exists_is_fine(env):
    """No source changed, so there is nothing to start red: the test only has to be a real one."""
    root, L, m, task = env
    only_a_test = lambda t, p=None: t["create_file"](path="test_more.py", content="from calc import add\n\n\ndef test_add_negative():\n    assert add(-1, 1) == 0\n")
    fm, out = work(env, [only_a_test], max_iterations=1)
    assert out["ok"] and "changed test_more.py" in out["summary"]


def test_a_project_that_is_already_red_is_not_held_to_the_red_check(env):
    root, L, m, task = env
    (root / "test_calc.py").write_text(TESTS + "\n\ndef test_sub():\n    from calc import sub\n    assert sub(5, 3) == 2\n")
    L2 = Ledger(root)
    fm, out = work((root, L2, m, task), [lambda t, p=None: t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b")], max_iterations=1)
    assert out["ok"]                                                          # the existing failing test is the proof


# ------------------------------------------------------------------ steps that give examples: the harness writes the tests
SPEC_TASK = lambda: Task("Add sub() to calc.py", "Create sub(a, b) returning a - b in calc.py", "sub(5, 3) == 2; sub(0, 4) == -4", kind="code", id="1")


@pytest.fixture
def spec_env(env):
    root, L, m, _ = env
    m.add_plan("Subtraction", [Task("Add sub() to calc.py", "Create sub(a, b) returning a - b in calc.py", "sub(5, 3) == 2; sub(0, 4) == -4", kind="code")])
    return root, L, m, m.tasks()[1]


def only_code(t, p=None):
    t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b")


def test_the_harness_writes_the_tests_from_the_examples_before_the_model_starts(spec_env):
    root, L, m, task = spec_env
    fm, out = work(spec_env, [only_code])
    assert out["ok"] and "tests are already written in test_calc_spec.py" in fm.prompts[0] and "do not edit that file" in fm.prompts[0]
    assert (root / "test_calc_spec.py").read_text() == "from calc import sub\n\n\ndef test_spec_1():\n    assert sub(5, 3) == 2\n\n\ndef test_spec_2():\n    assert sub(0, 4) == -4\n"
    assert out["summary"].endswith("changed calc.py") and "test_calc_spec" not in out["summary"]            # the model's work is the code, not the generated tests
    assert [(r["path"], r["iteration"], r["kind"]) for r in L.list() if r["path"] == "test_calc_spec.py"] == [("test_calc_spec.py", 0, "test")]


def test_with_examples_the_model_writes_no_tests_at_all_and_still_gets_verified(spec_env):
    root, L, m, task = spec_env
    before = {p.name for p in root.glob("*.py")}
    fm, out = work(spec_env, [only_code])
    assert out["ok"] and {p.name for p in root.glob("*.py")} - before == {"test_calc_spec.py"}
    assert "3 passed" in out["evidence"]                                           # the original test_add plus the two generated ones


def test_the_generated_tests_are_protected_like_any_other_original_test(spec_env):
    root, L, m, task = spec_env
    weaken = lambda t, p=None: (t["create_file"](path="test_calc_spec.py", content="from calc import add\n\n\ndef test_spec_1():\n    assert add(1, 1) == 2\n"),)
    fm, out = work(spec_env, [weaken, only_code], max_iterations=2)
    assert "changed existing test code in test_calc_spec.py" in fm.prompts[1] and out["ok"]
    assert "sub(5, 3) == 2" in (root / "test_calc_spec.py").read_text()


def test_doing_nothing_is_still_not_enough_when_the_tests_are_generated(spec_env):
    root, L, m, task = spec_env
    fm, out = work(spec_env, [idle], max_iterations=3)
    assert not out["ok"] and (root / "calc.py").read_text() == CALC and not (root / "test_calc_spec.py").exists()          # rolled back with the rest
    assert "pytest exited with code 2" in out["summary"] and "no files were changed" in out["summary"]     # the generated tests cannot even import calc.sub


def test_examples_that_cannot_be_met_block_the_step_instead_of_being_met_by_changing_them(spec_env):
    root, L, m, _ = spec_env
    m.add_plan("Impossible", [Task("Add sub() to calc.py", "Create sub in calc.py", "sub(5, 3) == 99", kind="code")])
    task = m.tasks()[2]
    fm, out = work((root, L, m, task), [only_code], max_iterations=2)
    assert not out["ok"] and "edits rolled back" in out["summary"]
    assert not (root / "test_calc_spec.py").exists()                                 # the unmeetable tests went away with the rest of the attempt


def test_a_step_without_examples_works_exactly_as_before(env):
    root, L, m, task = env
    fm, out = work(env, [good])
    assert out["ok"] and "already written" not in fm.prompts[0] and not (root / "test_calc_spec.py").exists()


def test_hostile_examples_in_a_step_write_nothing_and_do_not_stop_the_work(env):
    root, L, m, _ = env
    bad = Task("Add sub() to calc.py", "with a test", "__import__('os').system('touch pwned') == 0; open('x') == 1", kind="code", id="1")
    fm = FakeFM([good])
    out = work_task(fm, L, m, bad)
    assert out["ok"] and not (root / "pwned").exists() and not list(root.glob("test_*_spec.py"))


# ------------------------------------------------------------------ the model proposes the examples (propose=True)
class ProposingFM(FakeFM):
    """Answers the 'propose examples' session itself and leaves everything else to the scripted attempts."""

    def __init__(self, attempts, examples):
        super().__init__(attempts)
        self.examples, self.proposals = examples, 0

    def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None, budget_tokens=None):
        t = {x.name: x.handler for x in tools}
        if "add_example" in t:
            self.proposals += 1
            for e in self.examples:
                t["add_example"](e)
            return {"content": "", "stopped": False, "calls": []}
        return super().run(prompt, tools, instructions, max_tool_calls, temperature, budget_tokens)


@pytest.fixture
def plain_env(env):
    root, L, m, _ = env
    m.add_plan("Subtraction", [Task("Add sub() to calc.py", "Create sub(a, b) returning a - b in calc.py", kind="code")])
    return root, L, m, m.tasks()[1]


def test_proposed_examples_become_the_tests_and_are_kept_with_the_task(plain_env):
    root, L, m, task = plain_env
    lines = []
    fm = ProposingFM([only_code], ["sub(5, 3) == 2", "sub(0, 4) == -4"])
    out = work_task(fm, L, m, task, propose=True, say=lines.append)
    assert out["ok"] and fm.proposals == 1 and "tests are already written in test_calc_spec.py" in fm.prompts[-1]
    assert (root / "test_calc_spec.py").read_text().count("def test_spec_") == 2 and "3 passed" in out["evidence"]
    assert m.tasks()[1].done_when == "sub(5, 3) == 2; sub(0, 4) == -4" and Memory(Ledger(root)).tasks()[1].done_when == "sub(5, 3) == 2; sub(0, 4) == -4"
    assert any(l.startswith("  examples proposed: sub(5, 3) == 2") for l in lines)
    assert [r["action"] for r in L.list() if r["action"] == "examples"] == ["examples"]


def test_nothing_is_proposed_unless_asked(plain_env):
    root, L, m, task = plain_env
    fm = ProposingFM([good], ["sub(5, 3) == 2"])
    assert work_task(fm, L, m, task)["ok"] and fm.proposals == 0 and not list(root.glob("test_*_spec.py"))      # off by default


def test_a_step_that_already_has_examples_keeps_them_and_asks_no_one(spec_env):
    root, L, m, task = spec_env
    fm = ProposingFM([only_code], ["sub(5, 3) == 99"])
    assert work_task(fm, L, m, task, propose=True)["ok"] and fm.proposals == 0 and "99" not in (root / "test_calc_spec.py").read_text()


def test_no_proposal_is_made_for_a_step_that_names_no_source_file(env):
    root, L, m, _ = env
    m.add_plan("x", [Task("Tidy things up", "no file named", kind="code")])
    fm = ProposingFM([idle], ["f(1) == 1"])
    work_task(fm, L, m, m.tasks()[1], propose=True, max_iterations=1)
    assert fm.proposals == 0


def test_unusable_proposals_fall_back_to_the_model_writing_its_own_tests(plain_env):
    root, L, m, task = plain_env
    fm = ProposingFM([good], ["__import__('os').system('touch pwned') == 0", "gibberish"])
    out = work_task(fm, L, m, task, propose=True)
    assert out["ok"] and fm.proposals == 1 and not list(root.glob("test_*_spec.py")) and not (root / "pwned").exists() and m.tasks()[1].done_when == ""


def test_proposed_examples_that_do_not_match_what_the_step_asked_for_cannot_be_met_and_block_it(plain_env):
    root, L, m, task = plain_env
    fm = ProposingFM([only_code], ["sub(5, 3) == 99"])
    out = work_task(fm, L, m, task, propose=True, max_iterations=2)
    assert not out["ok"] and "edits rolled back" in out["summary"] and not (root / "test_calc_spec.py").exists()


def test_a_step_that_is_not_in_memory_still_works_with_proposed_examples(env):
    root, L, m, _ = env
    stray = Task("Add sub() to calc.py", "Create sub(a, b) in calc.py", kind="code", id="9")
    fm = ProposingFM([only_code], ["sub(5, 3) == 2"])
    assert work_task(fm, L, m, stray, propose=True)["ok"] and (root / "test_calc_spec.py").exists()


# ------------------------------------------------------------------ running out of room (the 7K budget)
BUDGET_STOP = {"content": "", "stopped": True, "calls": [], "reason": "budget", "tokens": 7300, "estimated": 7200, "context": 8192}


def runs_out(then):
    """An attempt that does some work and is stopped by the budget."""
    return lambda t, p=None: (then(t), BUDGET_STOP)[1]


def broken_code(t, p=None):
    t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b)\n    return a - b")            # a syntax error


def test_every_session_is_given_the_7k_budget(env):
    from agent.handoff import BUDGET
    root, L, m, task = env
    fm, _ = work(env, [good])
    assert BUDGET == 7168 and fm.budgets == [7168]


def test_a_session_stopped_by_the_budget_whose_work_checks_out_is_simply_complete(env):
    root, L, m, task = env
    lines = []
    fm, out = work(env, [runs_out(good)], say=lines.append)
    assert out["ok"] and out["summary"].startswith("verified") and m.tasks()[0].children == []
    assert "  attempt 1: used 7200 of 8192 tokens: wound down" in lines


def test_a_budget_stop_tells_the_next_session_to_work_in_smaller_pieces(env):
    root, L, m, task = env
    fm, out = work(env, [runs_out(lambda t: None), good])
    assert out["ok"] and "ran out of room" in fm.prompts[1] and "ran out of room" not in fm.prompts[0]


def test_when_attempts_run_out_after_a_budget_stop_the_rest_is_queued_with_a_handoff_note(env):
    root, L, m, task = env
    runs = []
    fm, out = work(env, [runs_out(buggy), runs_out(lambda t: None)], max_iterations=2, say=runs.append)
    assert out["ok"] and out["summary"].startswith("handed off: ran out of room (work kept)") and "queued as a new task" in out["summary"]
    kid = m.tasks()[0].children[0]
    assert (kid.id, kid.kind, kid.title, kid.status) == ("1.1", "code", "Continue: Add sub() to calc.py", "todo")
    note = m.handoff("1")
    assert "Task: Add sub() to calc.py" in note and "Done so far: calc.py, test_calc.py" in note and "Still wrong:" in note and "ran out of room" in note
    assert "def sub" in (root / "calc.py").read_text()                                                                          # the partial work was kept
    assert m.next_task().id == "1.1" and "  handed off: partial work kept; the rest is a new task" in runs
    assert [r["action"] for r in L.list() if r["action"] == "handoff"] == ["handoff"] and L.verify() == []


def test_partial_work_that_does_not_parse_is_rolled_back_not_handed_on(env):
    root, L, m, task = env
    before = snapshot(root)
    fm, out = work(env, [runs_out(broken_code), runs_out(lambda t: None)], max_iterations=2)
    without_notes = lambda snap: {k: v for k, v in snap.items() if k != "MEMORY.md"}
    assert out["ok"] and "work rolled back" in out["summary"] and without_notes(snapshot(root)) == without_notes(before)
    assert "Done so far: nothing kept" in m.handoff("1") and m.tasks()[0].children[0].title.startswith("Continue:")


def test_the_next_agent_gets_the_handoff_in_its_prompt_and_finishes_the_job(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    (root / "test_calc.py").write_text(TESTS)
    L = Ledger(root)
    prompts = []

    class Model(FakeFM):
        def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None, budget_tokens=None):
            t = {x.name: x.handler for x in tools}
            if "start_task" in t:
                t["start_task"]()
                return {"content": "", "stopped": False, "calls": []}
            if "add_actionable" in t:
                t["add_actionable"](title="Add sub() to calc.py", detail="with a test", kind="code")
                return {"content": "", "stopped": False, "calls": []}
            prompts.append(prompt)
            self.calls += 1
            if self.calls <= 3:                                   # the first agent: out of room each time (the stall rule ends it after 3 sessions)
                buggy(t) if self.calls == 1 else None
                return BUDGET_STOP
            fix(t)                                                # the next agent: a fresh session that finishes
            return {"content": "DONE", "stopped": False, "calls": []}

    model = Model([])                                                 # one model for the whole run: it remembers how many sessions it has had
    q = AgentQueue(L, task_runner(lambda ctx, task: research_and_work(ctx, task, model)))
    q.start("Add a subtract function")
    q.run()
    m = Memory(L)
    assert {r["id"]: (r["status"], r["error"]) for r in q.agents()} == {"root": ("done", None)} and m.next_task() is None
    kids = {t.id: t for t in m.tasks()[0].children}
    assert list(kids) == ["1.1", "1.2"] and [c.id for c in kids["1.1"].children] == ["1.1.1"]                  # the continuation hangs under the task
    assert "Handoff from the agent before you:" in prompts[-1] and "Task: Add sub() to calc.py" in prompts[-1]
    assert "Handoff from the agent before you" not in prompts[0] and (root / "calc.py").read_text().endswith("return a - b\n")
    assert "Handoffs" in (root / "MEMORY.md").read_text() and L.verify() == []


def research_and_work(ctx, task, fm):
    from agent.worker import solve_step
    return solve_step(fm)(ctx, task)


def test_a_continuation_of_a_continuation_is_the_limit(env):
    root, L, m, task = env
    m.add_plan("g", [Task("Continue: Continue: Add sub()", "d", kind="code")])
    deep = m.tasks()[1]
    fm = FakeFM([runs_out(buggy)])
    out = work_task(fm, L, m, deep, max_iterations=1)
    assert not out["ok"] and "edits rolled back" in out["summary"] and m.tasks()[1].children == [] and m.handoff("2") == ""


def test_a_task_that_is_not_in_memory_is_blocked_instead_of_crashing(env):
    root, L, m, task = env
    stray = Task("Add sub() to calc.py", "x", kind="code", id="9")
    out = work_task(FakeFM([runs_out(buggy)]), L, m, stray, max_iterations=1)
    assert not out["ok"] and "edits rolled back" in out["summary"] and "def sub" not in (root / "calc.py").read_text()


def test_hitting_apples_hard_limit_counts_as_running_out_of_room_too(env):
    root, L, m, task = env
    overflow = lambda t, p=None: (buggy(t), {"error": "context_overflow", "message": "too long", "calls": []})[1]
    fm, out = work(env, [overflow], max_iterations=1)
    assert out["ok"] and out["summary"].startswith("handed off")


def test_other_failures_still_roll_back_and_block_as_before(env):
    root, L, m, task = env
    before = snapshot(root)
    fm, out = work(env, [buggy], max_iterations=1)
    assert not out["ok"] and snapshot(root) == before and m.tasks()[0].children == []


# ------------------------------------------------------------------ giving up
def test_when_every_attempt_fails_the_edits_are_rolled_back_and_can_be_restored(env):
    root, L, m, task = env
    before = snapshot(root)
    fm, out = work(env, [buggy, lambda t, p=None: t["insert_after"](path="calc.py", anchor="def sub(a, b):", text="    # still wrong")], max_iterations=2)
    assert not out["ok"] and "not verified after 2 attempt(s)" in out["summary"] and "edits rolled back" in out["summary"]
    assert snapshot(root) == before and L.verify() == []
    L.redo()
    assert snapshot(root) != before                                            # nothing was destroyed: the failed work can be brought back


def test_repeating_the_same_failure_without_any_edit_stops_early(env):
    root, L, m, task = env
    (root / "test_calc.py").write_text(TESTS + "\n\ndef test_bad():\n    assert False\n")
    fm, out = work((root, Ledger(root), m, task), [idle], max_iterations=6)
    assert not out["ok"] and fm.calls == 2                         # the same failure twice in a row with no edit: it is not going anywhere


# ------------------------------------------------------------------ the model's own problems
def test_running_out_of_context_still_gets_the_work_verified(env):
    root, L, m, task = env
    fm, out = work(env, [lambda t, p: (good(t), {"error": "context_overflow", "message": "too long", "calls": []})[1]])
    assert out["ok"]


def test_after_a_context_overflow_the_next_prompt_drops_the_notes(env):
    root, L, m, task = env
    fm, _ = work(env, [lambda t, p: {"error": "context_overflow", "message": "x", "calls": []}, idle], max_iterations=2)
    assert "Notes:" in fm.prompts[0] and "Code in the project:" in fm.prompts[0]
    assert "Notes:" not in fm.prompts[1] and "Code in the project:" not in fm.prompts[1]


def test_a_guardrail_refusal_costs_an_attempt_but_does_not_end_the_task(env):
    root, L, m, task = env
    fm, out = work(env, [lambda t, p: {"error": "guardrail", "message": "blocked", "calls": []}, good])
    assert out["ok"] and fm.calls == 2


def test_the_evidence_the_model_was_given_is_recorded_in_the_ledger(env):
    root, L, m, task = env
    work(env, [idle], max_iterations=1)
    rows = [r for r in L.list() if r["action"] == "evidence"]
    assert len(rows) == 1 and rows[0]["task_id"] == "1" and "calc.py: def add(a, b)" in L.output(rows[0]["seq"])


def test_the_prompt_is_trimmed_until_it_fits_the_context_window(env):
    root, L, m, task = env
    full = FakeFM([idle], context=100_000)
    work_task(full, L, m, task, max_iterations=1)
    full_len = len(full.prompts[0])
    tight = FakeFM([idle], context=RESERVE + 150 + 260)       # tools 100 + instructions 50 are fixed in the fake counter
    work_task(tight, L, m, task, max_iterations=1)
    assert "Notes:" in full.prompts[0] and len(tight.prompts[0]) < full_len and "Notes:" not in tight.prompts[0] and "Task 1: Add sub()" in tight.prompts[0]
    assert INSTRUCTIONS.count("DONE") == 1


def test_acceptance_arguments_come_from_the_done_when_text_and_cannot_inject_commands(env):
    root, L, m, task = env
    (root / "test_other.py").write_text("def test_other():\n    assert True\n")
    L2 = Ledger(root)
    t = Task("Add sub()", "", "pytest test_calc.py::test_sub passes; rm -rf /", kind="code", id="9")
    work_task(FakeFM([good]), L2, m, t, max_iterations=1)
    cmds = [__import__("json").loads(r["args"])["cmd"] for r in L2.list() if r["action"] == "run_command"]
    assert all("test_calc.py::test_sub" in c and "rm -rf" not in c and "passes" not in c for c in cmds)


# ------------------------------------------------------------------ inside the agent queue
def test_a_goal_goes_through_research_work_and_verification_end_to_end(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    (root / "test_calc.py").write_text(TESTS)
    L = Ledger(root)

    def model(prompt, tools):
        t = {x.name: x.handler for x in tools}
        if "start_task" in t:
            t["start_task"]()
        elif "add_actionable" in t:
            t["list_files"]("*.py")
            t["add_actionable"](title="Add sub() to calc.py", detail="with a test in test_calc.py", kind="code", done_when="pytest test_calc.py")
        else:
            good(t)

    class Scripted(FakeFM):
        def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None, budget_tokens=None):
            self.prompts.append(prompt)
            model(prompt, tools)
            return {"content": "DONE", "stopped": False, "calls": []}

    q = AgentQueue(L, task_runner(solve_step(Scripted([]))))
    q.start("Add a subtract function")
    q.run()
    m = Memory(L)
    assert {r["id"]: r["status"] for r in q.agents()} == {"root": "done"} and m.next_task() is None
    assert m.get("Last task completed").startswith("1.2 Plan the rest of the goal")
    assert (root / "calc.py").read_text().endswith("return a - b\n") and L.verify() == []
    trail = [(r["action"], r["kind"], r["iteration"]) for r in L.trail("1.1") if r["action"] in ("verify", "list_files")]
    assert ("verify", "test", 1) in trail
    assert [r["path"] for r in L.edits("root", "1.1") if not r["path"].endswith("MEMORY.md")] == ["test_calc.py", "test_calc.py", "calc.py"]


def test_progress_is_reported_as_the_work_happens(env):
    root, L, m, task = env
    lines = []
    work_task(FakeFM([buggy, fix]), L, m, task, say=lines.append)
    assert len(lines) == 2 and lines[0].startswith("  attempt 1: rejected - pytest exited with code 1") and lines[1] == "  attempt 2: VERIFIED"
    assert all("\n" not in x and len(x) < 200 for x in lines)
    work_task(FakeFM([idle]), L, m, Task("Add sub()", "", kind="code", id="9"), max_iterations=1)       # saying nothing is allowed


def test_the_runner_announces_each_task_and_its_outcome(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    L = Ledger(root)
    lines = []
    q = AgentQueue(L, task_runner(lambda ctx, task: {"ok": task.kind == "research", "summary": "did it"}, say=lines.append))
    q.start("goal")
    q.run()
    assert lines == ["task 1 [research] Plan: goal", "  -> done: did it"]


def test_a_task_that_cannot_be_verified_blocks_the_agent_and_leaves_the_project_clean(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    (root / "test_calc.py").write_text(TESTS)
    L = Ledger(root)
    start = snapshot(root)

    class Scripted(FakeFM):
        def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None, budget_tokens=None):
            t = {x.name: x.handler for x in tools}
            if "start_task" in t:
                t["start_task"]()
                return {"content": "", "stopped": False, "calls": []}
            if "add_actionable" in t:
                t["add_actionable"](title="Add sub() to calc.py", kind="code")
            else:
                buggy(t) if self.calls == 0 else None
            self.calls += 1
            return {"content": "DONE", "stopped": False, "calls": []}

    q = AgentQueue(L, task_runner(solve_step(Scripted([]))))
    q.start("Add a subtract function")
    q.run()
    assert {r["id"]: r["status"] for r in q.agents()} == {"root": "blocked"}
    assert {k: v for k, v in snapshot(root).items() if k != "MEMORY.md"} == {k: v for k, v in start.items()}
    kid = Memory(L).tasks()[0].children[0]
    assert kid.status == "blocked"


# ---- real model (pytest -m fm): whatever it does, the harness must never certify work that is not real ----
@pytest.mark.fm
@pytest.mark.parametrize("attempt", range(2))
def test_real_model_is_never_certified_falsely(tmp_path, attempt):
    import subprocess
    import sys

    from agent.fm import FM
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(CALC)
    (root / "test_calc.py").write_text(TESTS)
    L = Ledger(root)
    start = snapshot(root)
    fm = FM()
    try:
        q = AgentQueue(L, task_runner(solve_step(fm)))
        q.start("Add a subtract function to calc.py, with a test, following how add is written and tested.")
        q.run()
    finally:
        fm.close()
    status = q.get("root")["status"]
    pytest_now = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True).stdout
    assert L.verify() == [] and not any(r["status"] == "pending" for r in L.list())
    assert "assert add(1, 2) == 3" in (root / "test_calc.py").read_text()               # the original test survived whatever happened
    if status == "done":                                                                # certified: then it must be real
        assert " passed" in pytest_now and "failed" not in pytest_now and int(pytest_now.split(" passed")[0].split()[-1]) >= 2
        assert [r["status"] for r in L.list() if r["action"] == "verify"][-1] == "ok"
    else:                                                                               # not certified: then nothing was left behind
        assert {k: v for k, v in snapshot(root).items() if k != "MEMORY.md"} == start


def test_a_retry_sees_the_files_as_they_are_now_including_the_ones_the_failure_names(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    L = Ledger(root)
    m = Memory(L)
    m.add_plan("g", [Task("Make words", "write words.py", kind="code")])
    wrong = lambda t, p=None: (t["create_file"](path="words.py", content="def words(t):\n    return t\n"),
                               t["create_file"](path="test_words.py", content="from words import words\n\n\ndef test_words():\n    assert words('a b') == ['a', 'b']\n"))
    fm = FakeFM([wrong, idle])
    work_task(fm, L, m, m.tasks()[0], max_iterations=2)
    assert "The project has no files yet." in fm.prompts[0]
    assert "The project has no files yet." not in fm.prompts[1] and "Contents of words.py:\ndef words(t):\n    return t" in fm.prompts[1]


def test_a_new_test_that_forgets_its_import_gets_the_import_in_the_next_prompt(env):
    root, L, m, task = env
    forgot = lambda t, p=None: (t["insert_after"](path="calc.py", anchor="return a + b", text="\n\ndef sub(a, b):\n    return a - b"),
                                t["create_file"](path="test_sub.py", content="def test_sub():\n    assert sub(5, 3) == 2\n"))
    fm, out = work(env, [forgot, idle])
    assert "NameError: name 'sub' is not defined" in fm.prompts[1] and "Hint: add `from calc import sub` at the top of the file that uses it" in fm.prompts[1]


def test_only_a_retry_is_sampled_at_a_different_temperature(env):
    from agent.worker import RETRY_TEMPERATURE
    fm, out = work(env, [buggy, idle, fix])
    assert out["ok"] and fm.temperatures == [None, RETRY_TEMPERATURE, RETRY_TEMPERATURE]


def test_a_retry_shows_a_whole_longer_file_that_the_failure_names(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    L = Ledger(root)
    m = Memory(L)
    m.add_plan("g", [Task("Make words", "write words.py", kind="code")])
    body = "def words(t):\n" + "".join(f"    # line {i} of a longer helper\n" for i in range(55)) + "    return t +\n"
    wrong = lambda t, p=None: t["create_file"](path="words.py", content=body)
    fm = FakeFM([wrong, idle])
    work_task(fm, L, m, m.tasks()[0], max_iterations=2)
    assert len(body) > 1800 and "# line 54 of a longer helper\n    return t +" in fm.prompts[1]
