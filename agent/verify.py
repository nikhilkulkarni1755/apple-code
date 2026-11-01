"""The harness decides whether a task is done: it runs the tests itself and checks the evidence.
The model's claim that it finished counts for nothing."""
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
                out.append(f"{p}:{e.lineno}: {e.msg}")
            except (UnicodeDecodeError, ValueError) as e:
                out.append(f"{p}: cannot be read as Python ({e})")
    return out


def frozen_check(root, originals, timeout=300):
    """Run the test files as they were before the task against the project as it is now, in a scratch copy, so a test that was
    weakened by an inserted `return` or a skip still fails here. Returns (ok, failure_summary)."""
    targets = [p for p in originals if p.endswith(".py") and (Path(p).name.startswith("test_") or p.endswith("_test.py"))]
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
