"""How big a piece of work can the on-device model finish? A ladder of precisely specified tasks from a one-line function to a small
class, each run in an empty project through the real worker loop. Also checks the result against hidden tests, because 'verified by
the harness' is not the same as 'correct'.
Run from the repo root: .venv/bin/python -m experiments.measure_ladder [reps] [level ...]"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from agent.fm import FM
from agent.ledger import Ledger
from agent.memory import Memory, Task
from agent.worker import work_task

LADDER = [  # (level, title, spec, hidden test that only I can see)
    (1, "one-line function", "Create score.py with a function add_points(score, jumped) that returns score + 5 * jumped.",
     "from score import add_points\ndef test_h():\n    assert add_points(10, 2) == 20\n    assert add_points(0, 0) == 0\n    assert add_points(3.5, 1) == 8.5\n"),
    (2, "one-line function", "Create score.py with a function alive_bonus(seconds) that returns seconds * 0.5.",
     "from score import alive_bonus\ndef test_h():\n    assert alive_bonus(4) == 2.0\n    assert alive_bonus(0) == 0\n    assert alive_bonus(1.5) == 0.75\n"),
    (3, "small function, a rule", "Create score.py with a function final_score(score) that returns the score rounded down to a whole number (the floor), as an int.",
     "from score import final_score\ndef test_h():\n    assert final_score(12.9) == 12 and isinstance(final_score(12.9), int)\n    assert final_score(7.0) == 7\n    assert final_score(0.5) == 0\n"),
    (4, "function with logic", "Create collide.py with a function collides(a, b) where a and b are rectangles given as tuples (x, y, width, height). "
        "It returns True only when the rectangles overlap; rectangles that merely touch along an edge do not overlap.",
     "from collide import collides\ndef test_h():\n    assert collides((0, 0, 2, 2), (1, 1, 2, 2)) is True\n    assert collides((0, 0, 2, 2), (5, 5, 1, 1)) is False\n"
     "    assert collides((0, 0, 2, 2), (2, 0, 2, 2)) is False\n    assert collides((0, 0, 4, 4), (1, 1, 1, 1)) is True\n"),
    (5, "small function, branching", "Create keys.py with a function handle_key(state, key). state is a dict with keys running (bool) and score (float). "
        "If key is 'r' return a new dict {'running': True, 'score': 0.0}. If key is 'e' return a copy of state with running set to False and a new key exit set to True. "
        "For any other key return state unchanged.",
     "from keys import handle_key\ndef test_h():\n    assert handle_key({'running': False, 'score': 9.5}, 'r') == {'running': True, 'score': 0.0}\n"
     "    s = {'running': True, 'score': 3.0}\n    assert handle_key(s, 'e') == {'running': False, 'score': 3.0, 'exit': True}\n    assert s == {'running': True, 'score': 3.0}\n"
     "    assert handle_key(s, 'x') == s\n"),
    (6, "small class, 3 methods", "Create game.py with a class Game. Game() starts with score 0.0. Method tick(seconds) adds seconds * 0.5 to the score. "
        "Method cactus_jumped() adds 5 to the score. Property final_score returns the score rounded down to an int.",
     "from game import Game\ndef test_h():\n    g = Game()\n    g.tick(3)\n    g.cactus_jumped()\n    assert g.score == 6.5 and g.final_score == 6 and isinstance(g.final_score, int)\n"
     "    g.tick(1)\n    assert g.score == 7.0\n"),
    (7, "class with physics", "Create dino.py with a class Dino. Dino() starts with y = 0.0 and velocity = 0.0. Method jump() sets velocity to 10.0, but only if y is 0.0 "
        "(a jump in the air does nothing). Method step(dt) does: y += velocity * dt; velocity -= 30.0 * dt; and if y < 0 then y = 0.0 and velocity = 0.0.",
     "from dino import Dino\ndef test_h():\n    d = Dino()\n    d.jump()\n    d.step(0.1)\n    assert d.y > 0\n    d2 = Dino()\n    d2.jump()\n    d2.step(0.1)\n"
     "    v = d2.velocity\n    d2.jump()\n    assert d2.velocity == v\n    for _ in range(100):\n        d2.step(0.1)\n    assert d2.y == 0.0 and d2.velocity == 0.0\n"),
]


def run_one(fm, level, spec, hidden):
    root = Path(tempfile.mkdtemp())
    L = Ledger(root)
    m = Memory(L)
    task = Task(spec.split(".")[0][:70], spec, "pytest passes", kind="code", id="1")
    started = time.time()
    out = work_task(fm, L, m, task, max_iterations=4)
    seconds = time.time() - started
    verifies = [r for r in L.list() if r["action"] == "verify"]
    sessions = [json.loads(r["args"]) for r in L.list() if r["action"] == "session"]
    (root / "hidden_test_h.py").write_text(hidden)
    correct = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "hidden_test_h.py"], cwd=root, capture_output=True, text=True).returncode == 0
    lines = sum(len(p.read_text().splitlines()) for p in root.glob("*.py") if not p.name.startswith(("test_", "hidden_")))
    return {"verified": out["ok"], "correct": correct and out["ok"], "attempts": len(verifies), "seconds": round(seconds), "code_lines": lines,
            "peak_tokens": max((max(s["tokens"], s.get("estimated") or 0) for s in sessions), default=0),
            "wound_down": sum(1 for s in sessions if s.get("reason") == "budget"), "tool_calls": sum(s.get("tool_calls", 0) for s in sessions), "why": "" if out["ok"] else out["summary"][:90]}


if __name__ == "__main__":
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    only = {int(x) for x in sys.argv[2:]}
    fm = FM()
    rows = []
    for level, kind, spec, hidden in LADDER:
        if only and level not in only:
            continue
        for rep in range(reps):
            r = run_one(fm, level, spec, hidden)
            rows.append((level, kind, r))
            print(f"L{level} {kind:26s} run {rep + 1}: verified={str(r['verified']):5s} correct={str(r['correct']):5s} attempts={r['attempts']} "
                  f"{r['seconds']:4d}s code={r['code_lines']:3d} lines peak={r['peak_tokens']} tokens, {r['tool_calls']} tool calls, wound down {r['wound_down']}x {r['why']}", flush=True)
    print("\nsummary")
    for level, kind, *_ in LADDER:
        got = [r for lv, _, r in rows if lv == level]
        if got:
            print(f"  L{level} {kind:26s} verified {sum(r['verified'] for r in got)}/{len(got)}  actually correct {sum(r['correct'] for r in got)}/{len(got)}")
    fm.close()
