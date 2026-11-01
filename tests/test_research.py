import pytest

from agent.ledger import ActionError, Ledger
from agent.memory import Memory, Task
from agent.research import MAX_STEPS, READ_TOOLS, research, research_step
from agent.tools import make_tools
from agent.web import Web
from tests.test_ledger import snapshot


class FakeFM:
    """Plays the model: `script(tools)` calls whichever tools it likes; returns the run result."""

    def __init__(self, script, result=None):
        self.script, self.result, self.runs = script, result or {"content": "", "stopped": False, "calls": []}, []

    def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None, budget_tokens=None):
        names = [t.name for t in tools]
        self.runs.append((prompt, names, max_tool_calls))
        self.script({t.name: t.handler for t in tools})
        return self.result


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    L = Ledger(root)
    m = Memory(L)
    m.add_plan("Add subtraction", [Task("Research: add subtraction", "Add a sub function to calc.py", "tests pass", kind="research")])
    return root, L, m


def web():
    return Web(search=lambda q: [{"title": "Docs", "url": "https://docs.example/x", "snippet": "about " + q}], fetch_page=lambda u: "page text for " + u)


def test_research_reads_the_codebase_then_records_actionables_under_the_task(env):
    root, L, m = env

    def script(t):
        t["list_files"]("*.py")
        t["grep"]("def add")
        t["read_file"](path="calc.py")
        t["add_actionable"](title="Write sub()", detail="in calc.py next to add", kind="code", done_when="pytest test_calc.py")
        t["add_actionable"](title="Check rounding rules", kind="research")   # there is no research kind: it becomes code

    r = research(FakeFM(script), L, m, "1", max_tool_calls=5)
    assert r == {"added": 2, "dropped": 0, "spawned": [], "error": None}
    kids = m.tasks()[0].children
    assert [(k.id, k.kind, k.title, k.done_when) for k in kids] == [("1.1", "code", "Write sub()", "pytest test_calc.py"), ("1.2", "code", "Check rounding rules", "")]
    assert m.next_task().id == "1.1" and "[code] Write sub()" in (root / "MEMORY.md").read_text()
    reads = [(r["action"], r["element"], r["task_id"], r["kind"], r["agent"]) for r in L.trail("1") if r["action"] in ("list_files", "grep", "read_file")]
    assert reads == [("list_files", "*.py", "1", "research", "root"), ("grep", "*", "1", "research", "root"), ("read_file", "calc.py", "1", "research", "root")]
    assert snapshot(root)["calc.py"] == b"def add(a, b):\n    return a + b\n"          # research changes no code


def test_the_research_prompt_names_the_task_and_offers_only_reading_tools(env):
    _, L, m = env
    fm = FakeFM(lambda t: t["add_actionable"](title="x"))
    research(fm, L, m, "1")
    prompt, names, calls = fm.runs[0]
    assert "Add subtraction" in prompt or "add subtraction" in prompt.lower()
    assert "Add a sub function to calc.py" in prompt and "tests pass" in prompt
    assert names == ["read_file", "list_files", "grep", "add_actionable"] and calls == 8
    assert not {"replace_text", "insert_after", "create_file", "delete_file", "run_command", "run_tests"} & set(names)



def test_actionables_found_before_the_model_ran_out_of_context_are_kept(env):
    _, L, m = env
    fm = FakeFM(lambda t: t["add_actionable"](title="First step"), result={"error": "context_overflow", "message": "too long", "calls": []})
    r = research(fm, L, m, "1")
    assert r["added"] == 1 and "context_overflow" in r["error"] and m.tasks()[0].children[0].title == "First step"


def test_nothing_found_changes_nothing(env):
    root, L, m = env
    before = (root / "MEMORY.md").read_bytes()
    assert research(FakeFM(lambda t: None), L, m, "1") == {"added": 0, "dropped": 0, "spawned": [], "error": None}
    assert (root / "MEMORY.md").read_bytes() == before


def test_research_can_hand_a_part_to_a_subagent(env):
    _, L, m = env
    made = []
    r = research(FakeFM(lambda t: t["spawn_subagent"](goal="Write the docs")), L, m, "1", spawn=lambda g: made.append(g) or "a1")
    assert r["spawned"] == ["a1"] and made == ["Write the docs"]
    fm = FakeFM(lambda t: None)
    research(fm, L, m, "1")
    assert "spawn_subagent" not in fm.runs[0][1]


