"""End to end with the real model: goal -> research -> code tasks worked and verified by the harness.
Run from the repo root: .venv/bin/python -m experiments.run_solve ["goal"]"""
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from agent.agents import AgentQueue, task_runner
from agent.fm import FM
from agent.ledger import Ledger
from agent.web import Web
from agent.worker import solve_step

GOAL = sys.argv[1] if len(sys.argv) > 1 else "Add a subtract function to calc.py, with a test, following how add is written and tested."
root = Path(tempfile.mkdtemp())
(root / "calc.py").write_text('"""Tiny calculator."""\n\n\ndef add(a, b):\n    """Return a + b."""\n    return a + b\n')
(root / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
L = Ledger(root)
fm = FM()
q = AgentQueue(L, task_runner(solve_step(fm, Web())))
q.start(GOAL)
t0 = time.time()
q.run()
print(f"goal: {GOAL}\nfinished in {time.time() - t0:.0f}s\n")
for r in q.agents():
    print(f"agent {r['id']}: {r['status']} {r['summary'] or r['error'] or ''}")
print("\n--- task plan ---\n" + q.memory("root").get("Task plan"))
print("\n--- attempts (verify records) ---")
for r in L.list():
    if r["action"] == "verify":
        print(f"  task {r['task_id']} attempt {r['iteration']}: {'VERIFIED' if r['status'] == 'ok' else 'rejected: ' + (r['error'] or '')[:110]}")
print("\n--- ledger actions by task/kind ---")
for task in sorted({r["task_id"] for r in L.list() if r["task_id"]}, key=lambda s: [int(x) for x in s.split(".")]):
    c = Counter((r["kind"] or "-", r["action"]) for r in L.list() if r["task_id"] == task and r["status"] == "ok" and r["depth"] in (None, 0))
    print(f"task {task}: " + ", ".join(f"{k}/{a} x{n}" for (k, a), n in sorted(c.items())))
print("\n--- edits that were undone (what the model tried) ---")
for r in L.list():
    if r["reverted_by"] is not None and r["path"] and r["depth"] == 0 and not r["path"].endswith("MEMORY.md"):
        print(f"  attempt {r['iteration']} {r['action']} {r['path']}: removed {r['removed']!r} -> added {r['added']!r}")
print("\n--- calc.py ---\n" + (root / "calc.py").read_text() + "--- test_calc.py ---\n" + (root / "test_calc.py").read_text())
import subprocess
print("--- pytest now ---\n" + subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=root, capture_output=True, text=True).stdout[-300:])
fm.close()
