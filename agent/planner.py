"""Splits a big goal into many small tasks and stores the plan in MEMORY.md, so later sessions inherit it."""
from .fm import Tool
from .ledger import ActionError
from .memory import Task, clean

MAX_GOAL = 4000
MAX_PER_SPLIT = 12
INSTRUCTIONS = "You plan software work. Call add_task once per task, in the order the tasks should be done."


class PlanError(ActionError):
    pass


def _p(name, type_, description, optional=False):
    return {"name": name, "type": type_, "description": description, "optional": optional}


class FMBackend:
    """Asks the on-device model to split a goal; its add_task tool calls are the structured output."""

    def __init__(self, fm):
        self.fm = fm

    def split(self, goal, context):
        found = []

        def add_task(title, detail="", done_when="", big=False):
            found.append({"title": title, "detail": detail, "done_when": done_when, "big": big})
            return f"task {len(found)} added"

        tool = Tool("add_task", "Add one task to the plan. Set big=true if the task is still too large and needs splitting further.",
                    [_p("title", "string", "short task name"), _p("detail", "string", "what to do", True),
                     _p("done_when", "string", "how to tell it is done, e.g. a command or a visible result", True),
                     _p("big", "boolean", "true if it still needs splitting", True)], add_task)
        prompt = (f"Request: {goal}\n" + (f"Context: {context}\n" if context else "")
                  + "Split this request into 3 to 10 small, concrete tasks, in order. A task is small if it can be finished in one sitting "
                    "by editing a few files; otherwise set big=true. Call add_task for each task.")
        r = self.fm.run(prompt, [tool], INSTRUCTIONS, max_tool_calls=MAX_PER_SPLIT + 2)
        if "error" in r:
            raise PlanError(f"model error ({r['error']}): {r['message']}")
        return found


class Planner:
    def __init__(self, memory, backend, max_depth=3, max_tasks=200):
        self.memory, self.backend, self.max_depth, self.max_tasks = memory, backend, max_depth, max_tasks

    def decompose(self, goal):
        """Split `goal` recursively, save the plan to MEMORY.md, and return the top-level Task objects (numbered).
        A title is used only once in the whole plan: small models repeat themselves across levels."""
        if not isinstance(goal, str) or not goal.strip():
            raise PlanError("goal must be a non-empty string")
        goal = " ".join(goal.split())[:MAX_GOAL]
        budget, seen = [self.max_tasks], set()
        tasks = self._split(goal, "", 1, budget, seen, top=True)
        if not tasks:
            raise PlanError("the planner produced no tasks")
        self.memory.add_plan(goal, tasks)
        return tasks

    def _split(self, goal, context, depth, budget, seen, top=False):
        try:
            raw = self.backend.split(goal, context)
        except Exception as e:
            if top:
                raise PlanError(f"planner backend failed: {e}") from e
            return []   # a failed sub-split leaves that task as a leaf
        if not isinstance(raw, list):
            return []
        out, big = [], []
        for item in raw[:100]:                      # bounded even if a backend returns millions of entries
            if len(out) >= MAX_PER_SPLIT or budget[0] <= 0:
                break
            if not isinstance(item, dict):
                continue
            field = lambda k, n: clean(item[k], n) if isinstance(item.get(k), str) else ""
            title = field("title", 80)
            if not title or title.casefold() in seen:
                continue
            seen.add(title.casefold())
            budget[0] -= 1
            out.append(Task(title, field("detail", 200), field("done_when", 200)))
            if item.get("big") is True and depth < self.max_depth:
                big.append(out[-1])
        for task in big:                            # siblings claim their titles before anything is nested under them
            task.children = self._split(f"{task.title}. {task.detail}", f"part of: {goal}"[:500], depth + 1, budget, seen)
        return out


def route(fm, goal, planner):
    """Show the model two tools and let it pick: decompose_task for big requests, start_task for small ones.
    Returns {'route': 'decompose'|'direct'|'none', 'tasks': [...] or None, 'error': str or None}."""
    result = {"route": "none", "tasks": None, "error": None}

    def decompose_task(goal_text=""):
        result["route"] = "decompose"
        try:
            result["tasks"] = planner.decompose(goal)   # always the user's own words, not the model's paraphrase
        except ActionError as e:
            result["error"] = str(e)
            return f"error: {e}"
        return f"plan saved to MEMORY.md with {len(result['tasks'])} top-level tasks"

    def start_task(task_text=""):
        result["route"] = "direct"
        return "ok"

    tools = [Tool("decompose_task", "Use this FIRST for any large request that needs many files, components or steps, such as building an "
                  "application, a system or a library. It splits the request into small tasks and saves the plan.",
                  [_p("goal_text", "string", "the request", True)], decompose_task),
             Tool("start_task", "Use for a small request that is a single step: edit one file, fix one bug, run one command, answer a question.",
                  [_p("task_text", "string", "the request", True)], start_task)]
    r = fm.run(goal[:MAX_GOAL], tools, "You are a router. Immediately call exactly one tool for the user's request.", max_tool_calls=1)
    if "error" in r and result["error"] is None:
        result["error"] = f"{r['error']}: {r['message']}"
    return result
