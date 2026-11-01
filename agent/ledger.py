"""Append-only action ledger. Every file mutation is recorded with its exact diff and its inverse."""
import difflib
import hashlib
import io
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .envstate import env_state, reach, valid_state
from .runner import LocalExecutor, check_command, pytest_summary


class ActionError(Exception):
    pass


class ConflictError(ActionError):
    pass


_UNSET = object()
_SCHEMA = """CREATE TABLE IF NOT EXISTS actions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, action TEXT, args TEXT, status TEXT, error TEXT,
    path TEXT, pre_hash TEXT, post_hash TEXT, start_line INTEGER, removed TEXT, added TEXT,
    inverse TEXT, reverts INTEGER, reverted_by INTEGER, depth INTEGER,
    cause INTEGER, replay_of INTEGER, exit_code INTEGER, output TEXT, duration REAL,
    agent TEXT, task_id TEXT, kind TEXT, element TEXT, iteration INTEGER)"""
_SKIP_DIRS = {".agent", ".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
_MAX_BYTES = 1_000_000
_JUNK_FILES = {".DS_Store"}   # macOS Finder metadata: never tracked, never part of a build


def _check_args(args):
    """Reject anything the atomic actions cannot handle safely: wrong types, bad unicode, oversized text."""
    if not isinstance(args, dict):
        raise ActionError("args must be an object")
    for k in ("path", "content", "text", "expected"):
        if k in args:
            if not isinstance(args[k], str):
                raise ActionError(f"{k} must be a string")
            try:
                size = len(args[k].encode())
            except UnicodeEncodeError:
                raise ActionError(f"{k} is not valid unicode text") from None
            if size > _MAX_BYTES:
                raise ActionError(f"{k} is larger than {_MAX_BYTES} bytes")
    for k in ("at_line", "start", "end"):
        if k in args and type(args[k]) is not int:
            raise ActionError(f"{k} must be an integer")


def _lines(text):
    return io.StringIO(text).readlines()  # splits on "\n" only


def _plan(action, a, old):
    """Pure: return (new_text, removed, added, start_line, (inverse_action, inverse_args)). `old`/`new` None = no file."""
    p = a["path"]
    if action == "create_file":
        if old is not None:
            raise ActionError(f"{p} already exists")
        return a["content"], "", a["content"], 1, ("delete_file", {"path": p})
    if old is None:
        raise ActionError(f"{p} does not exist")
    if action == "delete_file":
        return None, old, "", 1, ("create_file", {"path": p, "content": old})
    lines = _lines(old)
    if action == "insert_lines":
        start, k = a["at_line"], 0
        if not 1 <= start <= len(lines) + 1:
            raise ActionError(f"at_line {start} out of range (file has {len(lines)} lines)")
    elif action in ("delete_lines", "replace_lines"):
        start, end = a["start"], a["end"]
        if not 1 <= start <= end <= len(lines):
            raise ActionError(f"lines {start}-{end} out of range (file has {len(lines)} lines)")
        k = end - start + 1
    else:
        raise ActionError(f"unknown action {action}")
    removed = "".join(lines[start - 1:start - 1 + k])
    if "expected" in a and a["expected"] != removed:
        raise ActionError("expected text does not match the file")
    text = "" if action == "delete_lines" else a["text"]
    if action != "delete_lines" and not text:
        raise ActionError("text is empty")
    new_lines = lines[:start - 1] + _lines(text) + lines[start - 1 + k:]
    if any(not l.endswith("\n") for l in new_lines[:-1]):
        raise ActionError("edit would join lines; text must end with a newline")
    m = len(_lines(text))
    inverse = {
        "insert_lines": ("delete_lines", {"path": p, "start": start, "end": start + m - 1, "expected": text}),
        "delete_lines": ("insert_lines", {"path": p, "at_line": start, "text": removed}),
        "replace_lines": ("replace_lines", {"path": p, "start": start, "end": start + m - 1, "text": removed, "expected": text}),
    }[action]
    return "".join(new_lines), removed, text, start, inverse


def diff_action(p, old, new):
    """The one atomic action that turns `old` into `new` (None = no file); lines shared at both ends are left out."""
    if old is None:
        return "create_file", {"path": p, "content": new}
    if new is None:
        return "delete_file", {"path": p}
    a, b = _lines(old), _lines(new)
    head = 0
    while head < min(len(a), len(b)) and a[head] == b[head]:
        head += 1
    tail = 0
    while tail < min(len(a), len(b)) - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    cut, add = "".join(a[head:len(a) - tail]), "".join(b[head:len(b) - tail])
    n = len(a) - tail - head
    if not cut:
        return "insert_lines", {"path": p, "at_line": head + 1, "text": add}
    if not add:
        return "delete_lines", {"path": p, "start": head + 1, "end": head + n, "expected": cut}
    return "replace_lines", {"path": p, "start": head + 1, "end": head + n, "text": add, "expected": cut}


class Ledger:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.dir = self.root / ".agent"
        (self.dir / "blobs").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dir / "ledger.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute(_SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(actions)")}
        for col, type_ in (("agent", "TEXT"), ("task_id", "TEXT"), ("kind", "TEXT"), ("element", "TEXT"), ("iteration", "INTEGER")):
            if col not in have:   # ledgers created before this column existed
                self.db.execute(f"ALTER TABLE actions ADD COLUMN {col} {type_}")
        self._scope = {"agent": "root", "task_id": None, "kind": None, "iteration": None}
        self._recover()

    # ---- file helpers ----
    @staticmethod
    def _hash(text):
        return None if text is None else hashlib.sha256(text.encode()).hexdigest()

    def _resolve(self, path):
        try:
            full = (self.root / path).resolve()
        except (ValueError, OSError, RuntimeError):
            raise ActionError(f"invalid path: {path!r}") from None
        if len(full.name.encode()) > 255:
            raise ActionError(f"name too long: {path}")
        if not full.is_relative_to(self.root) or full == self.root or any(p.exists() and p.samefile(self.dir) for p in (full, *full.parents)):
            raise ActionError(f"path outside workspace: {path}")  # samefile also catches case aliases like .AGENT
        if any(p.exists() and not p.is_dir() for p in full.parents):
            raise ActionError(f"cannot create {path}: a parent is a file")
        return full.relative_to(self.root).as_posix(), full

    @staticmethod
    def _read(full):
        if not full.exists():
            return None
        try:
            return full.read_bytes().decode()
        except (IsADirectoryError, UnicodeDecodeError) as e:
            raise ActionError(f"{full.name} is not a utf-8 text file") from e

    def _write(self, full, text):
        if text is None:
            full.unlink()
            for d in full.parents:  # prune directories the file leaves empty
                if d == self.root:
                    break
                try:
                    d.rmdir()
                except OSError:
                    break
            return
        full.parent.mkdir(parents=True, exist_ok=True)
        tmp = full.with_name(full.name + ".tmp")
        tmp.write_bytes(text.encode())
        os.replace(tmp, full)

    def _put(self, text):
        if text is not None:
            blob = self.dir / "blobs" / self._hash(text)
            if not blob.exists():
                blob.write_bytes(text.encode())

    def _blob(self, h):
        return None if h is None else (self.dir / "blobs" / h).read_bytes().decode()

    # ---- db helpers ----
    @contextmanager
    def scope(self, agent=None, task=None, kind=None, iteration=None):
        """Everything recorded inside is attributed to this agent, task (e.g. '1.2'), kind (e.g. 'research') and attempt number."""
        old = self._scope
        self._scope = {"agent": agent or old["agent"], "task_id": task or old["task_id"], "kind": kind or old["kind"],
                       "iteration": old["iteration"] if iteration is None else iteration}
        try:
            yield
        finally:
            self._scope = old

    def _insert(self, **c):
        c = {**self._scope, **c}
        with self.db:
            sql = f"INSERT INTO actions({','.join(c)}) VALUES ({','.join('?' * len(c))})"
            return self.db.execute(sql, list(c.values())).lastrowid

    def _finalize(self, seq, reverts):
        with self.db:
            self.db.execute("UPDATE actions SET status='ok' WHERE seq=?", (seq,))
            if reverts:
                self.db.execute("UPDATE actions SET reverted_by=? WHERE seq=?", (seq, reverts))

    def _recover(self):
        for r in self.db.execute("SELECT * FROM actions WHERE status='pending'").fetchall():
            if r["path"] is None:  # a command that never finished
                with self.db:
                    self.db.execute("UPDATE actions SET status='error', error=? WHERE seq=?",
                                    ("crashed before it finished; what it changed may not be fully recorded", r["seq"]))
                continue
            cur = self._hash(self._read(self.root / r["path"]))
            if cur == r["post_hash"]:
                self._finalize(r["seq"], r["reverts"])
            else:
                why = "crashed before the edit was applied" if cur == r["pre_hash"] else "crashed; file in an unexpected state"
                with self.db:
                    self.db.execute("UPDATE actions SET status='error', error=? WHERE seq=?", (why, r["seq"]))

    def get(self, seq):
        if type(seq) is not int:
            raise ActionError(f"no action {seq!r}")
        r = self.db.execute("SELECT * FROM actions WHERE seq=?", (seq,)).fetchone()
        if r is None:
            raise ActionError(f"no action {seq}")
        return r

    def list(self):
        return self.db.execute("SELECT * FROM actions ORDER BY seq").fetchall()

    def log_read(self, action, element, args, result="", error=None):
        """Record a read-only action (grep, read_file, web_search...) so the ledger shows everything the agent did."""
        self._put(result if isinstance(result, str) else None)
        return self._insert(ts=time.time(), action=str(action), args=json.dumps(args, default=repr), status="rejected" if error else "ok",
                            error=error, output=self._hash(result) if isinstance(result, str) else None, element=str(element)[:200])

    def log_session(self, result):
        """Record how much of the context window a model session used, and the error it ended with, if any."""
        if "tokens" in result:
            self.log_read("session", self._scope["kind"] or "-", {"tokens": result["tokens"], "context": result.get("context"),
                                                                    "tool_calls": len(result.get("calls", []))})
        if "error" in result:
            self.log_read("model_error", result["error"], {"message": str(result.get("message", ""))[:200]}, error=result["error"])

    def trail(self, task, agent=None):
        """Every record made under `task` or any task nested below it ('1' includes '1.2' and '1.2.3'), oldest first."""
        sql = "SELECT * FROM actions WHERE (task_id = ? OR task_id LIKE ?)" + (" AND agent = ?" if agent else "") + " ORDER BY seq"
        return self.db.execute(sql, (task, f"{task}.%", *([agent] if agent else []))).fetchall()

    def edits(self, agent, task, iteration=None):
        """File edits still in force that `agent` made on exactly `task` (during one attempt, if given), oldest first."""
        sql = ("SELECT * FROM actions WHERE status='ok' AND reverted_by IS NULL AND depth = 0 AND path IS NOT NULL AND agent = ? AND task_id = ?"
               + (" AND iteration = ?" if iteration is not None else "") + " ORDER BY seq")
        return self.db.execute(sql, (agent, task, *([iteration] if iteration is not None else []))).fetchall()

    def undo_task(self, agent, task, iteration=None):
        """Undo the edits still in force that `agent` made on exactly `task` (in one attempt, if given), newest first."""
        return [self.undo(r["seq"]) for r in reversed(self.edits(agent, task, iteration))]

    # ---- actions ----
    def _prepare(self, action, args, expect_pre):
        try:
            _check_args(args)
            rel, full = self._resolve(args["path"])
            old = self._read(full)
            if expect_pre is not _UNSET and self._hash(old) != expect_pre:
                raise ConflictError(f"{rel} changed since the action being undone")
            planned = _plan(action, {**args, "path": rel}, old)
            if planned[0] is not None and len(planned[0].encode()) > _MAX_BYTES:
                raise ActionError(f"resulting file would be larger than {_MAX_BYTES} bytes")
            return (rel, old, *planned)
        except (KeyError, TypeError) as e:
            raise ActionError(f"bad arguments: {e!r}") from e

    def do(self, action, args, reverts=None, expect_pre=_UNSET):
        """Apply one atomic action and record it. Returns its seq; raises ActionError (recorded as 'rejected')."""
        if action == "env_change":
            return self._do_env(args, reverts, expect_pre)
        try:
            rel, old, new, removed, added, start, inv = self._prepare(action, args, expect_pre)
        except ActionError as e:
            self._insert(ts=time.time(), action=str(action), args=json.dumps(args, default=repr), status="rejected", error=str(e), reverts=reverts)
            raise
        return self._commit(action, args, (rel, old, new, removed, added, start, inv), reverts)

    def _commit(self, action, args, prepared, reverts=None, cause=None, write=True):
        """Record a prepared change (pending), apply it to disk unless the change already happened, then mark it ok."""
        rel, old, new, removed, added, start, inv = prepared
        self._put(old)
        self._put(new)
        seq = self._insert(
            ts=time.time(), action=action, args=json.dumps(args), status="pending", path=rel, element=rel,
            pre_hash=self._hash(old), post_hash=self._hash(new), start_line=start, removed=removed, added=added,
            inverse=json.dumps({"action": inv[0], "args": inv[1]}), reverts=reverts,
            depth=0 if reverts is None else self.get(reverts)["depth"] + 1, cause=cause)
        if write:
            try:
                self._write(self.root / rel, new)
            except OSError as e:  # should have been caught by validation; never leave a pending row behind
                with self.db:
                    self.db.execute("UPDATE actions SET status='error', error=? WHERE seq=?", (str(e), seq))
                raise ActionError(f"could not write {rel}: {e}") from e
        self._finalize(seq, reverts)
        return seq

    @staticmethod
    def _state_hash(state):
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    def _env_row(self, before, after, depth=0, **cols):
        return self._insert(ts=time.time(), action="env_change", args=json.dumps({"before": before, "after": after}),
                            pre_hash=self._state_hash(before), post_hash=self._state_hash(after), depth=depth, element="env",
                            inverse=json.dumps({"action": "env_change", "args": {"before": after, "after": before}}), **cols)

    def _do_env(self, args, reverts, expect_pre):
        """Bring the environment to args['after'] (pip/uv/npm/rmtree) and record it; the inverse swaps before and after."""
        try:
            if not isinstance(args, dict) or set(args) != {"before", "after"} or not all(valid_state(args[k]) for k in args):
                raise ActionError("env_change needs valid before and after states")
            if expect_pre is not _UNSET and self._state_hash(env_state(self.root)) != expect_pre:
                raise ConflictError("the environment changed since the action being undone")
        except ActionError as e:
            self._insert(ts=time.time(), action="env_change", args=json.dumps(args, default=repr), status="rejected", error=str(e), reverts=reverts)
            raise
        seq = self._env_row(args["before"], args["after"], 0 if reverts is None else self.get(reverts)["depth"] + 1,
                            status="pending", reverts=reverts)
        try:
            reached, log = reach(self.root, args["after"], LocalExecutor())
        except (ValueError, OSError) as e:
            reached, log = False, str(e)
        if not reached:
            with self.db:
                self.db.execute("UPDATE actions SET status='error', error=? WHERE seq=?", (log[-500:], seq))
            raise ActionError(f"could not bring the environment to the recorded state: {log[-300:]}")
        self._put(log)
        self._finalize(seq, reverts)
        return seq

    def undo(self, seq):
        r = self.get(seq)
        if r["status"] != "ok" or r["reverted_by"] is not None or r["inverse"] is None:
            raise ActionError(f"action {seq} cannot be undone")
        inv = json.loads(r["inverse"])
        return self.do(inv["action"], inv["args"], reverts=seq, expect_pre=r["post_hash"])

    def _live(self, undos):
        """Seqs of records not yet reverted, newest first. Depth is 0 for an action and +1 per revert of it, so
        even depth = an effect in force (undo_last/rewind target it), odd = an undo that `redo` can reverse."""
        rows = self.db.execute("SELECT seq FROM actions WHERE status='ok' AND reverted_by IS NULL AND depth % 2 = ? ORDER BY seq DESC",
                               (int(undos),))
        return [r["seq"] for r in rows]

    def undo_last(self):
        seqs = self._live(undos=False)
        if not seqs:
            raise ActionError("nothing to undo")
        return self.undo(seqs[0])

    def redo(self):
        seqs = self._live(undos=True)
        if not seqs:
            raise ActionError("nothing to redo")
        return self.undo(seqs[0])

    def rewind(self, to_seq):
        """Undo every effect in force after `to_seq`, newest first."""
        return [self.undo(s) for s in self._live(undos=False) if s > to_seq]

    def verify(self):
        """Replay the ledger forward from each file's first recorded state; return a list of problems."""
        state, problems = {}, []
        for r in self.db.execute("SELECT * FROM actions WHERE status='ok' AND path IS NOT NULL ORDER BY seq"):
            p = r["path"]
            state.setdefault(p, self._blob(r["pre_hash"]))
            try:
                new = _plan(r["action"], {**json.loads(r["args"]), "path": p}, state[p])[0]
            except ActionError as e:
                problems.append(f"seq {r['seq']}: replay failed: {e}")
                new = self._blob(r["post_hash"])
            if self._hash(state[p]) != r["pre_hash"] or self._hash(new) != r["post_hash"]:
                problems.append(f"seq {r['seq']}: hashes do not match the recorded states")
                new = self._blob(r["post_hash"])
            state[p] = new
        for p, text in state.items():
            if self._read(self.root / p) != text:
                problems.append(f"{p}: file on disk differs from the ledger")
        return problems

    # ---- commands ----
    def _snapshot(self):
        """Every utf-8 text file (<= 1MB, not linked from outside the workspace) outside tool/cache directories: {relative path: text}."""
        files = {}
        for d, dirs, names in os.walk(self.root):
            dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
            for n in names:
                if n in _JUNK_FILES or n.startswith("._"):   # .DS_Store and AppleDouble files
                    continue
                f = Path(d, n)
                try:
                    if f.is_file() and f.resolve().is_relative_to(self.root) and f.stat().st_size <= _MAX_BYTES:
                        files[f.relative_to(self.root).as_posix()] = f.read_bytes().decode()
                except (UnicodeDecodeError, OSError):
                    pass
        return files

    def run_command(self, cmd, timeout=120, executor=None, replay_of=None):
        """Run a shell command and record it. Every text-file change it made is recorded as an ordinary,
        undoable action (cause = this command's seq). Not undoable itself, but `replay` re-runs it."""
        why = check_command(cmd, timeout)
        if why:
            self._insert(ts=time.time(), action="run_command", args=json.dumps({"cmd": cmd, "timeout": timeout}, default=repr),
                         status="rejected", error=why)
            raise ActionError(why)
        before, env_before = self._snapshot(), env_state(self.root)
        seq = self._insert(ts=time.time(), action="run_command", args=json.dumps({"cmd": cmd, "timeout": timeout}),
                           status="pending", replay_of=replay_of, element=cmd[:200])
        code, out, secs, err = (executor or LocalExecutor()).run(cmd, self.root, timeout)
        after = self._snapshot()
        for p in sorted(before.keys() | after.keys()):
            if before.get(p) != after.get(p):
                action, args = diff_action(p, before.get(p), after.get(p))
                prepared = (p, before.get(p), *_plan(action, args, before.get(p)))
                assert prepared[2] == after.get(p), f"observed change to {p} was not reproduced"
                self._commit(action, args, prepared, cause=seq, write=False)
        env_after = env_state(self.root)
        if env_after != env_before:
            self._env_row(env_before, env_after, status="ok", cause=seq)
        self._put(out)
        with self.db:
            self.db.execute("UPDATE actions SET status='ok', exit_code=?, output=?, duration=?, error=? WHERE seq=?",
                            (code, self._hash(out), secs, err, seq))
        return seq

    def _command(self, seq):
        r = self.get(seq)
        if r["action"] != "run_command":
            raise ActionError(f"action {seq} is not a command")
        return r

    def output(self, seq):
        """What a command printed, or what a logged read returned, in full."""
        return self._blob(self.get(seq)["output"]) or ""

    def replay(self, seq, executor=None):
        """Run a recorded command again against the current tree; the new record links back to the original."""
        args = json.loads(self._command(seq)["args"])
        return self.run_command(args["cmd"], args["timeout"], executor, replay_of=seq)

    def diff(self, a, b):
        """Compare two recorded command runs: exit codes, pytest summaries, and output (timings ignored)."""
        self._command(a), self._command(b)
        raw = [self.output(a), self.output(b)]
        ta, tb = (re.sub(r"\d+\.\d+s", "<t>", t) for t in raw)
        head = [f"exit code: {self.get(a)['exit_code']} -> {self.get(b)['exit_code']}",
                f"pytest: {pytest_summary(raw[0])} -> {pytest_summary(raw[1])}"]
        return "\n".join(head + list(difflib.unified_diff(ta.splitlines(), tb.splitlines(), f"#{a}", f"#{b}", lineterm="")))
