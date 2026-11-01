"""Does asking for structure produce better steps from a 3B model? Compares the current free-form splitter with one that must name a
file and a function/class for every step. Run from the repo root: .venv/bin/python -m experiments.measure_decomposition"""
import re
import tempfile

from agent.fm import FM, Tool
from agent.ledger import Ledger
from agent.memory import Memory
from agent.planner import FMBackend, Planner, _p

GOALS = [
    "recreate the chrome no internet game in python in the CLI, basically cactus objects keep rushing towards t rex object and using keyboard the t rex jumps. "
    "add a scoring system, every cactus jumped is +5 points, every second the game is still alive the score should go up by .5 ... total score at the end "
    "(once the trex and object touch) will be the floor of the score. then clicking r on keyboard will restart it, clicking e will exit it",
    "Build a command-line todo app: add a task, list tasks, mark a task done, delete a task, and keep the tasks in a JSON file between runs.",
    "Write a small library that parses simple arithmetic expressions like '2 + 3 * (4 - 1)' and evaluates them, with proper operator precedence.",
]


class SpecBackend:
    """Every step must say which file and which function or class it is, and what it does."""

    def __init__(self, fm):
        self.fm = fm

    def split(self, goal, context):
        found = []

        def add_step(file, name, does):
            found.append({"title": f"Write {name} in {file}", "detail": does, "done_when": f"pytest passes for {name}", "big": False})
            return f"step {len(found)} added"

        tool = Tool("add_step", "Add one step: one function or one small class, in one file.",
                    [_p("file", "string", "python file, e.g. scoring.py"), _p("name", "string", "the function or class name"),
                     _p("does", "string", "what it takes in, what it returns, and the rule it follows")], add_step)
        prompt = (f"Request: {goal}\nList the functions and classes needed to build this, in the order to build them, by calling add_step once for each. "
                  "Each step is ONE function or ONE class with at most 3 methods, short enough to write in under 15 lines. "
                  "Keep rules, scores and game state in plain functions or classes that need no keyboard and no screen, so tests can check them.")
        r = self.fm.run(prompt, [tool], "You plan software work as a list of small functions and classes.", max_tool_calls=16)
        return found


def judge(tasks):
    details = [t.detail.strip().lower() for t in tasks]
    return {"steps": len(tasks), "distinct_specs": f"{len(set(details))}/{len(tasks)}",
            "name_a_function_or_class": f"{sum(bool(re.search(r'[a-z_]+\\(|class |def |Write \\w+ in', t.title + ' ' + t.detail)) for t in tasks)}/{len(tasks)}",
            "avg_spec_words": round(sum(len(t.detail.split()) for t in tasks) / max(len(tasks), 1))}


if __name__ == "__main__":
    fm = FM()
    for goal in GOALS:
        print("\n" + "=" * 100 + f"\nGOAL: {goal[:110]}")
        for name, backend in (("free-form (today)", FMBackend(fm)), ("structured: file + function/class", SpecBackend(fm))):
            L = Ledger(tempfile.mkdtemp())
            try:
                tasks = Planner(Memory(L), backend, max_depth=1).decompose(goal)
            except Exception as e:
                print(f"\n--- {name}: FAILED ({e})")
                continue
            print(f"\n--- {name}: {judge(tasks)}")
            for t in tasks:
                print(f"   {t.title[:60]:60s} | {t.detail[:80]}")
    fm.close()
