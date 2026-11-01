"""How good are the examples the model proposes? Each is evaluated against a reference implementation (true / false / error).
Run from the repo root: .venv/bin/python -u -m experiments.grade_examples [samples per level]"""
import sys

import agent.spec as spec
from agent.fm import FM
from experiments.measure_ladder import LADDER

REF = {
    1: "def add_points(score, jumped):\n    return score + 5 * jumped\n",
    2: "def alive_bonus(seconds):\n    return seconds * 0.5\n",
    3: "import math\ndef final_score(score):\n    return int(math.floor(score))\n",
    4: "def collides(a, b):\n    ax, ay, aw, ah = a\n    bx, by, bw, bh = b\n    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah\n",
    5: "def handle_key(state, key):\n    if key == 'r':\n        return {'running': True, 'score': 0.0}\n    if key == 'e':\n        return {**state, 'running': False, 'exit': True}\n    return state\n",
    6: "import math\nclass Game:\n    def __init__(self):\n        self.score = 0.0\n    def tick(self, seconds):\n        self.score += seconds * 0.5\n    def cactus_jumped(self):\n        self.score += 5\n    @property\n    def final_score(self):\n        return int(math.floor(self.score))\n",
    7: "class Dino:\n    def __init__(self):\n        self.y = 0.0\n        self.velocity = 0.0\n    def jump(self):\n        if self.y == 0.0:\n            self.velocity = 10.0\n    def step(self, dt):\n        self.y += self.velocity * dt\n        self.velocity -= 30.0 * dt\n        if self.y < 0:\n            self.y = 0.0\n            self.velocity = 0.0\n",
}

if __name__ == "__main__":
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    raw_seen = []
    real = spec.parse_examples
    spec.parse_examples = lambda text: (raw_seen.append(text), real(text))[1]
    fm = FM()
    tot = {"true": 0, "false": 0, "error": 0, "unusable": 0, "proposals_with_no_usable_example": 0, "proposals_with_a_wrong_example": 0}
    for level, kind, text, _ in LADDER:
        ns = {}
        exec(REF[level], ns)
        for i in range(samples):
            raw_seen.clear()
            parsed = spec.propose_examples(fm, text)
            n_raw = len([p for p in (raw_seen[0] if raw_seen else "").replace("\n", ";").split(";") if p.strip()])
            verdicts = []
            for ex in parsed:
                try:
                    verdicts.append("T" if eval(ex, dict(ns)) else "F")
                except Exception:
                    verdicts.append("E")
            tot["true"] += verdicts.count("T"); tot["false"] += verdicts.count("F"); tot["error"] += verdicts.count("E")
            tot["unusable"] += n_raw - len(parsed); tot["proposals_with_no_usable_example"] += not parsed
            tot["proposals_with_a_wrong_example"] += any(v != "T" for v in verdicts)
            print(f"L{level} sample {i + 1}: model gave {n_raw}, usable {len(parsed)}: {''.join(verdicts) or '-'}  e.g. {(parsed or [''])[0][:70]}", flush=True)
    print("\nTOTAL:", tot, flush=True)
    fm.close()
