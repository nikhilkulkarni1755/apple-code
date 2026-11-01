"""Plan a goal: look through the codebase, then record what must be done as a flat list of code and test steps (1.1, 1.2, ...).
There are no research steps: iteration (write code, run tests, repeat) is the work. Every read the model makes is a ledger record."""
import re
from pathlib import Path

from .fm import Tool
from .ledger import ActionError
from .memory import Task
from .evidence import gather
from .handoff import BUDGET
from .spec import join_examples, parse_examples
from .tools import _p, make_tools, visible_files
from .verify import is_test_path

READ_TOOLS = ("read_file", "list_files", "grep")   # no web: research is not part of the loop
INSTRUCTIONS = ("You prepare work for a coding agent. Look at the project with the tools only if it has files. Then call add_actionable once for each "
                "small step that must be done, in order (3 to 8 steps). Write each title as a short imperative phrase that names the file it changes. "
                "Use kind=code to write or change code, kind=test to write tests for it. Plan no step that only researches or reads: every step writes code or tests. Add only steps the goal still needs; if all it asks for is done, add none. Do not repeat a step already listed under Next tasks. "
                "Steps are things to build, not tools to call.")
_TOOL_NAMES = {*READ_TOOLS, "add_actionable", "spawn_subagent", "run_tests", "run_command", "replace_text", "insert_after", "create_file", "delete_file"}
MAX_REPLANS = 2        # times a goal is planned again, after its steps are done, to add what the goal still needs
MAX_STEPS = 12         # steps one goal may have: the list grows 1.1, 1.2, ... and stops, so it never crowds out the next goal
MAX_TASKS = 200


def _junk_title(title):
    """Small models sometimes answer with a tool's name ('web_search') or an identifier ('guide_title') instead of a step."""
    return title.casefold() in _TOOL_NAMES or bool(re.fullmatch(r"[A-Za-z0-9]+(_[A-Za-z0-9]+)+", title))


_FILENAME = re.compile(r"[\w./-]+\.\w{1,5}")


def _tidy_title(title, files):
    """A step titled just 'cactus.py' says nothing: make it 'Create cactus.py' (or 'Update' if the file exists)."""
    return f"{'Update' if title in files else 'Create'} {title}" if _FILENAME.fullmatch(title) else title


_FILE_TYPES = {".py", ".md", ".txt", ".json", ".toml", ".cfg", ".yml", ".yaml", ".sh", ".js", ".ts", ".html", ".css"}


def _work(item):
    """What a step is about: its kind and the files it names."""
    return item.kind, frozenset(f for f in _FILENAME.findall(f"{item.title} {item.detail}") if Path(f).suffix in _FILE_TYPES)


def _only_looks(item):
    """A step that researches ("research x", "slugs.py: research x") or names no file ("finalize both rounds"): nothing a test can verify."""
    return bool(re.match(r"(?:[\w./-]+:\s*)?research\b", item.title, re.I)) or not _work(item)[1]


def _modules(item):
    """The modules a step is about: slugs.py and tests/test_slugs.py are both 'slugs'."""
    return {Path(f).stem.removeprefix("test_") for f in _work(item)[1] if f.endswith(".py")}


def _spec(goal, item):
    """The sentences of the goal that name the step's files: the exact wording the code has to meet (the model's own paraphrase drops it)."""
    names = {Path(f).name for f in _work(item)[1]}
    return " ".join(x for x in re.split(r"(?<=[.;])\s+", goal or "") if any(n in x for n in names))[:700]


def _repeats(item, done):
    kind, files = _work(item)
    return bool(files) and any(k == kind and f and (files <= f or f <= files) for k, f in done)   # the same files, or some of them, for the same kind of work


