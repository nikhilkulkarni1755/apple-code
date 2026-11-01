"""A queue of agents. Each has its own MEMORY.md, may spawn up to 3 subagents, and at most 15 subagents are ever created.
State lives in the ledger database and in each agent's MEMORY.md, so an interrupted run resumes where it stopped."""
import time
from dataclasses import dataclass

from .ledger import ActionError
from .memory import MAX_GOAL, Memory, Task, clean

MAX_CHILDREN = 3      # subagents one agent may spawn
MAX_SUBAGENTS = 15    # subagents ever created in this workspace (the root agent is not counted)
MAX_ATTEMPTS = 3      # a crashing agent is retried this many times, then marked failed
ROOT = "root"
_SCHEMA = """CREATE TABLE IF NOT EXISTS agents (
    n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, parent TEXT, goal TEXT, status TEXT,
    attempts INTEGER DEFAULT 0, summary TEXT, error TEXT, memory TEXT, ts REAL)"""


@dataclass
class AgentCtx:
    """What a runner gets: the agent's identity, its own memory, and the means to spawn subagents."""
    queue: object
    id: str
    goal: str
    memory: Memory

    @property
    def ledger(self):
        return self.queue.ledger

    def spawn(self, goal):
        return self.queue.spawn(self.id, goal)


class AgentQueue:
    def __init__(self, ledger, runner, resume=True):
        self.ledger, self.runner = ledger, runner
        ledger.db.execute(_SCHEMA)
        self.resumed = self.resume() if resume else 0   # a read-only viewer must not touch agents another process is running

    def resume(self):
        """Agents left 'running' belong to a process that died: queue them again. Returns how many."""
        with self.ledger.db:
            return self.ledger.db.execute("UPDATE agents SET status='queued', error='interrupted' WHERE status='running'").rowcount

    # ---- reading ----
    def get(self, agent_id):
        row = self.ledger.db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise ActionError(f"no agent {agent_id!r}")
        return row

    def agents(self):
        return self.ledger.db.execute("SELECT * FROM agents ORDER BY n").fetchall()

    def memory(self, agent_id):
        return Memory(self.ledger, self.get(agent_id)["memory"])

    # ---- creating ----
    def _create(self, agent_id, parent, goal, path):
        goal = " ".join(goal.split())[:MAX_GOAL]
        with self.ledger.db:
            self.ledger.db.execute("INSERT INTO agents(id, parent, goal, status, memory, ts) VALUES (?,?,?,?,?,?)",
                                   (agent_id, parent, goal, "queued", path, time.time()))
        mem = Memory(self.ledger, path)
        with self.ledger.scope(agent=agent_id):
            mem.set("Project", f"Agent {agent_id}" + (f", spawned by {parent}." if parent else ", the root agent."))
            if parent:
                inherited = self.memory(parent).get("Design")
                if inherited:
                    mem.set("Design", inherited)
            mem.add_plan(goal, [Task(f"Plan: {goal[:60]}", goal, kind="research")])
        return agent_id

    def start(self, goal):
        """Give the root agent a goal; its first task is always to research it. Safe to call again after a restart (the same goal
        changes nothing); a new goal on an existing project is added to the same MEMORY.md and wakes the root agent."""
        if not isinstance(goal, str) or not goal.strip():
            raise ActionError("goal must be a non-empty string")
        row = self.ledger.db.execute("SELECT status FROM agents WHERE id=?", (ROOT,)).fetchone()
        if row is None:
            return self._create(ROOT, None, goal, "MEMORY.md")
        memory = self.memory(ROOT)
        if clean(goal, MAX_GOAL) not in memory.get("Goals"):
            with self.ledger.scope(agent=ROOT):
                text = " ".join(goal.split())[:MAX_GOAL]
                memory.add_plan(text, [Task(f"Plan: {text[:60]}", text, kind="research")])
            if row["status"] in ("done", "blocked", "failed"):
                self._set(ROOT, status="queued", attempts=0, error=None)
        elif row["status"] in ("blocked", "failed"):   # the same goal again: try what was blocked once more
            memory.unblock()
            self._set(ROOT, status="queued", attempts=0, error=None)
        return ROOT

    def spawn(self, parent, goal):
        """Queue a new subagent under `parent`. Refused beyond 3 per agent or 15 in total."""
        row = self.get(parent)
        if row["status"] == "failed":
            raise ActionError(f"agent {parent} failed and cannot spawn")
        if not isinstance(goal, str) or not goal.strip():
            raise ActionError("goal must be a non-empty string")
        db = self.ledger.db
        mine = db.execute("SELECT COUNT(*) FROM agents WHERE parent=?", (parent,)).fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM agents WHERE id != ?", (ROOT,)).fetchone()[0]
        if mine >= MAX_CHILDREN:
            raise ActionError(f"agent {parent} already spawned {MAX_CHILDREN} subagents (the limit)")
        if total >= MAX_SUBAGENTS:
            raise ActionError(f"{MAX_SUBAGENTS} subagents have already been created (the limit)")
        agent_id = f"a{total + 1}"
        return self._create(agent_id, parent, goal, f".agents/{agent_id}/MEMORY.md")

    # ---- running ----
    def _set(self, agent_id, **cols):
        with self.ledger.db:
            self.ledger.db.execute(f"UPDATE agents SET {', '.join(f'{k}=?' for k in cols)}, ts=? WHERE id=?", (*cols.values(), time.time(), agent_id))

    def _run_one(self, row):
        agent_id, attempts = row["id"], row["attempts"] + 1
        self._set(agent_id, status="running", attempts=attempts)
        ctx = AgentCtx(self, agent_id, row["goal"], Memory(self.ledger, row["memory"]))
        try:
            with self.ledger.scope(agent=agent_id):
                result = self.runner(ctx) or {}
        except KeyboardInterrupt:
            self._set(agent_id, status="queued", error="interrupted")   # saved before the interrupt propagates
            raise
        except Exception as e:
            self._set(agent_id, status="queued" if attempts < MAX_ATTEMPTS else "failed", error=f"{type(e).__name__}: {e}"[:300])
            return
        result = result if isinstance(result, dict) else {}
        status = result.get("status") if result.get("status") in ("done", "blocked") else "done"
        summary = str(result.get("summary", ""))[:300]
        self._set(agent_id, status=status, summary=summary, error=None)
        if row["parent"]:
            with self.ledger.scope(agent=row["parent"]):
                self.memory(row["parent"]).append("Subagents", f"- {agent_id} [{status}] {row['goal'][:80]} — {summary}")

    def run(self):
        """Run queued agents oldest first until none are left. Returns the final rows."""
        while True:
            row = self.ledger.db.execute("SELECT * FROM agents WHERE status='queued' ORDER BY n LIMIT 1").fetchone()
            if row is None:
                return self.agents()
            self._run_one(row)


