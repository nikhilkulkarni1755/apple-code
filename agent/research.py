"""Research a task before doing it: look through the codebase (and the web), then record what must be done as actionables.
Every read the model makes is a ledger record (action, element) attributed to this task, so the research is accountable."""
import difflib

from .fm import Tool
from .ledger import ActionError
from .memory import KINDS, Task
from .tools import _p, make_tools

READ_TOOLS = ("read_file", "list_files", "grep", "web_search", "fetch_url")
INSTRUCTIONS = ("You prepare work for a coding agent. First look at the project with the tools, and search the web if you need outside "
                "information. Then call add_actionable once for each step that must be done, in order. Write each title as a short "
                "imperative phrase naming the file, like 'Add sub() to calc.py'. Use kind=code whenever the change can be made now; "
                "use kind=research only for something the project files cannot tell you.")
MAX_DEPTH = 3          # a research task nested deeper than this (id 1.1.1.1) is not researched further
SAME = 0.7             # how alike two texts must be for one to count as a restatement of the other
MAX_TASKS = 200


def _restates(item, task):
    """Research that only repeats the task it came from makes no progress; small models do this a lot."""
    like = lambda a, b: difflib.SequenceMatcher(None, a.casefold(), b.casefold()).ratio() >= SAME
    return item.kind == "research" and (like(item.title, task.title) or like(item.detail or item.title, task.detail or task.title))


def research(fm, ledger, memory, task_id, web=None, spawn=None, max_tool_calls=8):
    """Research the task `task_id` from MEMORY.md and add the actionables the model finds as its subtasks.
    `spawn(goal)`, if given, lets the model hand a self-contained part to a subagent.
    Returns {'added': n, 'dropped': n restating the task or repeating a title, 'spawned': [ids], 'error': str or None}."""
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
    prompt = (f"Task: {task.title}. {task.detail}\n" + (f"Done when: {task.done_when}\n" if task.done_when else "")
              + f"Project notes:\n{memory.context(1200)}\n"
                "Look at the project (list_files, grep, read_file), then call add_actionable for each step.")
    with ledger.scope(task=task_id, kind="research"):
        r = fm.run(prompt, tools, INSTRUCTIONS, max_tool_calls=max_tool_calls)
        seen = {c.title.casefold() for c in task.children}
        kept = []
        for t in found:                     # no restatements of the task, and each title only once
            if not _restates(t, task) and t.title.casefold() not in seen:
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
        r = research(fm, ctx.ledger, ctx.memory, task.id, web, ctx.spawn)
        if r["added"] == 0 and not r["spawned"]:
            return {"ok": False, "summary": r["error"] or ("research only restated the task" if r["dropped"] else "research found nothing to do")}
        return {"ok": True, "summary": f"{r['added']} actionables, {len(r['spawned'])} subagents" + (f" ({r['error']})" if r["error"] else "")}

    return step
