"""Shell execution (swappable executor) and pytest helpers."""
import os
import re
import shlex
import signal
import subprocess
import sys
import time

_RM = (r"\brm\b(?:\s+-\S+)*?\s+(?:-\w*[rR]\w*|--recursive)\b[^;&|]*?\s(?:--\s+)?[\"']?"
       r"(?:/|~|\$HOME|\$\{HOME\}|\.\.|\.)/?\*?[\"']?(?:\s|$|[;&|])")
_DENY = [(r"\bsudo\b|\bdoas\b|\bsu\s+-", "privileged commands are not allowed"),
         (r"\.agent\b", "the ledger directory is off limits"),
         (_RM, "recursive delete of the workspace, home, or a parent directory is not allowed"),
         (r"\bmkfs\b|\bdd\b[^;&|]*\bof=/dev/|>\s*/dev/(sd|disk|nvme)", "raw disk writes are not allowed"),
         (r":\s*\(\s*\)\s*\{", "fork bombs are not allowed"),
         (r"\b(curl|wget)\b[^;&|]*\|\s*(sudo\s+)?(ba|z)?sh\b", "piping downloads into a shell is not allowed"),
         (r"\bchmod\s+-R\b[^;&|]*\s/(\s|$)", "recursive chmod of / is not allowed")]
# Commands the model runs get only these variables of the parent environment, so exported secrets (tokens, API keys) are not visible to them.
_ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "LANG", "TZ", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE")
_ENV_KEEP_PREFIX = ("LC_", "UV_", "PIP_", "NPM_", "npm_config_", "PYTHONUTF8")   # tool configuration, not credentials
_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)")


def check_command(cmd, timeout):
    """Why this command must not run, or None. A speed bump against accidents, not a sandbox."""
    if not isinstance(cmd, str) or not cmd.strip():
        return "cmd must be a non-empty string"
    if "\0" in cmd:
        return "cmd contains a NUL byte"
    if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
        return "timeout must be a number of seconds between 0 and 3600"
    return next((why for rx, why in _DENY if re.search(rx, cmd, re.I)), None)


def command_env(cwd):
    """Environment for a command: only allowlisted variables, the workspace .venv first on PATH if it exists, never the agent's own venv,
    pip refusing to install outside a virtualenv, and no bytecode caches (an edit and its undo within one second
    keep the same mtime and size, so Python would reuse a stale .pyc)."""
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP or k.startswith(_ENV_KEEP_PREFIX)}
    env.update(PIP_REQUIRE_VIRTUALENV="true", PYTHONDONTWRITEBYTECODE="1")   # no stale .pyc after edit+undo in one second
    own = os.environ.get("VIRTUAL_ENV")   # the agent's own venv: its bin directory must not be on the command's PATH
    path = [p for p in env.get("PATH", "").split(os.pathsep) if not own or p != os.path.join(own, "bin")]
    venv = os.path.join(cwd, ".venv")
    if os.path.isdir(venv):
        env["VIRTUAL_ENV"] = venv
        path.insert(0, os.path.join(venv, "bin"))
    env["PATH"] = os.pathsep.join(path)
    return env


class LocalExecutor:
    def run(self, cmd, cwd, timeout):
        """Run `cmd` in a shell; returns (exit_code, stdout+stderr, seconds, error). A timeout kills the whole process group."""
        start = time.time()
        try:
            p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, errors="replace", start_new_session=True, env=command_env(cwd))
        except (ValueError, OSError) as e:
            return None, "", 0.0, f"could not start: {e}"
        try:
            out, _ = p.communicate(timeout=timeout)
            return p.returncode, out, time.time() - start, None
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            out, _ = p.communicate()
            return None, out, time.time() - start, f"timed out after {timeout}s"


def pytest_summary(output):
    """Counts from pytest's final summary line, e.g. {'passed': 2, 'failed': 1}; {} if no test ran."""
    for line in reversed(output.splitlines()):
        if re.search(r" in \d+(\.\d+)?s", line):
            return {("errors" if k.startswith("error") else k): int(n) for n, k in _COUNT.findall(line)}
    return {}


def test_python(root):
    """The interpreter tests run with: the workspace's .venv if it has one, otherwise the agent's own."""
    venv_python = root / ".venv" / "bin" / "python"
    return str(venv_python if venv_python.exists() else sys.executable)


def run_tests(ledger, args="", timeout=300):
    """Run pytest with the workspace's venv python if it has one. Returns the ledger seq of the run."""
    return ledger.run_command(f"{shlex.quote(test_python(ledger.root))} -m pytest -q {args}", timeout)
