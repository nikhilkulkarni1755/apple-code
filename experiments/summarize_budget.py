"""Tables from the files experiments.measure_budget wrote.   .venv/bin/python -m experiments.summarize_budget DIR"""
import json
import sys
from collections import defaultdict
from pathlib import Path

runs = sorted((json.loads(f.read_text()) for f in Path(sys.argv[1]).glob("*.json")), key=lambda d: (d["threshold"], d["secs"]))
used = lambda s: (s["estimated"] if s["reason"] else s["tokens"]) or 0

print("per run: threshold | sessions | wound down | handoffs | tokens to first wind-down | tokens to first handoff | total tokens | secs | solved | timed out")
for d in runs:
    ss = d["sessions"]
    print(f"{d['threshold']:>5} | {len(ss):>3} | {sum(s['reason'] == 'budget' for s in ss):>3} | {d['handoffs']:>2} | {d['first_wind_down_cumulative']} | "
          f"{d['first_handoff_cumulative']} | {sum(map(used, ss))} | {d['secs']} | {d['solved']} | {d.get('timed_out')}")

print("\nstarting context, exact tokens (tools + instructions + prompt), by kind of session:")
by = defaultdict(list)
for d in runs:
    for s in d["sessions"]:
        by[s["role"]].append(s)
for role, ss in sorted(by.items()):
    m = lambda k: sum(s[k] for s in ss) / len(ss)
    print(f"  {role:9s} n={len(ss):3d}  tools {m('tools'):5.0f}  instructions {m('instructions'):4.0f}  prompt {m('prompt'):5.0f}  start {m('start'):5.0f}  "
          f"max start {max(s['start'] for s in ss)}")

print("\nwhat the model wrote (tokens of code in create_file/replace_text/insert_after) and what a worker session used:")
for d in runs:
    w = [s for s in d["sessions"] if s["role"] == "worker"]
    print(f"  threshold {d['threshold']:>5}: worker sessions {len(w)}, wrote {[s['wrote'] for s in w]}, used {[used(s) for s in w]}; "
          f"final files {d['solution_tokens']}")
