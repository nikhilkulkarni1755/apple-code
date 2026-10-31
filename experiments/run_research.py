"""End to end with the real model: goal -> root agent researches the codebase and the web -> actionables -> ledger trail.
Run from the repo root: .venv/bin/python -m experiments.run_research ["goal"]"""
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from agent.agents import AgentQueue, task_runner
from agent.fm import FM
from agent.ledger import Ledger
from agent.research import research_step
from agent.web import Web

GOAL = sys.argv[1] if len(sys.argv) > 1 else "Add a subtract function to calc.py, with a test, following how add is written and tested."
root = Path(tempfile.mkdtemp())
(root / "calc.py").write_text('"""Tiny calculator."""\n\n\ndef add(a, b):\n    """Return a + b."""\n    return a + b\n')
(root / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
(root / "README.md").write_text("# calc\n\nA tiny calculator library.\n")
L = Ledger(root)
fm = FM()
q = AgentQueue(L, task_runner(research_step(fm, Web())))
q.start(GOAL)
t0 = time.time()
q.run()
print(f"goal: {GOAL}\nfinished in {time.time() - t0:.0f}s\n")
for r in q.agents():
    print(f"agent {r['id']}: {r['status']} (attempts {r['attempts']}) {r['summary'] or r['error'] or ''}")
print("\n--- MEMORY.md task plan ---\n" + q.memory("root").get("Task plan"))
print("\n--- ledger: what was done under each task ---")
for task in sorted({r["task_id"] for r in L.list() if r["task_id"]}, key=lambda s: [int(x) for x in s.split(".")]):
    c = Counter((r["kind"] or "-", r["action"]) for r in L.list() if r["task_id"] == task and r["action"] not in ("create_file", "replace_lines", "insert_lines", "delete_lines"))
    if c:
        print(f"task {task}: " + ", ".join(f"{k}/{a}x{n}" for (k, a), n in sorted(c.items())))
print("\n--- sample records (action, element, task, kind) ---")
for r in [x for x in L.list() if x["action"] in ("grep", "read_file", "list_files", "web_search", "fetch_url")][:12]:
    print(f"  {r['action']:11s} {str(r['element'])[:45]:45s} task {r['task_id']} {r['kind']} {r['status']}")
fm.close()
