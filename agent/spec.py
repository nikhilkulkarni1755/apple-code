"""Examples as the specification of a step. A step says what it will do as a few one-line comparisons, `add_points(10, 2) == 20`; the
harness turns them into a pytest file before the model starts, so the model writes only the implementation, the tests start red, and the
next agent inherits a precise, checkable idea of the step.
The examples come from a model and end up in a file that pytest executes, so they are parsed, restricted to a small safe subset of Python,
and written out from the parsed form, never copied as text."""
import ast
import builtins
import re

from .fm import Tool

MAX_EXAMPLES = 8
MAX_LENGTH = 400
_IMPORTABLE = {"PIL", "numpy", "sklearn", "pydicom", "math", "re", "json", "datetime", "collections", "itertools", "functools", "string", "decimal", "fractions", "statistics"}   # an example may import these
_NODES = (ast.Module, ast.Expr, ast.Assign, ast.Store, ast.ImportFrom, ast.Import, ast.alias, ast.Expression, ast.Compare, ast.Call, ast.Attribute, ast.Name, ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set, ast.UnaryOp,
          ast.BinOp, ast.BoolOp, ast.Subscript, ast.Slice, ast.keyword, ast.Load, ast.cmpop, ast.operator, ast.unaryop, ast.boolop)
_BANNED = {"open", "eval", "exec", "compile", "input", "getattr", "setattr", "delattr", "globals", "locals", "vars", "breakpoint", "exit", "quit",
           "help", "memoryview", "print", "os", "sys", "subprocess", "shutil", "importlib", "socket", "pathlib", "builtins", "pty", "ctypes",
           "signal", "threading", "multiprocessing", "pickle", "marshal", "tempfile", "glob", "urllib", "http", "requests"}


def _safe(tree):
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            return False
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in _BANNED):
            return False
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in _BANNED):
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, (bytes, complex)):
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 120:
            return False
    return True


def _split(text, pattern):
    """`text` cut where `pattern` matches outside brackets and quotes, so 'f(1, 2) == 3' is not cut at its comma."""
    depth, quote, mask = 0, "", []
    for c in text:
        inside = bool(depth or quote)
        if quote:
            quote = "" if c in (quote, "\n") else quote
        elif c in "'\"":
            quote, inside = c, True
        elif c in "([{":
            depth, inside = depth + 1, True
        elif c in ")]}":
            depth = max(depth - 1, 0)
        mask.append("_" if inside else c)
    cuts = [(0, 0)] + [m.span() for m in re.finditer(pattern, "".join(mask))] + [(len(text), len(text))]
    return [text[a[1]:b[0]] for a, b in zip(cuts, cuts[1:])]


_RAISES = re.compile(r"^(.+\)) raises ([A-Z]\w*)$")
_THEN = r"\bthen\b"


def _statement(part):
    """One step of an example as (source, kind), or None. kind is 'check' for a comparison ('f(1) == 2'), 'raises' for 'f(0) raises ValueError',
    'step' for a call or an assignment that sets things up."""
    part = part.strip().strip("`").rstrip(",.").strip()
    raises = _RAISES.match(part)
    try:
        tree = ast.parse(raises.group(1) if raises else part)
    except (SyntaxError, ValueError, RecursionError):
        return None
    node = tree.body[0] if len(tree.body) == 1 else None
    if isinstance(node, (ast.Import, ast.ImportFrom)):   # `from PIL import Image`: a step that sets things up
        modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
        return (ast.unparse(node), "step") if getattr(node, "level", 0) == 0 and all(m.split(".")[0] in _IMPORTABLE for m in modules) else None
    if node is None or not _safe(tree):
        return None
    if raises:
        return (f"{ast.unparse(node)} raises {raises.group(2)}", "raises") if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and hasattr(builtins, raises.group(2)) else None
    if isinstance(node, ast.Assign):
        return ast.unparse(node), "step"
    if not isinstance(node, ast.Expr):
        return None
    if isinstance(node.value, ast.Compare):   # a comparison needs a plain value on one side: prose such as 'sub(a, b) in calc.py' is not an example
        plain = any(not any(isinstance(m, (ast.Call, ast.Name)) for m in ast.walk(o)) for o in [node.value.left, *node.value.comparators])
        return (ast.unparse(node), "check") if plain else None
    return (ast.unparse(node), "step") if isinstance(node.value, ast.Call) else None


def _example(piece):
    """An example as text, or None: steps joined by ' then ' (`c = Counter(5) then c.add(2) then c.value() == 7`), the last one a check and one of them a call.
    One step that is not code makes the whole example void, so a step is never silently skipped."""
    first, *rest = _split(piece, _THEN)
    head = next((st for i in [0, *(w.start() for w in re.finditer(r"(?<![\w.])[A-Za-z_]", first))] if (st := _statement(first[i:]))), None)   # prose may come first
    steps = [head, *map(_statement, rest)]
    if None in steps or steps[-1][1] == "step":
        return None
    example = " then ".join(s for s, _ in steps)
    lines = _lines(example)
    return None if _needs(lines)[1] or not any(isinstance(n, ast.Call) for n in ast.walk(ast.parse("\n".join(lines)))) else example   # it uses a name that is set up nowhere ('image', or 'pop()' with no stack): the test could only fail


def parse_examples(text):
    """The usable examples in `text`, in a list or in a sentence ('for example f(1) == 2, f(0) raises ValueError'). Anything else is dropped."""
    out = []
    for piece in _split(text or "", r"[;\n]|,(?!\s*then\b)|\.(?=\s|$)|\band\b"):
        if len(piece.strip()) <= MAX_LENGTH and (ex := _example(piece)):
            out.append(ex)
        if len(out) >= MAX_EXAMPLES:
            break
    return list(dict.fromkeys(out))