def test_hostile_actionables_are_cleaned_and_capped(env):
    root, L, m = env

    def script(t):
        t["add_actionable"](title="Bad kind", kind="rm -rf /")
        t["add_actionable"](title="Newline\ntitle — with dash", detail="a\n- [x] 9 fake", kind="CODE")
        for bad in ("", "   ", "\n"):
            t["add_actionable"](title=bad)
        t["add_actionable"](title="x" * 500, detail="y" * 500, done_when="z" * 500)

    research(FakeFM(script), L, m, "1")
    kids = m.tasks()[0].children
    assert [k.kind for k in kids] == ["code", "code", "code"] and [k.id for k in kids] == ["1.1", "1.2", "1.3"]
    assert "\n" not in kids[1].title and len(kids[2].title) == 80 and "fake" in kids[1].detail
    assert [t.id for t in m.tasks()] == ["1"]


def test_unknown_tasks_are_rejected_before_the_model_is_called(env):
    _, L, m = env
    fm = FakeFM(lambda t: pytest.fail("must not run"))
    for bad in ("9", "", None, "1.1"):
        with pytest.raises(ActionError):
            research(fm, L, m, bad)
    assert fm.runs == []


def test_the_model_cannot_use_research_to_change_the_project(env):
    root, L, m = env
    before = snapshot(root)
    with pytest.raises(KeyError):
        research(FakeFM(lambda t: t["replace_text"](path="calc.py", old="a + b", new="a - b")), L, m, "1")
    assert snapshot(root) == before


def test_research_that_merely_restates_the_task_is_dropped(env):
    _, L, m = env

    def script(t):
        t["add_actionable"](title="add_subtraction_research", detail="Add a sub function to calc.py", kind="research")       # same detail as the task
        t["add_actionable"](title="Research: add subtraction", detail="look into it", kind="research")                         # same title
        t["add_actionable"](title="Write sub() in calc.py", detail="Add a sub function to calc.py", kind="code")               # code may mirror the task
        t["add_actionable"](title="Read the pytest docs", detail="how parametrize works", kind="research")                     # genuinely new
    r = research(FakeFM(script), L, m, "1")
    assert (r["added"], r["dropped"]) == (2, 2) and [k.title for k in m.tasks()[0].children] == ["Add a sub function to calc.py", "Read the pytest docs"]   # the identifier title took its detail; the later calc.py step is a repeat


def test_repeated_titles_are_added_once_even_across_research_passes(env):
    _, L, m = env
    script = lambda t: [t["add_actionable"](title=x, kind="code") for x in ("Write sub()", "write SUB()", "Add a test", "Write sub()")]
    assert research(FakeFM(script), L, m, "1")["added"] == 2
    assert research(FakeFM(script), L, m, "1") == {"added": 0, "dropped": 4, "spawned": [], "error": None}
    assert [k.title for k in m.tasks()[0].children] == ["Write sub()", "Add a test"]


def test_research_step_says_so_when_everything_was_a_restatement(env):
    _, L, m = env
    step = research_step(FakeFM(lambda t: t["add_actionable"](title="Research: add subtraction", kind="research")))
    out = step(Ctx(L, m), m.tasks()[0])
    assert out == {"ok": False, "summary": "planning gave nothing new; try a smaller, more specific goal"} and m.tasks()[0].children == []


def test_tool_names_and_identifiers_are_not_steps(env):
    _, L, m = env

    def script(t):
        for junk in ("web_search", "Web_Search", "read_file", "add_actionable", "guide_title", "add_subtract_research", "a_b"):
            t["add_actionable"](title=junk, kind="code")
        t["add_actionable"](title="Write sub() in calc.py", kind="code")
        t["add_actionable"](title="Search", kind="research")                     # an ordinary single word is fine
    r = research(FakeFM(script), L, m, "1")
    assert (r["added"], r["dropped"]) == (2, 7) and [k.title for k in m.tasks()[0].children] == ["Write sub() in calc.py", "Search"]


