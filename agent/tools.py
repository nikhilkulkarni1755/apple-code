"""The model-facing tools. Every mutation goes through the ledger; failures come back to the model as text."""
import fnmatch
import re
import signal
import threading
from contextlib import contextmanager

from .fm import Tool
from .ledger import ActionError, diff_action
from .runner import run_tests
from .verify import syntax_errors


def _p(name, type_, description, optional=False):
    return {"name": name, "type": type_, "description": description, "optional": optional}


def _clip(text, n=2000):
    return text if len(text) <= n else text[:n // 2] + f"\n...[{len(text) - n} chars cut]...\n" + text[-n // 2:]


_PYTEST_WORD = re.compile(r"^\s*(?:python3?\s+-m\s+)?pytest\b\s*")


def _test_args(ledger, args):
    """pytest arguments as a small model writes them: a leading 'pytest' is dropped, and a test file named without its tests/ folder is found there."""
    return " ".join(f"tests/{a}" if a.endswith(".py") and not (ledger.root / a).exists() and (ledger.root / "tests" / a).exists() else a
                    for a in _PYTEST_WORD.sub("", args).split())


def _str(value, name, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value):
        raise ActionError(f"{name} must be a {'non-empty ' if nonempty else ''}string")
    return value


@contextmanager
def _time_limit(seconds):
    """Raise TimeoutError if the block runs too long (interrupts even a catastrophic regex). Main thread only."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def boom(*_):
        raise TimeoutError(f"took longer than {seconds}s (catastrophic regex?)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


_FENCE = re.compile(r"\A\s*```[^\n]*\n(.*?)\n?```.*\Z", re.S)


def visible_files(ledger):
    """The project's files as the model should see them: not the agent's own notes (MEMORY.md, .agents/)."""
    return sorted(p for p in ledger._snapshot() if p.rsplit("/", 1)[-1] != "MEMORY.md" and not p.startswith(".agents/"))


def make_tools(ledger, web=None):
    """The model's tools. Reads are logged to the ledger too (action, element, task), so it shows everything that was done.
    Pass a `web` (agent.web.Web) to also offer web_search and fetch_url."""

    def logged(action, element, args, fn, limit=2500):
        try:
            result = fn()
        except Exception as e:
            ledger.log_read(action, element, args, error=str(e))
            raise
        ledger.log_read(action, element, args, result)
        return _clip(result, limit)   # the ledger keeps all of it; the model sees what fits its small window

    def attempt(action, path, args, fn):
        """Run an edit; if it was refused before reaching the ledger (bad snippet, wrong type), record the refusal here."""
        last = lambda: ledger.db.execute("SELECT MAX(seq) FROM actions").fetchone()[0]
        before = last()
        try:
            return fn()
        except Exception as e:
            if last() == before:
                ledger.log_read(action, path, args, error=str(e))
            raise

    def _read_file(path, start=1, end=None):
        if type(start) is not int or start < 1 or (end is not None and (type(end) is not int or end < start)):
            raise ActionError("start must be an integer >= 1 and end an integer >= start")
        rel, full = ledger._resolve(path)
        text = ledger._read(full)
        if text is None:
            return f"error: {rel} does not exist"
        lines = text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")
        if start > max(len(lines), 1):
            raise ActionError(f"{rel} has only {len(lines)} lines")
        last = min(end or len(lines), len(lines), start + 199)
        return "\n".join(lines[i - 1] for i in range(start, last + 1)) or "(empty)"

    def read_file(path, start=1, end=None):
        return logged("read_file", path, {"path": path, "start": start, "end": end}, lambda: _read_file(path, start, end))

    def _list_files(pattern="*"):
        _str(pattern, "pattern")
        return "\n".join([p for p in visible_files(ledger) if fnmatch.fnmatch(p, pattern)][:200]) or "(none)"

    def list_files(pattern="*"):
        return logged("list_files", pattern, {"pattern": pattern}, lambda: _list_files(pattern))

    def _grep(pattern, glob="*"):
        rx = re.compile(_str(pattern, "pattern"))
        _str(glob, "glob")
        with _time_limit(3):
            files = ledger._snapshot()
            hits = [f"{p}:{i}: {l}" for p in visible_files(ledger) for t in [files[p]] if fnmatch.fnmatch(p, glob)
                    for i, l in enumerate(t.splitlines(), 1) if rx.search(l)]
        return "\n".join(hits[:50]) or "(no matches)"

    def grep(pattern, glob="*"):
        return logged("grep", glob, {"pattern": pattern, "glob": glob}, lambda: _grep(pattern, glob))

    def unique(path, snippet, name):
        _str(snippet, name, nonempty=True)
        rel, full = ledger._resolve(path)
        text = ledger._read(full)
        if text is None:
            raise ActionError(f"{rel} does not exist")
        if text.count(snippet) != 1:
            n = text.count(snippet)
            raise ActionError(f"{name} must appear exactly once in {rel} (found {n} times)" + (f". The file begins:\n{text[:600]}" if n == 0 else ""))
        return rel, text, text.index(snippet)

    def insert_after(path, anchor, text):
        return attempt("insert_after", path, {"path": path, "anchor": anchor, "text": text}, lambda: _insert_after(path, anchor, text))

    def _insert_after(path, anchor, text):
        rel, content, i = unique(path, anchor, "anchor")
        end = content.find("\n", i + len(anchor))
        end = len(content) if end < 0 else end + 1
        eol = "\r\n" if content[:end].endswith("\r\n") else "\n"
        body = _str(text, "text", nonempty=True).rstrip("\r\n").replace("\r\n", "\n").replace("\n", eol) + eol
        head = content[:end] if content[:end].endswith("\n") else content[:end] + eol
        return f"ok (#{ledger.do(*diff_action(rel, content, head + body + content[end:]))})"

    def replace_text(path, old, new):
        return attempt("replace_text", path, {"path": path, "old": old, "new": new}, lambda: _replace_text(path, old, new))

    def _replace_text(path, old, new):
        rel, text, i = unique(path, old, "old")
        if old == _str(new, "new"):
            raise ActionError("old and new are identical")
        end = i + len(old)
        if not new:  # deleting must not leave an empty line behind: remove the line instead
            start, stop = text.rfind("\n", 0, i) + 1, text.find("\n", end)
            stop = len(text) if stop < 0 else stop + 1
            if not (text[start:i] + text[end:stop]).strip():
                i, end = start, stop
        return f"ok (#{ledger.do(*diff_action(rel, text, text[:i] + new + text[end:]))})"

    def edit(action):
        return lambda **args: f"ok (#{ledger.do(action, args)})"

    def create_file(path, content):
        """Write a whole file. If it exists it is replaced, as one recorded, undoable change (a 3B model's first move to rewrite a
        file is to create it again; refusing sent it into loops of the same error)."""
        if not isinstance(path, str):
            return edit("create_file")(path=path, content=content)   # let the ledger reject it with its usual message
        rel, full = ledger._resolve(path)
        old = ledger._read(full)
        if isinstance(content, str) and not rel.endswith(".md"):
            content = _FENCE.sub(r"\1\n", content)   # the model often wraps code in a markdown fence, which is a syntax error in the file
        if old is None or not isinstance(content, str):
            return edit("create_file")(path=path, content=content)
        if old == content:
            raise ActionError(f"{rel} already has exactly this content")
        return f"ok (#{ledger.do(*diff_action(rel, old, content))}) replaced the existing {rel}"

    def check_syntax(path=""):
        paths = [ledger._resolve(_str(path, "path"))[0]] if path else [p for p in visible_files(ledger) if p.endswith(".py")]
        return logged("check_syntax", path or "*.py", {"path": path}, lambda: "\n".join(syntax_errors(ledger.root, paths)) or "ok: no syntax errors")

    def shell(seq):
        r = ledger.get(seq)
        rows = [x for x in ledger.list() if x["cause"] == seq]
        changed = [x["path"] for x in rows if x["path"]]
        return (f"exit {r['exit_code']}" if r["error"] is None else r["error"]) + (f"; changed: {', '.join(changed)}" if changed else "") \
            + ("; environment changed (venv/packages)" if any(x["action"] == "env_change" for x in rows) else "") + "\n" + _clip(ledger.output(seq))

    tools = [
        Tool("read_file", "Show the contents of a file (optionally only lines start to end).",
             [_p("path", "string", "file path"), _p("start", "integer", "first line", True), _p("end", "integer", "last line", True)], read_file),
        Tool("list_files", "List file names (not their contents) matching a glob pattern; default all files.",
             [_p("pattern", "string", "glob such as *.py", True)], list_files),
        Tool("grep", "Search files with a regex; prints path:line: text.",
             [_p("pattern", "string", "regex"), _p("glob", "string", "only files matching this glob", True)], grep),
        Tool("create_file", "Write a whole file (replaces it if it already exists).",
             [_p("path", "string", "file path"), _p("content", "string", "full file content")], create_file),
        Tool("replace_text", "Replace one exact piece of text in a file. old must appear exactly once; new takes its place (new='' deletes it). "
             "Put only the changed text in old and new, never the rest of the file.",
             [_p("path", "string", "file path"), _p("old", "string", "exact text to replace"), _p("new", "string", "replacement text")], replace_text),
        Tool("insert_after", "Insert text on new lines right after the line containing anchor (anchor must appear exactly once). "
             "To append to the end of a file, use its last line as the anchor.",
             [_p("path", "string", "file path"), _p("anchor", "string", "exact text of the line to insert after"),
              _p("text", "string", "lines to insert")], insert_after),
        Tool("delete_file", "Delete a file.", [_p("path", "string", "file path")], edit("delete_file")),
        Tool("run_command", "Run a shell command in the workspace; returns exit code and output.",
             [_p("cmd", "string", "shell command")], lambda cmd: shell(ledger.run_command(cmd))),
        Tool("run_tests", "Run pytest; returns exit code and output.",
             [_p("args", "string", "pytest arguments, e.g. test_x.py -k name", True)], lambda args="": shell(run_tests(ledger, _test_args(ledger, _str(args, "args"))))),
        Tool("check_syntax", "Check that a Python file (or every Python file) can be parsed; returns ok, or each error with its line. Run it after changing code.",
             [_p("path", "string", "file path; leave empty to check all Python files", True)], check_syntax),
    ]
    if web is not None:
        tools += [Tool("web_search", "Search the web; returns titles, URLs and snippets.", [_p("query", "string", "what to look up")],
                       lambda query: logged("web_search", query, {"query": query}, lambda: web.search(query))),
                  Tool("fetch_url", "Download a web page and return its text.", [_p("url", "string", "page address")],
                       lambda url: logged("fetch_url", url, {"url": url}, lambda: web.fetch(url), limit=2000))]
    return tools
