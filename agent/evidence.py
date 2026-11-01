"""Evidence for a task, gathered by the harness instead of by the model: an outline of the project's code (every function, class and
method with its arguments), and the text of any file the task names. It costs no model calls and a bounded number of tokens, so the
model's context window is spent on writing code, not on reading files with tools."""
import ast
import re

from .tools import visible_files
from .verify import is_test_path

_NAMES = re.compile(r"[\w./-]+\.\w{1,5}")


def _signature(fn):
    args = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
    return f"{fn.name}({', '.join(args)})"


def outline(text):
    """['def add(a, b)', 'class Game: tick(self, seconds), score'] for one module's source, or None if it does not parse."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append("def " + _signature(node))
        elif isinstance(node, ast.ClassDef):
            members = [_signature(n) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) else n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) or isinstance(n, ast.ClassDef)]
            members += [t.id for n in node.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)]
            out.append(f"class {node.name}" + (": " + ", ".join(members) if members else ""))
        elif isinstance(node, ast.Assign):
            out += [t.id for t in node.targets if isinstance(t, ast.Name) and t.id.isupper()]
    return out


def gather(ledger, task_text, budget=1800):
    """Facts for a task, at most `budget` characters. Only files inside the project are ever read."""
    files = ledger._snapshot()
    shown = visible_files(ledger)
    if not shown:
        return "The project has no files yet."
    lines = ["Code in the project:"]
    for path in shown:
        if path.endswith(".py") and not is_test_path(path):
            parts = outline(files[path])
            lines.append(f"  {path}: " + ("; ".join(parts) if parts else "(empty)" if parts == [] else "(does not parse)"))
    tests = [p for p in shown if is_test_path(p)]
    if tests:
        lines.append("Tests: " + ", ".join(tests[:8]))
    others = [p for p in shown if not p.endswith(".py")]
    if others:
        lines.append("Other files: " + ", ".join(others[:10]))
    text = "\n".join(lines)
    named = [m for m in dict.fromkeys(_NAMES.findall(task_text)) if m in files and m in shown]   # a name must be a real project file
    for path in named:
        body = files[path]
        if len(body.splitlines()) <= 60 and len(text) + len(body) + 40 <= budget:
            text += f"\nContents of {path}:\n{body.rstrip()}"
    return text if len(text) <= budget else text[:budget - 20].rstrip() + "\n... (cut)"
