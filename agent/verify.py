"""The harness decides whether a task is done: it runs the tests itself and checks the evidence.
The model's claim that it finished counts for nothing."""
import ast
import re
import shlex
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .runner import LocalExecutor, pytest_summary, test_python

_PYTEST = re.compile(r"(?:python3?\s+-m\s+)?pytest\b([^`;&|\n]*)")
_SAFE = re.compile(r"^[\w\s./:\-=\[\]*,]*$")


@dataclass
class Verdict:
    ok: bool
    reasons: list
    summary: dict


_VALUE_FLAGS = {"-k", "-m", "-n", "-p", "--maxfail", "--tb", "--durations"}


def acceptance_args(done_when):
    """pytest arguments from a task's 'done when' text, if it names a pytest command; otherwise '' (run all tests).
    The text is prose written by a model, so only real arguments are kept: flags, the value after -k/-m/..., and file paths or node ids."""
    m = _PYTEST.search(done_when or "")
    tokens, kept, i = (m.group(1).split() if m else []), [], 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("-"):
            kept.append(t)
            if t in _VALUE_FLAGS and i + 1 < len(tokens):
                i += 1
                kept.append(tokens[i])
        elif ".py" in t or "/" in t or "::" in t:
            kept.append(t)
        else:
            break   # the first word that is not an argument ends the command ("... passes")
        i += 1
    args = " ".join(kept)
    return args if _SAFE.match(args) else ""


def is_test_path(path):
    p = path.replace("\\", "/").split("/")
    return p[-1] == "conftest.py" or (p[-1].endswith(".py") and (p[-1].startswith("test_") or p[-1].endswith("_test.py") or "tests" in p[:-1] or "test" in p[:-1]))


def judge(args, exit_code, output, error, edited, baseline_passed=None):
    """Is the evidence good enough? Every reason it is not is listed. `baseline_passed` is how many tests passed before the task:
    the count must go up, otherwise nothing new is proven (new tests written outside a test file are never collected, for example)."""
    summary, reasons = pytest_summary(output or ""), []
    if error:
        reasons.append(f"the test run did not finish ({error})")
    elif exit_code != 0:
        reasons.append(f"pytest exited with code {exit_code}" + (" (no tests were collected)" if exit_code == 5 else ""))
    if not error and summary.get("passed", 0) < 1:
        reasons.append("no test passed")
    for kind in ("skipped", "xfailed", "xpassed"):
        if summary.get(kind):
            reasons.append(f"{summary[kind]} test(s) {kind}")
    if summary.get("deselected") and not re.search(r"(^|\s)-k\b|::|(^|\s)-m\b", args):
        reasons.append("some tests were deselected")
    if not edited:
        reasons.append("no files were changed for this task")
    elif baseline_passed is not None and summary.get("passed", 0) <= baseline_passed and not error and exit_code == 0:
        reasons.append(f"no new test passes ({baseline_passed} passed before and {summary.get('passed', 0)} now): "
                       "add a test for the change in a test file (test_*.py)")
    return Verdict(not reasons, reasons, summary)


def failure_summary(output, limit=700):
    """The few lines of a failing pytest run that tell the model what to fix."""
    lines = (output or "").splitlines()
    keep = [l.rstrip() for l in lines if l.startswith(("FAILED", "ERROR", "E  ")) or re.match(r"^\S+\.py:\d+: ", l)]
    tail = [l for l in lines if re.search(r"\d+ (failed|passed|error)", l)][-1:]
    return "\n".join(dict.fromkeys(keep + tail))[:limit]


_UNDEFINED = re.compile(r"NameError: name '(\w+)' is not defined")
_NOT_IN = re.compile(r"ImportError: cannot import name '(\w+)' from '(\w+)'")


def import_hint(root, output):
    """Small models repeat two import mistakes attempt after attempt: using a project function without importing it (NameError), and
    importing one the module does not define (ImportError). Say what to add or define, when a module in the project's top folder shows it."""
    hints = [f"add `from {f.stem} import {name}` at the top of the file that uses it" for name in dict.fromkeys(_UNDEFINED.findall(output or ""))
             for f in sorted(root.glob("*.py")) if not is_test_path(f.name) and re.search(rf"^(?:def|class) {name}\b", f.read_text(errors="ignore"), re.M)]
    hints += [f"define `{name}` in {module}.py" for name, module in dict.fromkeys(_NOT_IN.findall(output or "")) if (root / f"{module}.py").is_file()]
    return "; ".join(hints)


_IMPORT = re.compile(r"^\s*(import\s+\S|from\s+\S+\s+import\s+\S)")


def protected_violations(root, originals):
    """Compare each existing test file with how it was before the task: it may not be deleted, and every one of its original lines
    (except imports and blank lines) must still be there, so a test-first change may edit an import, retype the existing lines around
    new ones, reorder, or remove what the task itself added, but not lose or alter an assertion."""
    out = []
    for path, before in originals.items():
        f = root / path
        try:
            now = f.read_text() if f.is_file() else None
        except (UnicodeDecodeError, OSError):
            now = ""
        if now is None:
            out.append(f"deleted the existing test file {path}")
        elif Counter(l for l in before.splitlines() if l.strip() and not _IMPORT.match(l)) - Counter(now.splitlines()):
            out.append(f"changed existing test code in {path}")
        elif any((root / f"{m.split('.')[0]}.py").is_file() for m in {l.split()[1] for l in before.splitlines() if _IMPORT.match(l)}
                 - {l.split()[1] for l in now.splitlines() if _IMPORT.match(l)}):
            out.append(f"removed an import from {path}")   # an import may be edited, but a project module the tests exercise must still be imported
    return out