def test_a_step_titled_with_just_a_filename_becomes_a_sentence(env):
    _, L, m = env

    def script(t):
        t["add_actionable"](title="cactus.py", detail="the cactus class", kind="code")        # does not exist yet
        t["add_actionable"](title="calc.py", detail="subtraction", kind="code")               # exists
        t["add_actionable"](title="src/game/score.py", kind="code")
        t["add_actionable"](title="Write notes.md for the player", kind="code")               # already a sentence
        t["add_actionable"](title="v1.2", kind="code")                                        # looks like a filename: also made into a sentence
    research(FakeFM(script), L, m, "1")
    assert [k.title for k in m.tasks()[0].children] == ["Create cactus.py", "Update calc.py", "Create src/game/score.py", "Write notes.md for the player", "Create v1.2"]


def test_examples_become_the_steps_acceptance_and_unsafe_ones_are_dropped(env):
    _, L, m = env

    def script(t):
        t["add_actionable"](title="Write sub() in calc.py", detail="d", kind="code", examples="sub(5, 3) == 2; sub(0, 4) == -4; __import__('os') == 1")
        t["add_actionable"](title="Write mul() in mul.py", detail="d", kind="code", done_when="mul works", examples="not a comparison")
        t["add_actionable"](title="Write div() in div.py", detail="d", kind="code", done_when="pytest test_calc.py")
    research(FakeFM(script), L, m, "1")
    assert [k.done_when for k in m.tasks()[0].children] == ["sub(5, 3) == 2; sub(0, 4) == -4", "mul works", "pytest test_calc.py"]


def test_add_actionable_offers_an_examples_field(env):
    _, L, m = env
    fm = FakeFM(lambda t: None)
    research(fm, L, m, "1")
    assert fm.runs and "add_actionable" in fm.runs[0][1]


def test_the_goal_is_recorded_once_in_memory(tmp_path):
    from agent.agents import AgentQueue
    root = tmp_path / "ws"
    root.mkdir()
    L = Ledger(root)
    AgentQueue(L, lambda ctx: None).start("Build the thing")
    assert Memory(L).get("Goals") == "- Build the thing"


def test_the_prompt_lists_the_projects_files_but_never_the_agents_own_notes(env):
    root, L, m = env
    (root / ".agents" / "a1").mkdir(parents=True)
    (root / ".agents" / "a1" / "MEMORY.md").write_text("notes\n")
    fm = FakeFM(lambda t: None)
    research(fm, L, m, "1")
    assert "Code in the project:\n  calc.py: def add(a, b)" in fm.runs[0][0] and "Tests: test_calc.py" in fm.runs[0][0]
    assert "MEMORY.md" not in fm.runs[0][0].split("Project notes")[0]


