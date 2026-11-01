"""Replay one ladder task and show every attempt: what the model wrote (including edits that were rolled back) and why it was rejected.
Run from the repo root: .venv/bin/python -m experiments.diagnose_task <level>"""
import json
import sys
import tempfile
from pathlib import Path

from agent.fm import FM
from agent.ledger import Ledger
from agent.memory import Memory, Task
from agent.worker import work_task
from experiments.measure_ladder import LADDER

def main():
    level = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    _, kind, spec, _ = next(x for x in LADDER if x[0] == level)
    root = Path(tempfile.mkdtemp())
    L = Ledger(root)
    fm = FM()
    task = Task(spec.split(".")[0][:70], spec, "pytest passes", kind="code", id="1")
    out = work_task(fm, L, Memory(L), task, max_iterations=4)
    print(f"TASK: {spec}\nRESULT: ok={out['ok']} {out['summary'][:120]}\n")
    for r in L.list():
        if r["action"] in ("create_file", "replace_lines", "insert_lines") and r["path"] and not r["path"].endswith("MEMORY.md") and r["depth"] == 0:
            print(f"--- attempt {r['iteration']} wrote {r['path']} ({'kept' if r['reverted_by'] is None else 'rolled back'}):\n{(r['added'] or '').rstrip()}\n")
        elif r["action"] == "verify":
            print(f"=== attempt {r['iteration']} verdict: {'VERIFIED' if r['status'] == 'ok' else 'rejected: ' + (r['error'] or '')[:300]}\n")
        elif r["action"] == "run_command" and r["kind"] == "test" and r["iteration"]:
            print(f"    (harness ran pytest, exit {r['exit_code']}) {L.output(r['seq']).strip().splitlines()[-1][:100] if L.output(r['seq']).strip() else ''}")
    fm.close()


if __name__ == "__main__":
    main()
