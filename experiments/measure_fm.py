"""Phase 3 measurements: tool-schema token cost, tool selection, and edit-schema following.
Run from the repo root: .venv/bin/python -m experiments.measure_fm [tokens|select|edit]
Earlier line-number edit tools (insert_lines/delete_lines/replace_lines) scored 1/24 here; see PLAN.md section 11."""
import shutil
import sys
import tempfile
import time
from pathlib import Path

from agent.fm import FM, Tool
from agent.ledger import Ledger
from agent.tools import make_tools

REPS = 3
INSTRUCTIONS = ("You are a coding agent working in a project workspace. Use the tools to do what the user asks. "
                "Call one tool at a time.")
CALC = ("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n\n\n"
        "def div(a, b):\n    return a / b\n")
SEED = {"calc.py": CALC, "utils.py": "import os\nx = 1\n# TODO: clean up\ny = 3\n\n\ndef helper():\n    return x + y\n",
        "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        "README.md": "# Demo\n\nA tiny project.\n", "data.txt": "1,2,3\n"}


def workspace():
    root = Path(tempfile.mkdtemp())
    for name, text in SEED.items():
        (root / name).write_text(text)
    L = Ledger(root)
    return root, L, make_tools(L)


def compact(t):
    return Tool(t.name, t.description.split(".")[0] + ".", [dict(p, description="") for p in t.parameters])


def tokens(fm):
    _, _, tools = workspace()
    base = fm.count([], "", "x")
    print(f"baseline (no tools, no instructions, prompt 'x'): prompt={base['prompt']} instructions={base['instructions']} tools={base['tools']}")
    print(f"our instructions: {fm.count([], INSTRUCTIONS, 'x')['instructions']} tokens")
    print("\nper-tool schema cost (alone):")
    for t in tools:
        print(f"  {t.name:14s} {fm.count([t])['tools']:4d}   compact: {fm.count([compact(t)])['tools']:4d}")
    for n in (1, 3, 5, 8, 10):
        print(f"first {n:2d} tools together: {fm.count(tools[:n])['tools']:4d}   compact: {fm.count([compact(t) for t in tools[:n]])['tools']:4d}")


SELECT = [  # (prompt, accepted first tools, check on args)
    ("Show me the contents of calc.py.", {"read_file"}, lambda a: a["path"] == "calc.py"),
    ("What files are in this project?", {"list_files"}, lambda a: True),
    ("Find every place the function add is used.", {"grep"}, lambda a: "add" in a["pattern"]),
    ("Create a new file notes.txt containing the line: hello", {"create_file"}, lambda a: a["path"] == "notes.txt" and "hello" in a["content"]),
    ("Delete the file data.txt.", {"delete_file"}, lambda a: a["path"] == "data.txt"),
    ("Run the tests.", {"run_tests"}, lambda a: True),
    ("Run `python3 --version`.", {"run_command"}, lambda a: "version" in a["cmd"]),
    ("Add the line `# end` at the very end of calc.py.", {"insert_after", "read_file"}, lambda a: a["path"] == "calc.py"),
    ("Delete the TODO comment from utils.py.", {"replace_text", "read_file"}, lambda a: a["path"] == "utils.py"),
    ("Change `x = 1` in utils.py to `x = 2`.", {"replace_text", "read_file"}, lambda a: a["path"] == "utils.py"),
    ("Run only the test named test_add.", {"run_tests"}, lambda a: "test_add" in a.get("args", "")),
    ("Which .py files exist?", {"list_files"}, lambda a: ".py" in a.get("pattern", ".py")),
    ("Look at lines 5 to 10 of calc.py.", {"read_file"}, lambda a: a["path"] == "calc.py" and a.get("start") == 5 and a.get("end") == 10),
    ("Search for TODO comments.", {"grep"}, lambda a: "TODO" in a["pattern"]),
    ("Change the title of README.md to `# Demo App`.", {"replace_text", "read_file"}, lambda a: a["path"] == "README.md"),
    ("How many lines does utils.py have?", {"read_file", "run_command"}, lambda a: True),
    ("Install pytest with pip.", {"run_command"}, lambda a: "pip" in a["cmd"]),
    ("Make a new module mathx.py with a function double(x) returning x*2.", {"create_file"}, lambda a: a["path"] == "mathx.py"),
    ("Check whether the tests still pass.", {"run_tests"}, lambda a: True),
    ("Open README.md.", {"read_file"}, lambda a: a["path"] == "README.md"),
    ("Add `import sys` after the import line in utils.py.", {"insert_after", "read_file"}, lambda a: a["path"] == "utils.py"),
]


def select(fm):
    name_ok = args_ok = n = 0
    times = []
    for prompt, accepted, check in SELECT:
        row = []
        for _ in range(REPS):
            _, _, tools = workspace()
            t0 = time.time()
            r = fm.run(prompt, tools, INSTRUCTIONS, max_tool_calls=1)
            times.append(time.time() - t0)
            n += 1
            if not r["calls"]:
                row.append("none" if "error" not in r else r["error"])
                continue
            name, args, _ = r["calls"][0]
            ok_name = name in accepted
            try:
                ok_args = ok_name and bool(check(args))
            except (KeyError, TypeError):
                ok_args = False
            name_ok += ok_name
            args_ok += ok_args
            row.append(("OK " if ok_args else "arg ") + name if ok_name else "WRONG " + name)
        print(f"{prompt[:60]:60s} {row}")
    print(f"\nright tool: {name_ok}/{n}   right tool and args: {args_ok}/{n}   mean {sum(times) / len(times):.1f}s per session")


def splice(text, start, end, new):
    ls = text.splitlines(True)
    return "".join(ls[:start - 1]) + new + "".join(ls[end:])


EDITS = [  # (file, task, expected final text)
    ("calc.py", "Change sub so it adds instead of subtracting (make it `return a + b`).", splice(CALC, 6, 6, "    return a + b\n")),
    ("calc.py", "Append a new function `neg(a)` returning -a at the end of the file, after a blank-line gap like the other functions.",
     CALC + "\n\ndef neg(a):\n    return -a\n"),
    ("utils.py", "Delete the TODO comment line.", splice(SEED["utils.py"], 3, 3, "")),
    ("utils.py", "Change helper to return x * y instead (replace the return line).", splice(SEED["utils.py"], 8, 8, "    return x * y\n")),
    ("utils.py", "Insert the line `import sys` right after `import os`.", splice(SEED["utils.py"], 2, 1, "import sys\n")),
    ("calc.py", "Delete the whole mul function (its def line and its return line) but leave the blank lines.", splice(CALC, 9, 10, "")),
    ("calc.py", "Change div so it returns `a // b`.", splice(CALC, 14, 14, "    return a // b\n")),
    ("utils.py", "Replace the two lines `x = 1` and `# TODO: clean up` with the single line `x = 10`.", splice(SEED["utils.py"], 2, 3, "x = 10\n")),
]


def edit(fm):
    first = final = n = rejected = 0
    for fname, task, expected in EDITS:
        row = []
        for _ in range(REPS):
            root, L, tools = workspace()
            prompt = f"File {fname} currently contains:\n{SEED[fname]}\n\nTask: {task}"
            r = fm.run(prompt, tools, INSTRUCTIONS, max_tool_calls=4)
            edits = [c for c in r["calls"] if c[0] in ("replace_text", "insert_after", "create_file", "delete_file")]
            bad = sum(c[2].startswith("error") for c in edits)
            rejected += bad
            good = (root / fname).exists() and (root / fname).read_text() == expected
            n += 1
            final += good
            first += good and bad == 0
            row.append("OK" if good and not bad else "OK-after-retry" if good else f"FAIL({len(edits)} edits, {bad} rejected)")
        print(f"{task[:62]:62s} {row}")
    print(f"\nexact result on first try: {first}/{n}   exact result eventually: {final}/{n}   rejected edit calls: {rejected}")


if __name__ == "__main__":
    fm = FM()
    which = sys.argv[1:2] or ["tokens", "select", "edit"]
    for name in which:
        print(f"\n===== {name} =====")
        {"tokens": tokens, "select": select, "edit": edit}[name](fm)
    fm.close()
