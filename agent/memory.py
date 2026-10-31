"""MEMORY.md: the project's long-term state, readable by people and by future model sessions.
Every write goes through the ledger, so memory changes are recorded and undoable like any edit."""
import re
from dataclasses import dataclass, field

from .ledger import ActionError, diff_action

SECTIONS = ["Project", "Goals", "Design", "Current state", "Last task completed", "Task plan"]
PREAMBLE = "# MEMORY\n\nProject memory, kept current by the agent after every task. Edit freely: unknown sections are kept.\n"
EMPTY = "(none yet)"
_LIMIT = {"Task plan": 100_000}
_DEFAULT_LIMIT = 20_000
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _/-]{0,39}$")
_TASK = re.compile(r"^\s*- \[([ ~x!])\] (\d+(?:\.\d+)*) (.*)$")
_DONE_WHEN = re.compile(r"\s*\(done when: (.*)\)$")
_MARK = {"todo": " ", "doing": "~", "done": "x", "blocked": "!"}
KINDS = ("research", "code")   # what a task is: look something up, or change something
_KIND = re.compile(r"^\[(research|code)\] ")


@dataclass
class Task:
    title: str
    detail: str = ""
    done_when: str = ""
    status: str = "todo"
    id: str = ""
    children: list = field(default_factory=list)
    kind: str = ""


def clean(value, limit):
    """One line of plain text that survives a render/parse round trip."""
    value = " ".join(value.split()).replace(" — ", " - ").replace("(done when:", "(done when -")
    return value[:limit]


def render_tasks(tasks, depth=0):
    lines = []
    for t in tasks:
        line = f"{'  ' * depth}- [{_MARK[t.status]}] {t.id} {'[' + t.kind + '] ' if t.kind else ''}{t.title}"
        line += f" — {t.detail}" if t.detail else ""
        line += f" (done when: {t.done_when})" if t.done_when else ""
        lines += [line] + ([render_tasks(t.children, depth + 1)] if t.children else [])
    return "\n".join(lines)


def parse_tasks(body):
    """Tolerant: lines that are not task lines are ignored; a task whose parent is missing becomes a top-level task."""
    by_id, roots = {}, []
    status = {v: k for k, v in _MARK.items()}
    for line in body.split("\n"):
        m = _TASK.match(line)
        if not m or m.group(2) in by_id:
            continue
        mark, tid, rest = m.groups()
        kind = _KIND.match(rest)
        rest = rest[kind.end():] if kind else rest
        dw = _DONE_WHEN.search(rest)
        rest = rest[:dw.start()] if dw else rest
        title, _, detail = rest.partition(" — ")
        task = by_id[tid] = Task(title.strip(), detail.strip(), dw.group(1).strip() if dw else "", status[mark], tid,
                                 kind=kind.group(1) if kind else "")
        parent = by_id.get(tid.rpartition(".")[0])
        (parent.children if parent else roots).append(task)
    return roots


def _sanitize(tasks):
    """Fresh copies that are safe to render: one-line fields, valid status, no tasks without a title."""
    out = []
    for t in tasks if isinstance(tasks, list) else []:
        title = clean(t.title, 80) if isinstance(getattr(t, "title", None), str) else ""
        if title:
            text = lambda v, n: clean(v, n) if isinstance(v, str) else ""
            out.append(Task(title, text(t.detail, 200), text(t.done_when, 200), t.status if t.status in _MARK else "todo",
                            children=_sanitize(t.children), kind=t.kind if t.kind in KINDS else ""))
    return out


def _number(tasks, prefix="", start=1):
    for i, t in enumerate(tasks, start):
        t.id = f"{prefix}{i}"
        _number(t.children, t.id + ".")
    return tasks


def _walk(tasks):
    for t in tasks:
        yield t
        yield from _walk(t.children)


def _rollup(tasks):
    """A task with children is done exactly when all of its children are done."""
    for t in tasks:
        _rollup(t.children)
        if t.children:
            t.status = "done" if all(c.status == "done" for c in t.children) else ("todo" if t.status == "done" else t.status)


def _parse(text):
    pre, sections, cur = [], {}, None
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            cur = sections.setdefault(m.group(1), [])
        else:
            (pre if cur is None else cur).append(line)
    return "\n".join(pre).strip("\n") + "\n", {k: "\n".join(v).strip("\n") for k, v in sections.items()}


def _render(pre, sections):
    names = [n for n in SECTIONS] + [n for n in sections if n not in SECTIONS]
    out = [pre.rstrip("\n"), ""]
    for n in names:
        out += [f"## {n}", "", sections.get(n) or EMPTY, ""]
    return "\n".join(out)


