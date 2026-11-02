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
MAX_LENGTH = 200
_NODES = (ast.Expression, ast.Compare, ast.Call, ast.Attribute, ast.Name, ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set, ast.UnaryOp,
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


def parse_examples(text):
    """The usable examples in `text`: comparisons separated by ';' or new lines, e.g. 'f(1) == 2; f(0) == 0'. Anything else is dropped."""
    out = []
    for piece in re.split(r"[;\n]", text or ""):
        piece = piece.strip().strip("`").strip()
        if not piece or len(piece) > MAX_LENGTH:
            continue
        try:
            tree = ast.parse(piece, mode="eval")
        except (SyntaxError, ValueError, RecursionError):
            continue
        if isinstance(tree.body, ast.Compare) and _safe(tree) and any(isinstance(n, ast.Call) for n in ast.walk(tree)):
            out.append(ast.unparse(tree))
        if len(out) >= MAX_EXAMPLES:
            break
    return list(dict.fromkeys(out))


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


def render_test(module, examples):
    """The text of a pytest file for these (already parsed) examples."""
    names = sorted({n.id for ex in examples for n in ast.walk(ast.parse(ex, mode="eval")) if isinstance(n, ast.Name)
                    and isinstance(n.ctx, ast.Load) and not hasattr(builtins, n.id)})
    lines = [f"from {module} import {', '.join(names)}" if names else f"import {module}", "", ""]
    for i, ex in enumerate(examples, 1):
        lines += [f"def test_spec_{i}():", f"    assert {ex}", "", ""]
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

