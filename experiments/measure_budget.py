"""The PROMPT1 problem, verbatim, with the wind-down threshold set to THRESHOLD tokens. For every model session it records the exact
tokens of the starting context (tools, instructions, prompt), the tokens of the code the model wrote, and the tokens the session used;
it also records when a new agent was needed (a session wound down, a continuation task queued).
Run from the repo root:  .venv/bin/python -m experiments.measure_budget THRESHOLD OUT.json CHECK.py [PROMPT1.md [ATTEMPTS [SECONDS]]]
(ATTEMPTS: tries per step, default 4; SECONDS: no new model session starts after this, default 780.)"""
import functools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import agent.handoff
import agent.planner
import agent.research
import agent.worker
from agent.agents import AgentQueue, task_runner
from agent.fm import FM
from agent.ledger import Ledger
from agent.web import Web
from agent.worker import solve_step

THRESHOLD, OUT, CHECK = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
GOAL = re.search(r'apple-code solve "(.*)"', Path(sys.argv[4] if len(sys.argv) > 4 else "PROMPT1.md").read_text()).group(1)
for module in (agent.handoff, agent.planner, agent.research, agent.worker):
    module.BUDGET = THRESHOLD   # each of them copied the constant when it was imported
DEADLINE = int(sys.argv[6]) if len(sys.argv) > 6 else 780   # seconds: no new session starts after this, so the helper is still alive when the results are saved
if len(sys.argv) > 5:
    agent.worker.work_task = functools.partial(agent.worker.work_task, max_iterations=int(sys.argv[5]))   # solve_step looks work_task up here when it runs
WRITES = {"create_file": "content", "replace_text": "new", "insert_after": "text"}


def role(tools):
    names = {t.name for t in tools}
    return ("router" if "decompose_task" in names else "planner" if "add_task" in names else "research" if "add_actionable" in names
            else "worker" if "replace_text" in names else "other")


class Meter:
    """Stands in for FM: counts exactly what each session starts with, what it wrote and what it used."""

    def __init__(self, real):
        self.real, self.sessions, self.ledger = real, [], None

    def __getattr__(self, name):
        return getattr(self.real, name)

    def run(self, prompt, tools=(), instructions="", max_tool_calls=8, temperature=None, budget_tokens=None):
        if time.time() - T0 > DEADLINE:
            raise KeyboardInterrupt   # not an Exception, so nothing in the agent swallows it
        start = self.real.count(tools, instructions, prompt)
        t0 = time.time()
        r = self.real.run(prompt, tools, instructions, max_tool_calls=max_tool_calls, temperature=temperature, budget_tokens=budget_tokens)
        wrote = sum(self.real.count(prompt=str(args.get(WRITES[name], "")))["prompt"] for name, args, _ in r.get("calls", []) if name in WRITES)
        self.sessions.append({"role": role(tools), "tools": start["tools"], "instructions": start["instructions"], "prompt": start["prompt"],
                              "start": start["tools"] + start["instructions"] + start["prompt"], "wrote": wrote, "tokens": r.get("tokens"),
                              "estimated": r.get("estimated"), "reason": r.get("reason"), "error": r.get("error"), "calls": len(r.get("calls", [])),
                              "secs": round(time.time() - t0, 1), "prompt": prompt, "log": [(n, a, str(res)[:300]) for n, a, res in r.get("calls", [])], "after_seq": self.ledger.db.execute("SELECT max(seq) FROM actions").fetchone()[0]})
        return r


T0 = time.time()
root = Path(tempfile.mkdtemp())
L = Ledger(root)
real = FM()
fm = Meter(real)
fm.ledger = L
q = AgentQueue(L, task_runner(solve_step(fm, Web())))
q.start(GOAL)
t0 = time.time()
try:
    q.run()
finally:
    handoffs = [r["seq"] for r in L.db.execute("SELECT seq FROM actions WHERE action='handoff' ORDER BY seq")]
    plan = q.memory("root").get("Task plan")
    used = lambda s: (s["estimated"] if s["reason"] else s["tokens"]) or 0   # Apple's count is not reliable after a forced stop
    cum, running, first_wind, first_handoff = [], 0, None, None
    for s in fm.sessions:
        running += used(s)
        cum.append(running)
        if s["reason"] == "budget" and first_wind is None:
            first_wind = running
        if handoffs and first_handoff is None and s["after_seq"] > handoffs[0]:
            first_handoff = cum[-2] if len(cum) > 1 else 0
    if handoffs and first_handoff is None:
        first_handoff = running
    check = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, '.'); exec(open({str(CHECK)!r}).read())"], cwd=root,
                           capture_output=True, text=True)
    written = {p.name: real.count(prompt=p.read_text())["prompt"] for p in root.glob("*.py")}
    timed_out = time.time() - T0 > DEADLINE
    OUT.write_text(json.dumps({"threshold": THRESHOLD, "timed_out": timed_out, "secs": round(time.time() - t0), "sessions": fm.sessions, "cumulative": cum,
                               "first_wind_down_cumulative": first_wind, "first_handoff_cumulative": first_handoff, "handoffs": len(handoffs),
                               "plan": plan, "solved": check.returncode == 0, "check": (check.stdout + check.stderr)[-300:],
                               "solution_tokens": written, "files": sorted(p.name for p in root.iterdir() if not p.name.startswith("."))}, indent=1))
    shutil.copytree(root, OUT.with_suffix(".dir"), dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))   # the ledger and the files, to look through afterwards
    real.close()
