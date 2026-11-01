"""Research a task before doing it: look through the codebase (and the web), then record what must be done as actionables.
Every read the model makes is a ledger record (action, element) attributed to this task, so the research is accountable."""
import difflib
import re

from .fm import Tool
from .ledger import ActionError
from .memory import KINDS, Task
from .planner import FMBackend, Planner, route
from .tools import _p, make_tools, visible_files

READ_TOOLS = ("read_file", "list_files", "grep", "web_search", "fetch_url")
INSTRUCTIONS = ("You prepare work for a coding agent. Look at the project with the tools only if it has files, and search the web only if "
                "you need outside information. Then call add_actionable once for each small step that must be done, in order (3 to 8 steps). "
                "Write each title as a short imperative phrase that names the file it changes. Use kind=code whenever the change can be made "
                "now; use kind=research only for something the project files cannot tell you. Steps are things to build, not tools to call.")
_TOOL_NAMES = {*READ_TOOLS, "add_actionable", "spawn_subagent", "run_tests", "run_command", "replace_text", "insert_after", "create_file", "delete_file"}
MAX_DEPTH = 3          # a research task nested deeper than this (id 1.1.1.1) is not researched further
SAME = 0.7             # how alike two texts must be for one to count as a restatement of the other
MAX_TASKS = 200


def _junk_title(title):
    """Small models sometimes answer with a tool's name ('web_search') or an identifier ('guide_title') instead of a step."""
    return title.casefold() in _TOOL_NAMES or bool(re.fullmatch(r"[A-Za-z0-9]+(_[A-Za-z0-9]+)+", title))


_FILENAME = re.compile(r"[\w./-]+\.\w{1,5}")


def _tidy_title(title, files):
    """A step titled just 'cactus.py' says nothing: make it 'Create cactus.py' (or 'Update' if the file exists)."""
    return f"{'Update' if title in files else 'Create'} {title}" if _FILENAME.fullmatch(title) else title


def _restates(item, task):
    """Research that only repeats the task it came from makes no progress; small models do this a lot."""
    like = lambda a, b: difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio() >= SAME
    return item.kind == "research" and (like(item.title, task.title) or like(item.detail or item.title, task.detail or task.title))


def research(fm, ledger, memory, task_id, web=None, spawn=None, max_tool_calls=8):
    """Research the task `task_id` from MEMORY.md and add the actionables the model finds as its subtasks.
    `spawn(goal)`, if given, lets the model hand a self-contained part to a subagent.
    Returns {'added': n, 'dropped': n restating the task, repeating a title, or naming a tool, 'spawned': [ids], 'error': str or None}."""
    task = next((t for t in _walk(memory.tasks()) if t.id == task_id), None)
    if task is None:
        raise ActionError(f"no task {task_id!r}")
    found, spawned = [], []

    def add_actionable(title, detail="", kind="code", done_when=""):
        found.append(Task(title, detail, done_when, kind=kind if kind in KINDS else "code"))
        return f"actionable {len(found)} added"

    tools = [t for t in make_tools(ledger, web) if t.name in READ_TOOLS]
    tools.append(Tool("add_actionable", "Add one step that must be done. kind is 'code' to change something or 'research' if more must be looked up first.",
                      [_p("title", "string", "short name"), _p("detail", "string", "what to do, naming the files involved", True),
                       _p("kind", "string", "code or research", True), _p("done_when", "string", "how to tell it is done, ideally a command", True)],
                      add_actionable))
    if spawn is not None:
        tools.append(Tool("spawn_subagent", "Hand a large, self-contained part of this work to a new subagent with its own memory.",
                          [_p("goal", "string", "what the subagent should accomplish")], lambda goal: f"spawned {spawned.append(spawn(goal)) or spawned[-1]}"))
    files = visible_files(ledger)
    prompt = (f"Task: {task.title}. {task.detail}\n" + (f"Done when: {task.done_when}\n" if task.done_when else "")
              + ("Files in the project: " + ", ".join(files[:25]) if files else "The project has no files yet.")
              + f"\nProject notes:\n{memory.context(1200)}\nThen call add_actionable for each step.")
    with ledger.scope(task=task_id, kind="research"):
        r = fm.run(prompt, tools, INSTRUCTIONS, max_tool_calls=max_tool_calls)
        seen = {c.title.casefold() for c in task.children}
        kept = []
        for t in found:                     # no restatements of the task, and each title only once
            t.title = _tidy_title(t.title, files)
            if not _restates(t, task) and not _junk_title(t.title) and t.title.casefold() not in seen:
                kept.append(t)
                seen.add(t.title.casefold())
        if kept:
            memory.add_children(task_id, kept)   # kept even if the model later ran out of context
    error = f"{r['error']}: {r['message']}" if "error" in r else None
    return {"added": len(kept), "dropped": len(found) - len(kept), "spawned": spawned, "error": error}


def _walk(tasks):
    for t in tasks:
        yield t
        yield from _walk(t.children)


def research_step(fm, web=None, worker=None):
    """A step for agents.task_runner: research tasks are researched; code tasks go to `worker(ctx, task)`
    (the worker loop is not built yet, so without one a code task is reported blocked rather than pretended done)."""

    def step(ctx, task):
        if task.kind == "code":
            return worker(ctx, task) if worker else {"ok": False, "summary": "no worker to carry out code tasks yet"}
        if task.id.count(".") >= MAX_DEPTH or len(list(_walk(ctx.memory.tasks()))) >= MAX_TASKS:
            return {"ok": False, "summary": "research limit reached (too deep or too many tasks)"}
        if "." not in task.id and not task.children:   # a whole goal: if it is big, split it into parts before researching each part
            split = route(fm, task.detail or task.title, Planner(ctx.memory, FMBackend(fm), parent=task.id))
            if split["route"] == "decompose" and split["tasks"]:
                return {"ok": True, "summary": f"split into {len(split['tasks'])} parts"}
        r = research(fm, ctx.ledger, ctx.memory, task.id, web, ctx.spawn)
        if r["added"] == 0 and not r["spawned"]:
            return {"ok": False, "summary": r["error"] or ("research gave nothing new; try a smaller, more specific goal" if r["dropped"] else "research found nothing to do")}
        return {"ok": True, "summary": f"{r['added']} actionables, {len(r['spawned'])} subagents" + (f" ({r['error']})" if r["error"] else "")}

    return step