def task_runner(step, undo_partial=True, say=None):
    """Work through the agent's own task plan one task at a time. A task's completion is written to the agent's MEMORY.md
    (through the ledger) before the next task starts; a task found 'doing' was interrupted, and its partial edits are undone first.
    `step(ctx, task)` returns {'ok': bool, 'summary': str, 'evidence': str}. `say(text)` is told about each task as it starts and ends."""
    say = say or (lambda text: None)

    def run(ctx):
        blocked = 0
        while (task := ctx.memory.next_task()) is not None:
            with ctx.ledger.scope(task=task.id, kind=task.kind or None):
                if task.status == "doing" and undo_partial:
                    ctx.ledger.undo_task(ctx.id, task.id)
                ctx.memory.mark(task.id, "doing")
                say(f"task {task.id} [{task.kind or '-'}] {task.title}")
                outcome = step(ctx, task) or {}
                say(f"  -> {'done' if outcome.get('ok') else 'BLOCKED'}: {outcome.get('summary', '')}")
                if outcome.get("ok"):
                    ctx.memory.complete(task.id, outcome.get("summary", "done"), outcome.get("evidence"))
                else:
                    ctx.memory.mark(task.id, "blocked")
                    blocked += 1
        return {"status": "blocked" if blocked else "done", "summary": f"{blocked} blocked task(s)" if blocked else "all tasks done"}

    return run
