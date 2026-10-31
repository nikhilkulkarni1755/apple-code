"""Real-model measurements for the router and the decomposer.
Run from the repo root: .venv/bin/python -m experiments.measure_planner [route|plan]"""
import sys
import tempfile
import time

from agent.fm import FM
from agent.ledger import Ledger
from agent.memory import Memory
from agent.planner import FMBackend, Planner, route

REPS = 3
BIG = [
    "Create an operating system with a bootloader, a kernel, a scheduler and a file system.",
    "Build a full web application for a bookstore with user accounts, a catalog, a shopping cart and payments.",
    "Write a Python compiler for a small C-like language, with a lexer, parser, type checker and code generator.",
    "Implement a distributed key-value database with replication, sharding and a query language.",
    "Build a 2D game engine with physics, rendering, audio, a level editor and a scripting language.",
    "Create a command-line todo app with add, list, done and delete commands, persistent storage, tests and documentation.",
    "Design and implement a spreadsheet application with formulas, charts, import and export.",
    "Build a chat server and client with rooms, direct messages, history and authentication.",
    "Implement a package manager with dependency resolution, a registry and lock files.",
    "Create a static site generator with templates, markdown support, plugins and a dev server.",
]
SMALL = [
    "Fix the typo in README.md.",
    "Rename the function add to add_numbers in calc.py.",
    "Run the tests.",
    "Add a docstring to the sub function in calc.py.",
    "Delete the file notes.txt.",
    "Change the default timeout from 30 to 60 in config.py.",
    "What does the helper function in utils.py do?",
    "Add `import sys` to the top of main.py.",
    "Create an empty file called __init__.py in the pkg folder.",
    "Print the current Python version.",
]


def routing(fm):
    ok = {"big": 0, "small": 0}
    for kind, goals, want in (("big", BIG, "decompose"), ("small", SMALL, "direct")):
        for goal in goals:
            got = [route(fm, goal, Planner(Memory(Ledger(tempfile.mkdtemp())), type("B", (), {"split": lambda s, g, c: [{"title": "x"}]})()))["route"]
                   for _ in range(REPS)]
            ok[kind] += sum(g == want for g in got)
            print(f"{kind:5s} {goal[:70]:70s} {got}")
    print(f"\nbig requests sent to decompose_task: {ok['big']}/{len(BIG) * REPS}   small requests kept direct: {ok['small']}/{len(SMALL) * REPS}")


def planning(fm):
    for goal in BIG[:1] + BIG[5:6] + BIG[2:3]:
        root = tempfile.mkdtemp()
        L = Ledger(root)
        m = Memory(L)
        t0 = time.time()
        try:
            tasks = Planner(m, FMBackend(fm)).decompose(goal)
        except Exception as e:
            print(f"\n### {goal}\nFAILED after {time.time() - t0:.0f}s: {e}")
            continue
        flat = []
        def walk(ts, d=1):
            for t in ts:
                flat.append((d, bool(t.children)))
                walk(t.children, d + 1)
        walk(m.tasks())
        print(f"\n### {goal}\n{len(tasks)} top-level, {len(flat)} total, {sum(not c for _, c in flat)} leaves, depth {max(d for d, _ in flat)}, {time.time() - t0:.0f}s")
        print(m.get("Task plan")[:1800])


if __name__ == "__main__":
    fm = FM()
    for name in sys.argv[1:2] or ["route", "plan"]:
        print(f"\n===== {name} =====")
        {"route": routing, "plan": planning}[name](fm)
    fm.close()
