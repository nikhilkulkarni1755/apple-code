"""What the examples leave open. After a step is verified, the harness runs the module on edge inputs (agent/probe_run.py) and makes small
changes to its code (a `<` becomes `<=`, a `+` becomes `-`). Hangs, errors the task never mentions and changes that no test notices are
reported as findings, with an input that shows the difference. They are never rejections: the expected value is not known to the harness."""
import ast
import json
import shlex
import shutil
import tempfile
from pathlib import Path

from .runner import LocalExecutor, test_python

MAX_MUTANTS = 30
MAX_FINDINGS = 8
_BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Div, ast.Mod: ast.Mult}
_CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
        ast.In: ast.NotIn, ast.NotIn: ast.In}


def _change(node, apply=False):
    """The small change that can be made to this node, or None. Without `apply`: the node as text. With `apply`: the node is changed in
    place and the result is '`size < 1` becomes `size <= 1`'."""
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        change = lambda: setattr(node, "op", _BIN[type(node.op)]())
    elif isinstance(node, ast.Compare) and type(node.ops[0]) in _CMP:
        change = lambda: node.ops.__setitem__(0, _CMP[type(node.ops[0])]())
    elif isinstance(node, ast.BoolOp):
        change = lambda: setattr(node, "op", ast.Or() if isinstance(node.op, ast.And) else ast.And())
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        change = lambda: setattr(node, "op", ast.UAdd())
    elif isinstance(node, ast.Constant) and type(node.value) is int:
        change = lambda: setattr(node, "value", node.value + 1)
    else:
        return None
    before = ast.unparse(node)
    if apply:
        change()
    return f"`{before}` becomes `{ast.unparse(node)}`" if apply else before


def mutants(source):
    """(line, what changed, changed source) for each small change to the code, the first MAX_MUTANTS of them."""
    out = []
    while len(out) < MAX_MUTANTS:
        tree = ast.parse(source)
        sites = [n for n in ast.walk(tree) if _change(n) is not None]
        if len(out) >= len(sites):
            break
        out.append((sites[len(out)].lineno, _change(sites[len(out)], apply=True), ast.unparse(tree)))
    return out


def sweep(root, module, examples, timeout=60):
    """What each public function of the module does on edge inputs: [{'call', 'out', 'notes'}], or [] if it could not be run."""
    cmd = f"{shlex.quote(test_python(root))} {shlex.quote(str(Path(__file__).with_name('probe_run.py')))} {shlex.quote(module)} {shlex.quote(json.dumps(examples))}"
    code, out, _, err = LocalExecutor().run(cmd, root, timeout)
    try:
        return json.loads(out.strip().splitlines()[-1]) if code == 0 and not err else []
    except (ValueError, IndexError):
        return []


def findings(root, path, text, examples, timeout=30):
    """Findings for the module at `path` (e.g. 'maths.py'): [str]. `text` is the task, so errors it names are not reported; `examples` are the author's."""
    module = path.removesuffix(".py").replace("/", ".")
    base = sweep(root, module, examples)
    out = [f"hard: {r['call']} does not finish" for r in base if r["out"] == "hang"]
    out += [f"soft: {r['call']} raises {r['out'][7:]}, which the task does not mention" for r in base if r["out"].startswith("raises ") and r["out"][7:] not in text]
    out += [f"soft: {r['call']} {note}" for r in base for note in r["notes"]]
    with tempfile.TemporaryDirectory() as scratch:
        shutil.copytree(root, scratch, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".agent", ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"))
        source, python, silent = (root / path).read_text(), shlex.quote(test_python(root)), 0
        try:
            changes = mutants(source)
        except SyntaxError:
            changes = []
        for line, what, changed in changes:
            (Path(scratch) / path).write_text(changed)
            code, _, _, err = LocalExecutor().run(f"{python} {shlex.quote(str(Path(__file__).with_name('pytest_limit.py')))} -q -x -p no:cacheprovider --test-limit 2", scratch, timeout)
            if code == 0 and not err:   # no test noticed the change: find an input where it shows
                now = {r["call"]: r["out"] for r in sweep(Path(scratch), module, examples)}
                shown = next(((r["call"], r["out"], now[r["call"]]) for r in base if r["call"] in now and now[r["call"]] != r["out"]), None)
                if shown and len(out) < MAX_FINDINGS:
                    out.append(f"soft: no example settles line {line} ({what}): {shown[0]} gives {shown[1]}, and {shown[2]} with the change")
                else:
                    silent += 1
        (Path(scratch) / path).write_text(source)
    return (out + ([f"soft: {silent} more small changes to {path} are not noticed by any test"] if silent else []))[:MAX_FINDINGS]