def unmentioned_deletions(edits, task_text):
    """Files deleted although the task never names them."""
    return list(dict.fromkeys(f"deleted {r['path']}, which the task does not mention" for r in edits
                              if r["action"] == "delete_file" and r["path"] not in task_text and r["path"].rsplit("/", 1)[-1] not in task_text))


def syntax_errors(root, paths):
    out = []
    for p in dict.fromkeys(paths):
        f = root / p
        if p.endswith(".py") and f.is_file():
            try:
                compile(f.read_text(), p, "exec")
            except SyntaxError as e:
                out.append(f"{p}:{e.lineno}: {e.msg}" + (f" in `{e.text.strip()[:80]}`" if e.text and e.text.strip() else ""))   # the model sees the line, not only its number
            except (UnicodeDecodeError, ValueError) as e:
                out.append(f"{p}: cannot be read as Python ({e})")
    return out


def frozen_check(root, originals, timeout=300, args=""):
    """Run the test files as they were before the task against the project as it is now, in a scratch copy, so a test that was
    weakened by an inserted `return` or a skip still fails here. `args` is the task's pytest arguments: if they name test files, only those
    are run (tests for modules that are not written yet cannot be called regressions). Returns (ok, failure_summary)."""
    named = {a.split("::")[0] for a in args.split() if ".py" in a}
    targets = [p for p in originals if p.endswith(".py") and (Path(p).name.startswith("test_") or p.endswith("_test.py")) and (not named or p in named)]
    if not targets:
        return True, ""
    with tempfile.TemporaryDirectory() as scratch:
        shutil.copytree(root, scratch, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".agent", ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"))
        for p, text in originals.items():
            f = Path(scratch) / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text)
        cmd = f"{shlex.quote(test_python(root))} -m pytest -q -p no:cacheprovider {' '.join(shlex.quote(t) for t in targets)}"
        code, out, _, err = LocalExecutor().run(cmd, scratch, timeout)
    if err or code != 0:
        return False, failure_summary(out) or err or f"pytest exited with code {code}"
    return True, ""


# ---- can the new tests fail at all? ----
def _test_functions(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            yield node


def _asserts(fn):
    """A real check: an assert statement, pytest.raises/warns, or an assertXxx call. Comments and `pass` check nothing."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Assert):
            return True
        if isinstance(n, ast.Call):
            name = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if name in ("raises", "warns", "fail") or name.startswith("assert"):
                return True
    return False


def _project_names(tree, roots):
    """Names a test file brings in from the project's own modules (import calc / from calc import add / from . import x)."""
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and (n.level > 0 or (n.module or "").split(".")[0] in roots):
            names |= {a.asname or a.name for a in n.names}
        elif isinstance(n, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in n.names if a.name.split(".")[0] in roots}
    return names


def vacuous_tests(root, originals, changed, roots):
    """New test functions that cannot catch anything: no real assertion, or no use of the project's code.
    `originals` are the test files before the task, `changed` the test files the task touched, `roots` the project's module names."""
    problems = []
    for path in changed:
        try:
            tree = ast.parse((root / path).read_text())
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue   # unreadable files are the syntax check's business
        try:
            before = {f.name for f in _test_functions(ast.parse(originals[path]))} if path in originals else set()
        except SyntaxError:
            before = set()
        names = _project_names(tree, roots)
        for fn in _test_functions(tree):
            if fn.name in before:
                continue
            if not _asserts(fn):
                problems.append(f"{path}::{fn.name} is a new test that asserts nothing (comments and `pass` check nothing)")
            elif not any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(fn)):
                problems.append(f"{path}::{fn.name} is a new test that does not use any code from the project")
    return problems


def red_check(root, start_files, timeout=300):
    """Red then green. Run the tests as they are now against the project's code as it was before the task (new tests kept, source
    files put back): they must fail there. Tests that also pass without the change prove nothing. Returns (ok, why)."""
    with tempfile.TemporaryDirectory() as scratch:
        shutil.copytree(root, scratch, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".agent", ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"))
        now = {str(p.relative_to(scratch)) for p in Path(scratch).rglob("*.py")}
        for p in now | set(start_files):
            if is_test_path(p):
                continue
            f = Path(scratch) / p
            if p in start_files:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(start_files[p])
            elif f.exists():
                f.unlink()
        code, out, _, err = LocalExecutor().run(f"{shlex.quote(test_python(root))} -m pytest -q -p no:cacheprovider", scratch, timeout)
    if err is None and code == 0:
        return False, "all the tests, including your new ones, also pass on the code as it was before your change, so they do not prove the change: write a test that fails without it"
    return True, ""