def research(fm, ledger, memory, task_id, web=None, spawn=None, max_tool_calls=8):
    """Research the task `task_id` from MEMORY.md and add the actionables the model finds as its subtasks.
    `spawn(goal)`, if given, lets the model hand a self-contained part to a subagent.
    Returns {'added': n, 'dropped': n restating the task, repeating a title, or naming a tool, 'spawned': [ids], 'error': str or None}."""
    task = next((t for t in _walk(memory.tasks()) if t.id == task_id), None)
    if task is None:
        raise ActionError(f"no task {task_id!r}")
    found, spawned = [], []

    def add_actionable(title, detail="", kind="code", done_when="", examples=""):
        shown = join_examples(parse_examples(examples))   # examples that pass as safe, whole comparisons become the step's acceptance
        found.append(Task(title, detail, shown or done_when, kind=kind if kind in ("code", "test") else "code"))
        return f"actionable {len(found)} added"

    tools = [t for t in make_tools(ledger, web) if t.name in READ_TOOLS]
    tools.append(Tool("add_actionable", "Add one step that must be done. kind is 'code' to change something or 'test' to write tests for it.",
                      [_p("title", "string", "short name"), _p("detail", "string", "what to do, naming the files involved", True),
                       _p("kind", "string", "code or test", True), _p("done_when", "string", "how to tell it is done", True),
                       _p("examples", "string", "calls with their expected results as Python comparisons, separated by ';'", True)],
                      add_actionable))
    if spawn is not None:
        tools.append(Tool("spawn_subagent", "Hand a large, self-contained part of this work to a new subagent with its own memory.",
                          [_p("goal", "string", "what the subagent should accomplish")], lambda goal: f"spawned {spawned.append(spawn(goal)) or spawned[-1]}"))
    files = visible_files(ledger)
    finished = "; ".join(c.title for c in _walk(memory.tasks()) if c.status == "done" and not c.children and c.kind != "research")[:600]
    detail = "The whole request is under Goals." if f"- {task.detail}" in memory.get("Goals") else task.detail   # a goal is shown once
    prompt = (f"Task: {task.title}. {detail}\n" + (f"Done when: {task.done_when}\n" if task.done_when else "")
              + gather(ledger, f"{task.title} {task.detail}", budget=1200)
              + f"\nProject notes:\n{memory.context(1200)}"
              + (f"\nAlready done: {finished}" if finished else "") + "\nThen call add_actionable for each step.")
    with ledger.scope(task=task_id, kind="research"):
        r = fm.run(prompt, tools, INSTRUCTIONS, max_tool_calls=max_tool_calls, budget_tokens=BUDGET)
        ledger.log_session(r)
        seen = {c.title.casefold() for c in _walk(memory.tasks())}
        done = [w for w in map(_work, _walk(memory.tasks())) if w[1]]
        covered = {m for c in _walk(memory.tasks()) if c.kind == "code" for m in _modules(c)}   # modules whose code step already includes their tests
        kept = []
        for t in found:                     # each title once in the whole plan, no kind of work on the same files twice in the whole plan
            t.title = t.detail if _junk_title(t.title) and t.detail else _tidy_title(t.title, files)   # 'write_slug' says nothing, its detail does
            if not _junk_title(t.title) and not _only_looks(t) and not (t.kind == "test" and _modules(t) & covered) and t.title.casefold() not in seen and not _repeats(t, done):
                seen.add(t.title.casefold())
                done.append(_work(t))
                mate = next((k for k in kept if {k.kind, t.kind} == {"code", "test"} and _modules(k) & _modules(t)), None)
                if mate:   # a code step and the tests for the same module are one unit of work for one session, in whichever order they were listed
                    code, test = (mate, t) if mate.kind == "code" else (t, mate)
                    code.detail = (code.detail + ". " if code.detail else "") + f"Also {test.title}"
                    kept[kept.index(mate)] = code
                    continue
                kept.append(t)
        kept = kept[:max(0, MAX_STEPS - len(task.children))]
        for t in kept:
            if spec := _spec(task.detail, t):
                t.detail = (t.detail + ". " if t.detail else "") + f"Spec: {spec}"
        steps_found = len(kept)
        if kept:
            memory.add_children(task_id, kept)   # kept even if the model later ran out of context
    error = f"{r['error']}: {r['message']}" if "error" in r else None
    return {"added": steps_found, "dropped": len(found) - steps_found, "spawned": spawned, "error": error}


def _walk(tasks):
    for t in tasks:
        yield t
        yield from _walk(t.children)


def research_step(fm, web=None, worker=None):
    """A step for agents.task_runner: the goal itself is planned into steps, and so is its "plan the rest" step; every code and test step
    goes to `worker(ctx, task)` (without one it is reported blocked rather than pretended done)."""

    def step(ctx, task):
        if task.kind in ("code", "test"):
            return worker(ctx, task) if worker else {"ok": False, "summary": "no worker to carry out code tasks yet"}
        if len(list(_walk(ctx.memory.tasks()))) >= MAX_TASKS:
            return {"ok": False, "summary": "too many tasks"}
        named = next((f for f in _FILENAME.findall(task.detail or task.title) if is_test_path(f) and f in visible_files(ctx.ledger)), None)
        if named and worker and "." not in task.id:   # the goal names tests that already exist: there is nothing to plan, make them pass
            task.done_when = f"pytest {named} passes"
            ctx.memory.set_done_when(task.id, task.done_when)
            return worker(ctx, task)
        root = task.id.split(".")[0]   # "plan the rest" steps plan under the goal, so the list stays flat
        r = research(fm, ctx.ledger, ctx.memory, root, web, ctx.spawn)
        goal = next(t for t in ctx.memory.tasks() if t.id == root)
        if r["added"] and sum(c.kind == "research" for c in goal.children) < MAX_REPLANS:   # when these steps are done, look again at what is left
            ctx.memory.add_children(root, [Task("Plan the rest of the goal", "Add the steps the goal still needs; none if it is all done.", kind="research")])
        if r["added"] == 0 and not r["spawned"] and task.id == root:
            return {"ok": False, "summary": r["error"] or ("planning gave nothing new; try a smaller, more specific goal" if r["dropped"] else "planning found nothing to do")}
        return {"ok": True, "summary": f"{r['added']} steps, {len(r['spawned'])} subagents" + (f" ({r['error']})" if r["error"] else "")}

    return step