def test_an_empty_project_is_said_to_be_empty_and_the_prompt_has_no_stray_filenames(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    L = Ledger(root)
    m = Memory(L)
    m.add_plan("Build a game", [Task("Research: build a game", "A terminal dinosaur game", kind="research")])
    fm = FakeFM(lambda t: None)
    research(fm, L, m, "1")
    prompt = fm.runs[0][0]
    assert "The project has no files yet." in prompt and "calc.py" not in prompt + __import__("agent.research").research.INSTRUCTIONS


def test_the_agents_notes_are_hidden_from_listing_and_searching(env):
    root, L, m = env
    t = {x.name: x.handler for x in make_tools(L)}
    (root / ".agents" / "a1").mkdir(parents=True)
    (root / ".agents" / "a1" / "MEMORY.md").write_text("secret plan\n")
    assert "MEMORY.md" not in t["list_files"]() and t["grep"]("plan|Add subtraction") == "(no matches)"
    assert t["read_file"](path="MEMORY.md")                                             # reading a named file is still allowed


# ---- the step used by agent runners ----
class Ctx:
    def __init__(self, L, m):
        self.ledger, self.memory = L, m

    def spawn(self, goal):
        return "a1"


def test_research_step_researches_research_tasks_and_blocks_code_tasks_without_a_worker(env):
    _, L, m = env
    step = research_step(FakeFM(lambda t: t["add_actionable"](title="Do it", kind="code")))
    out = step(Ctx(L, m), m.tasks()[0])
    assert out["ok"] and "1 steps" in out["summary"]
    code = m.next_task()
    assert code.kind == "code" and step(Ctx(L, m), code) == {"ok": False, "summary": "no worker to carry out code tasks yet"}
    done = research_step(FakeFM(lambda t: None), worker=lambda ctx, task: {"ok": True, "summary": "worked"})
    assert done(Ctx(L, m), code) == {"ok": True, "summary": "worked"}



# ---- real model (pytest -m fm) ----
@pytest.mark.fm
def test_real_model_researches_a_small_project_and_records_actionables(env):
    from agent.fm import FM
    root, L, m = env
    client = FM()
    try:
        r = research(client, L, m, "1")
    finally:
        client.close()
    trail = L.trail("1")
    assert r["error"] is None or "guardrail" in r["error"] or "overflow" in r["error"]
    assert {x["action"] for x in trail if x["element"] != "MEMORY.md"} <= {"list_files", "grep", "read_file", "web_search", "fetch_url"}
    assert any(x["action"] in ("list_files", "grep", "read_file") for x in trail) and all(x["kind"] == "research" for x in trail)
    assert snapshot(root)["calc.py"] == b"def add(a, b):\n    return a + b\n"


def test_a_goal_naming_existing_tests_is_worked_not_researched(env):
    root, L, m = env
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    m.add_plan("g", [Task("Make it pass", "make the tests in tests/test_x.py pass by creating x.py", kind="research")])
    seen = []
    out = research_step(FakeFM(lambda t: 1 / 0), worker=lambda ctx, task: seen.append(task.done_when) or {"ok": True, "summary": "ok"})(Ctx(L, m), m.tasks()[-1])
    assert out["ok"] and seen == ["pytest tests/test_x.py passes"]


def test_web_tools_are_never_offered_to_the_planner(env):
    _, L, m = env
    fm = FakeFM(lambda t: t["add_actionable"](title="Write sub() in calc.py"))
    research(fm, L, m, "1", web=web())
    assert fm.runs[0][1] == ["read_file", "list_files", "grep", "add_actionable"] and set(READ_TOOLS) == {"read_file", "list_files", "grep"}


def test_the_goal_is_planned_into_a_flat_list_and_every_step_is_worked_not_planned_again(env):
    _, L, m = env
    worked = []
    step = research_step(FakeFM(lambda t: [t["add_actionable"](title=x, kind=k) for x, k in (("Write sub() in calc.py", "code"), ("Write mul() in mul.py", "code"))]),
                         worker=lambda ctx, task: worked.append(task.id) or {"ok": True, "summary": "ok"})
    assert step(Ctx(L, m), m.tasks()[0]) == {"ok": True, "summary": "2 steps, 0 subagents"}
    step(Ctx(L, m), m.next_task())
    m.complete("1.1", "ok")
    step(Ctx(L, m), m.next_task())
    assert worked == ["1.1", "1.2"] and [(k.id, k.kind) for k in m.tasks()[0].children] == [("1.1", "code"), ("1.2", "code"), ("1.3", "research")]
    assert not any(k.children for k in m.tasks()[0].children)


def test_when_the_steps_are_done_the_goal_is_planned_again_flat_and_only_twice(env):
    _, L, m = env
    n = []
    plan = lambda t: t["add_actionable"](title=f"Write part{len(n)}.py", kind="code")
    fm = FakeFM(lambda t: (n.append(1), plan(t)))
    step = research_step(fm, worker=lambda ctx, task: {"ok": True, "summary": "ok"})
    for _ in range(12):
        task = m.next_task()
        if task is None:
            break
        out = step(Ctx(L, m), task)
        m.complete(task.id, out["summary"]) if out["ok"] else m.mark(task.id, "blocked")
    kids = m.tasks()[0].children
    assert [(k.id, k.kind) for k in kids] == [("1.1", "code"), ("1.2", "research"), ("1.3", "code"), ("1.4", "research"), ("1.5", "code")]
    assert not any(k.children for k in kids) and "Already done: Write part1.py" in fm.runs[1][0] and len(fm.runs) == 3


def test_planning_the_rest_with_nothing_new_just_finishes(env):
    _, L, m = env
    step = research_step(FakeFM(lambda t: t["add_actionable"](title="Write sub() in calc.py", kind="code")), worker=lambda ctx, task: {"ok": True, "summary": "ok"})
    step(Ctx(L, m), m.tasks()[0])
    m.complete("1.1", "ok")
    last = m.next_task()
    assert step(Ctx(L, m), last) == {"ok": True, "summary": "0 steps, 0 subagents"}
    m.complete(last.id, "ok")
    assert m.next_task() is None


def test_a_step_that_repeats_the_work_of_an_existing_one_is_dropped_whatever_its_wording(env):
    _, L, m = env
    first = lambda t: t["add_actionable"](title="Write slugify in slugs.py", kind="code")
    assert research(FakeFM(first), L, m, "1")["added"] == 1
    again = lambda t: [t["add_actionable"](title=x, detail=d, kind=k) for x, d, k in (
        ("Implement the slug function", "put it in slugs.py", "code"),            # same kind, same file: a repeat
        ("Write tests in tests/test_slugs.py", "for slugs.py", "test"),           # another kind and file: new
        ("Cover slugify in tests/test_slugs.py", "", "test"),                      # a repeat of the one above
        ("Write words.py", "", "code"))]                                           # a different file: new
    r = research(FakeFM(again), L, m, "1")
    assert (r["added"], r["dropped"]) == (2, 2) and [k.id for k in m.tasks()[0].children] == ["1.1", "1.2", "1.3"]


def test_a_goal_never_gets_more_than_max_steps_so_the_next_goal_is_reached(env):
    _, L, m = env
    script = lambda t: [t["add_actionable"](title=f"Write part{i}.py", kind="code") for i in range(MAX_STEPS + 8)]
    assert research(FakeFM(script), L, m, "1")["added"] == MAX_STEPS
    script2 = lambda t: [t["add_actionable"](title=f"Write other{i}.py", kind="code") for i in range(5)]
    assert research(FakeFM(script2), L, m, "1")["added"] == 0
    assert [k.id for k in m.tasks()[0].children][-1] == f"1.{MAX_STEPS}"


def test_an_identifier_title_is_replaced_by_the_steps_detail(env):
    _, L, m = env
    script = lambda t: [t["add_actionable"](title="write_slug", detail="write slugify(text) in slugs.py", kind="code"), t["add_actionable"](title="test_slug", kind="test")]
    assert research(FakeFM(script), L, m, "1")["added"] == 1 and [k.title for k in m.tasks()[0].children] == ["write slugify(text) in slugs.py"]


def test_a_step_naming_no_file_does_not_hide_later_steps_of_the_same_kind(env):
    _, L, m = env
    script = lambda t: [t["add_actionable"](title="Think about slugs", kind="code"), t["add_actionable"](title="Write slugify in slugs.py", kind="code")]
    assert research(FakeFM(script), L, m, "1")["added"] == 2


def test_a_step_that_only_researches_is_dropped_because_no_test_can_verify_it(env):
    _, L, m = env
    script = lambda t: [t["add_actionable"](title="Research slug rules", kind="code"), t["add_actionable"](title="Write slugify in slugs.py", kind="code")]
    r = research(FakeFM(script), L, m, "1")
    assert (r["added"], r["dropped"]) == (1, 1) and [k.title for k in m.tasks()[0].children] == ["Write slugify in slugs.py"]


def test_steps_that_research_or_test_no_file_are_dropped(env):
    _, L, m = env
    script = lambda t: [t["add_actionable"](title=a, detail=b, kind=k) for a, b, k in (
        ("inventory.py: research stock counts", "", "code"), ("tests/full_check: run everything", "", "test"),
        ("Write tests", "in tests/test_inventory.py", "test"), ("Create research.py", "", "code"))]
    assert research(FakeFM(script), L, m, "1")["added"] == 2 and [k.title for k in m.tasks()[0].children] == ["Write tests", "Create research.py"]


def test_a_test_step_right_after_a_code_step_joins_it_as_one_unit_of_work(env):
    _, L, m = env
    script = lambda t: [t["add_actionable"](title=a, kind=k) for a, k in (("Write slugify in slugs.py", "code"), ("Write tests in tests/test_slugs.py", "test"),
                                                                           ("Write word_count in words.py", "code"), ("Write tests in tests/test_words.py", "test"))]
    assert research(FakeFM(script), L, m, "1")["added"] == 2
    assert [(k.kind, k.detail) for k in m.tasks()[0].children] == [("code", "Also Write tests in tests/test_slugs.py"), ("code", "Also Write tests in tests/test_words.py")]
    script2 = lambda t: t["add_actionable"](title="Write tests in tests/test_extra.py", kind="test")                  # tests for code that exists stay a test step
    assert research(FakeFM(script2), L, m, "1")["added"] == 1 and m.tasks()[0].children[-1].kind == "test"
