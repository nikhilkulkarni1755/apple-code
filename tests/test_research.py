import pytest

from agent.ledger import ActionError, Ledger
from agent.memory import Memory, Task
from agent.research import MAX_DEPTH, READ_TOOLS, research, research_step
from agent.tools import make_tools
from agent.web import Web
from tests.test_ledger import snapshot


class FakeFM:
    """Plays the model: `script(tools)` calls whichever tools it likes; returns the run result."""

    def __init__(self, script, result=None):
        self.script, self.result, self.runs = script, result or {"content": "", "stopped": False, "calls": []}, []

    def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None):
        names = [t.name for t in tools]
        if "start_task" in names:                      # the router asks first: this goal is small, research it directly
            {t.name: t.handler for t in tools}["start_task"]()
            return {"content": "", "stopped": False, "calls": []}
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
        t["add_actionable"](title="Check rounding rules", kind="research")

    r = research(FakeFM(script), L, m, "1", max_tool_calls=5)
    assert r == {"added": 2, "dropped": 0, "spawned": [], "error": None}
    kids = m.tasks()[0].children
    assert [(k.id, k.kind, k.title, k.done_when) for k in kids] == [("1.1", "code", "Write sub()", "pytest test_calc.py"), ("1.2", "research", "Check rounding rules", "")]
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


def test_web_tools_appear_only_when_a_backend_is_given_and_are_logged_as_research(env):
    _, L, m = env
    fm = FakeFM(lambda t: (t["web_search"]("python subtraction"), t["fetch_url"]("https://docs.example/x"), t["add_actionable"](title="Use docs")))
    research(fm, L, m, "1", web=web())
    assert fm.runs[0][1] == ["read_file", "list_files", "grep", "web_search", "fetch_url", "add_actionable"]
    got = [(r["action"], r["element"], r["kind"], r["task_id"]) for r in L.list() if r["action"] in ("web_search", "fetch_url")]
    assert got == [("web_search", "python subtraction", "research", "1"), ("fetch_url", "https://docs.example/x", "research", "1")]
    assert L.output([r["seq"] for r in L.list() if r["action"] == "fetch_url"][0]) == "page text for https://docs.example/x"
    assert set(READ_TOOLS) >= {"web_search", "fetch_url"}


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
    assert (r["added"], r["dropped"]) == (2, 2) and [k.title for k in m.tasks()[0].children] == ["Write sub() in calc.py", "Read the pytest docs"]


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
    assert out == {"ok": False, "summary": "research gave nothing new; try a smaller, more specific goal"} and m.tasks()[0].children == []


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
    assert out["ok"] and "1 actionables" in out["summary"]
    code = m.next_task()
    assert code.kind == "code" and step(Ctx(L, m), code) == {"ok": False, "summary": "no worker to carry out code tasks yet"}
    done = research_step(FakeFM(lambda t: None), worker=lambda ctx, task: {"ok": True, "summary": "worked"})
    assert done(Ctx(L, m), code) == {"ok": True, "summary": "worked"}


def test_research_step_stops_researching_when_nothing_is_found_or_it_goes_too_deep(env):
    _, L, m = env
    assert research_step(FakeFM(lambda t: None))(Ctx(L, m), m.tasks()[0])["ok"] is False
    deep = Task("deep", id="1" + ".1" * MAX_DEPTH, kind="research")
    out = research_step(FakeFM(lambda t: pytest.fail("too deep to research")))(Ctx(L, m), deep)
    assert out["ok"] is False and "limit" in out["summary"]


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


class Router(FakeFM):
    """A model that decides the goal is big, splits it into parts when asked, and researches each part into steps."""

    def __init__(self, parts):
        super().__init__(lambda t: None)
        self.parts, self.sessions = parts, []

    def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None):
        t = {x.name: x.handler for x in tools}
        self.sessions.append(sorted(t))
        if "decompose_task" in t:
            t["decompose_task"]()
        elif "add_task" in t:
            for title in self.parts:
                t["add_task"](title=title, detail="d", done_when="tests pass", big=False)
        elif "add_actionable" in t:
            t["add_actionable"](title="Write the code for " + prompt.split(".")[0][-20:], kind="code")
        return {"content": "", "stopped": False, "calls": []}


def test_a_big_goal_is_split_into_parts_before_anything_is_researched(env):
    root, L, m = env
    fm = Router(["Game state and collision", "Scoring", "Keyboard and screen"])
    out = research_step(fm)(Ctx(L, m), m.tasks()[0])
    assert out == {"ok": True, "summary": "split into 3 parts"}
    kids = m.tasks()[0].children
    assert [(k.id, k.kind, k.title) for k in kids] == [("1.1", "research", "Game state and collision"), ("1.2", "research", "Scoring"), ("1.3", "research", "Keyboard and screen")]
    assert m.next_task().id == "1.1" and sorted(fm.sessions[0]) == ["decompose_task", "start_task"] and "add_task" in fm.sessions[1]


def test_each_part_is_then_researched_into_code_steps(env):
    root, L, m = env
    fm = Router(["Scoring", "Restart"])
    step = research_step(fm)
    step(Ctx(L, m), m.tasks()[0])
    out = step(Ctx(L, m), m.next_task())                                         # task 1.1, not top level: researched, not split again
    assert out["ok"] and [k.kind for k in m.tasks()[0].children[0].children] == ["code"] and "decompose_task" not in sorted(fm.sessions[-1])
    assert m.next_task().kind == "code" and m.next_task().id == "1.1.1"


def test_a_small_goal_is_researched_directly_and_never_split(env):
    _, L, m = env
    fm = FakeFM(lambda t: t["add_actionable"](title="Write sub() in calc.py", kind="code"))
    assert research_step(fm)(Ctx(L, m), m.tasks()[0])["ok"] and [k.title for k in m.tasks()[0].children] == ["Write sub() in calc.py"]


def test_if_splitting_fails_the_goal_is_researched_instead(env):
    _, L, m = env

    class Broken(Router):
        def run(self, prompt, tools, instructions="", max_tool_calls=8, temperature=None):
            names = [x.name for x in tools]
            if "add_task" in names:
                return {"error": "guardrail", "message": "blocked", "calls": []}           # the splitter fails
            return super().run(prompt, tools, instructions, max_tool_calls, temperature)

    out = research_step(Broken(["x"]))(Ctx(L, m), m.tasks()[0])
    assert out["ok"] and m.tasks()[0].children[0].kind == "code"                           # fell back to ordinary research