class Memory:
    def __init__(self, ledger, path="MEMORY.md"):
        self.ledger = ledger
        self.path = ledger._resolve(path)[0]   # rejects paths outside the workspace

    def _read(self):
        return self.ledger._read(self.ledger.root / self.path)

    def _load(self):
        text = self._read()
        return (PREAMBLE, {}) if text is None else _parse(text)

    def _save(self, pre, sections):
        old, new = self._read(), _render(pre, sections)
        if old == new:
            return None
        return self.ledger.do("create_file", {"path": self.path, "content": new}) if old is None \
            else self.ledger.do(*diff_action(self.path, old, new))

    @staticmethod
    def _name(name, sections):
        if not isinstance(name, str) or not _NAME.match(name.strip()):
            raise ActionError(f"bad section name: {name!r}")
        known = {s.casefold(): s for s in [*SECTIONS, *sections]}
        return known.get(name.strip().casefold(), name.strip())

    @staticmethod
    def _body(name, body):
        if not isinstance(body, str):
            raise ActionError("section text must be a string")
        body = re.sub(r"(?m)^(#{1,2})(?=\s|$)", r"\\\1", body.replace("\0", "").replace("\r\n", "\n")).strip("\n")
        if len(body) > _LIMIT.get(name, _DEFAULT_LIMIT):
            raise ActionError(f"{name} is longer than {_LIMIT.get(name, _DEFAULT_LIMIT)} characters")
        return body

    def sections(self):
        return {n: (b if b != EMPTY else "") for n, b in self._load()[1].items()}

    def get(self, name):
        pre, secs = self._load()
        return self.sections().get(self._name(name, secs), "")

    def set(self, name, body):
        """Replace one section. Returns the ledger seq, or None if nothing changed."""
        pre, secs = self._load()
        name = self._name(name, secs)
        secs[name] = self._body(name, body)
        return self._save(pre, secs)

    def append(self, name, line):
        pre, secs = self._load()
        name = self._name(name, secs)
        old = secs.get(name, "")
        old = "" if old == EMPTY else old
        secs[name] = self._body(name, (old + "\n" if old else "") + (line if isinstance(line, str) else str(line)))
        return self._save(pre, secs)

    # ---- the task plan ----
    def tasks(self):
        return parse_tasks(self._load()[1].get("Task plan", ""))

    def add_plan(self, goal, tasks):
        """Append a decomposed goal (Task objects without ids) as new top-level tasks, and record it in Goals and Current state."""
        pre, secs = self._load()
        existing, tasks = parse_tasks(secs.get("Task plan", "")), _sanitize(tasks)
        if not tasks:
            raise ActionError("the plan has no tasks with a title")
        plan = existing + _number(tasks, start=len(existing) + 1)
        return self._save_plan(pre, secs, plan, goal=goal)

    def add_children(self, parent_id, tasks):
        """Add new subtasks (e.g. actionables found by research) under an existing task and reopen it if it was done."""
        pre, secs = self._load()
        plan = parse_tasks(secs.get("Task plan", ""))
        parent = next((t for t in _walk(plan) if t.id == parent_id), None)
        tasks = _sanitize(tasks)
        if parent is None or not tasks:
            raise ActionError(f"no task {parent_id!r}" if parent is None else "the plan has no tasks with a title")
        _number(tasks, prefix=parent.id + ".", start=len(parent.children) + 1)
        parent.children += tasks
        parent.status = "todo" if parent.status == "done" else parent.status
        return self._save_plan(pre, secs, plan)

    def _save_plan(self, pre, secs, plan, goal=None, last=None):
        _rollup(plan)
        secs["Task plan"] = self._body("Task plan", render_tasks(plan))
        if goal is not None:
            secs["Goals"] = self._body("Goals", (secs.get("Goals", "").replace(EMPTY, "") + "\n- " + clean(goal, 300)).strip("\n"))
        if last is not None:
            secs["Last task completed"] = self._body("Last task completed", last)
        leaves = [t for t in _walk(plan) if not t.children]
        nxt = next((t for t in leaves if t.status in ("todo", "doing")), None)
        secs["Current state"] = self._body("Current state", f"{sum(t.status == 'done' for t in leaves)}/{len(leaves)} tasks done. "
                                           + (f"Next: {nxt.id} {nxt.title}" if nxt else "All tasks done."))
        return self._save(pre, secs)

    def next_task(self):
        return next((t for t in _walk(self.tasks()) if not t.children and t.status in ("todo", "doing")), None)

    def mark(self, task_id, status):
        if status not in _MARK:
            raise ActionError(f"bad status: {status!r}")
        return self._update(task_id, status)

    def complete(self, task_id, summary, evidence=None):
        """Mark a task done and record it as the last completed task; `evidence` is e.g. 'run #12 exit 0'."""
        if not isinstance(summary, str):
            raise ActionError("summary must be a string")
        return self._update(task_id, "done", summary, evidence)

    def _update(self, task_id, status, summary=None, evidence=None):
        pre, secs = self._load()
        plan = parse_tasks(secs.get("Task plan", ""))
        task = next((t for t in _walk(plan) if t.id == task_id), None)
        if task is None:
            raise ActionError(f"no task {task_id!r}")
        task.status = status
        last = None
        if summary is not None:
            last = f"{task.id} {task.title} — {clean(summary, 300)}" + (f" (evidence: {clean(str(evidence), 100)})" if evidence else "")
        return self._save_plan(pre, secs, plan, last=last)

    def context(self, max_chars=2500):
        """What a fresh model session should be told: project, goals, state, last task, and the next few tasks."""
        s = self.sections()
        todo = [t for t in _walk(self.tasks()) if not t.children and t.status in ("todo", "doing")][:5]
        parts = [f"{n}: {s[n][:600]}" for n in ("Project", "Goals", "Design", "Current state", "Last task completed") if s.get(n)]
        if todo:
            parts.append("Next tasks:\n" + "\n".join(render_tasks([Task(t.title, t.detail, t.done_when, t.status, t.id, kind=t.kind)]) for t in todo))
        text = "\n".join(parts)
        return text if len(text) <= max_chars else text[:max_chars - 1] + "…"