PROPERTY_TYPES = ("int", "float", "str", "bool", "list")


def parse_property(variables, check):
    """({name: type}, check as text) for a property such as ('x: list, n: int', 'len(chunk(x, n)) <= len(x)'): the variables are declared, and the check
    is a safe comparison or and/or of comparisons that calls something. None otherwise."""
    pairs = [v.split(":") for v in str(variables).split(",")]
    declared = {p[0].strip(): p[1].strip() for p in pairs if len(p) == 2}
    try:
        tree = ast.parse(str(check).strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        return None
    if len(declared) != len(pairs) or not all(k.isidentifier() and not k.startswith("_") and t in PROPERTY_TYPES for k, t in declared.items()) or not _safe(tree):
        return None
    if not isinstance(tree.body, (ast.Compare, ast.BoolOp)) or not any(isinstance(n, ast.Call) for n in ast.walk(tree)) or _needs([ast.unparse(tree)])[1] - set(declared):
        return None
    return declared, ast.unparse(tree)


def join_examples(examples, limit=200):
    """Examples joined with '; ', keeping whole examples only so that the text still parses after being cut to `limit` characters."""
    out = ""
    for ex in examples:
        if len(out) + len(ex) + 2 > limit:
            break
        out += ("; " if out else "") + ex
    return out


def py_paths(text):
    """The project .py files named in `text` (no absolute or parent paths, every part an identifier), each once, in order."""
    out = []
    for path in re.findall(r"[\w./-]+\.py(?!\w)", text or ""):
        if not path.startswith(("/", ".")) and ".." not in path and all(p.isidentifier() for p in path.removesuffix(".py").split("/")):
            out.append(path)
    return list(dict.fromkeys(out))


def module_of(text):
    """(dotted module, path) of the first project source file named in `text`, e.g. ('score', 'score.py'); None if there is none."""
    for path in py_paths(text):
        parts = path.removesuffix(".py").split("/")
        if parts[-1].startswith("test_") or parts[-1].endswith("_test") or parts[-1] == "conftest":
            continue
        return ".".join(parts), path
    return None


def _lines(example):
    """The lines of a test that carry out one example."""
    out = []
    for part in _split(example, _THEN):
        src, kind = _statement(part)
        call = _RAISES.match(src)
        out += [f"with pytest.raises({call[2]}):", f"    {call[1]}"] if call else [f"assert {src}" if kind == "check" else src]
    return out


def _needs(lines):
    """(names a test made of these lines must import from the module under test: the functions and classes it calls, names nothing defines)."""
    nodes = list(ast.walk(ast.parse("\n".join(lines))))
    have = {*dir(builtins), "pytest", *(n.id for n in nodes if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)),
            *((a.asname or a.name).split(".")[0] for n in nodes if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names)}
    calls = {n.func.id for n in nodes if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)} - have
    return calls, {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} - have - calls


CLI = '''import pathlib
import shutil
import subprocess
import sys
import tempfile


def cli(script, *args, files=None):
    """(exit code, stdout) of `python script args`, run in a copy of the project with the given files added."""
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(".", tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".*", "__pycache__"))
        for name, text in (files or {}).items():
            pathlib.Path(tmp, name).write_text(text)
        done = subprocess.run([sys.executable, script, *args], cwd=tmp, capture_output=True, text=True, timeout=10)
    return done.returncode, done.stdout
'''


def render_test(module, examples):
    """The text of a pytest file for these (already parsed) examples. An example may run the project as a program with `cli('tool.py', 'arg')`."""
    bodies = [_lines(ex) for ex in examples]
    names = sorted(set().union(*(_needs(b)[0] for b in bodies)) - {"cli"})
    runs = any("cli(" in l for b in bodies for l in b)
    lines = ["import pytest"] * any("pytest.raises" in l for b in bodies for l in b) + [f"from {module} import {', '.join(names)}"] * bool(names) + [f"import {module}"] * (not names and not runs)
    lines += [CLI.rstrip("\n")] * runs + ["", ""]
    for i, body in enumerate(bodies, 1):
        lines += [f"def test_spec_{i}():", *(f"    {l}" for l in body), "", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def spec_test_path(module):
    return f"test_{module.replace('.', '_')}_spec.py"


def ensure_spec_test(ledger, text, examples_text):
    """Write the tests for a step's examples into the project, once. Returns the test file's path, or None when the step has no usable
    examples or does not name the source file it is about."""
    examples, found = parse_examples(examples_text), module_of(text)
    if not examples or not found:
        return None
    path = spec_test_path(found[0])
    if (ledger.root / path).exists():
        return path
    ledger.do("create_file", {"path": path, "content": render_test(found[0], examples)})
    return path


def propose_examples(fm, text):
    """Ask the model for examples of what a step must do (a call and its exact result each), one short session. Returns the ones that
    parse as safe comparisons. The examples decide what counts as correct, so a step whose examples are wrong is blocked or, worse, verified
    wrongly: they are as good as the model's reading of the step."""
    found = []

    def add_example(example):
        found.append(example)
        return f"example {len(found)} added"

    tool = Tool("add_example", "Add one example: a call and its exact expected result, as a Python comparison.",
                [{"name": "example", "type": "string", "description": "such as name(1, 2) == 3", "optional": False}], add_example)
    prompt = (f"Task: {text}\nWrite 3 to 5 examples that show exactly what this must do, including one edge case. "
              "Call add_example once for each. An example is a call and its expected result, such as name(1, 2) == 3.")
    fm.run(prompt, [tool], "You write test examples for a coding task.", max_tool_calls=8)
    return parse_examples("; ".join(str(e) for e in found))

