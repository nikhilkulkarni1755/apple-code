"""The apple-code command line."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from .agents import AgentQueue
from .ledger import ActionError, Ledger
from .runner import run_tests


def _show(r):
    print(f"#{r['seq']} {r['status']} {r['action']} {r['path'] or ''}" + (f" (reverts #{r['reverts']})" if r["reverts"] else "")
          + (f" [undone by #{r['reverted_by']}]" if r["reverted_by"] else "")
          + (f" (by command #{r['cause']})" if r["cause"] else "") + (f" exit={r['exit_code']}" if r["action"] == "run_command" and r["status"] == "ok" else "") + (f"  {r['error']}" if r["error"] else ""))


def _installer():
    """The install.sh the installer left next to the program, or None when running from a source checkout."""
    path = Path(sys.prefix).parent / "install.sh"
    return path if path.is_file() and (path.parent / ".apple-code-home").exists() else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="apple-code", description="A local coding agent for Apple's on-device model. Run it inside a project folder.")
    ap.add_argument("--version", action="version", version=f"apple-code {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check that this machine can run apple-code")
    sub.add_parser("update", help="install the latest version")
    sub.add_parser("uninstall", help="remove apple-code (your projects are left alone)")
    d = sub.add_parser("do", help="apply one action: do ACTION JSON_ARGS")
    d.add_argument("action")
    d.add_argument("args")
    c = sub.add_parser("run", help="run a shell command; its file changes are recorded")
    c.add_argument("command")
    t = sub.add_parser("test", help="run pytest, e.g. test 'tests/test_x.py -k name'")
    t.add_argument("args", nargs="?", default="")
    r_ = sub.add_parser("replay", help="re-run a recorded command")
    r_.add_argument("seq", type=int)
    df = sub.add_parser("diff", help="compare two recorded command runs")
    df.add_argument("a", type=int)
    df.add_argument("b", type=int)
    sv = sub.add_parser("solve", help="research a goal, plan it, and carry out the code tasks with the on-device model")
    sv.add_argument("goal")
    sub.add_parser("agents", help="list agents and their state")
    sub.add_parser("recap", help="what every agent did, how it went, and what was noticed")
    tr = sub.add_parser("trail", help="everything done under a task (and the tasks nested below it)")
    tr.add_argument("task")
    tr.add_argument("--agent")
    sub.add_parser("list")
    s = sub.add_parser("show", help="show an action with its removed/added lines")
    s.add_argument("seq", type=int)
    u = sub.add_parser("undo", help="undo SEQ, the last action, or everything after --to N")
    u.add_argument("seq", type=int, nargs="?")
    u.add_argument("--to", type=int)
    sub.add_parser("redo")
    sub.add_parser("verify")
    a = ap.parse_args(argv)
    if a.cmd == "doctor":
        from .doctor import report
        return report()
    if a.cmd in ("update", "uninstall"):
        installer = _installer()
        if installer is None:
            print(f"apple-code was not installed by the installer; {a.cmd} does not apply to a source checkout.", file=sys.stderr)
            return 1
        return subprocess.run(["sh", str(installer), "--home", str(installer.parent)] + (["--uninstall"] if a.cmd == "uninstall" else [])).returncode
    ledger = Ledger(".")
    try:
        if a.cmd == "do":
            print(f"#{ledger.do(a.action, json.loads(a.args))}")
        elif a.cmd in ("run", "test", "replay"):
            seq = {"run": lambda: ledger.run_command(a.command), "test": lambda: run_tests(ledger, a.args),
                   "replay": lambda: ledger.replay(a.seq)}[a.cmd]()
            print(f"#{seq} exit={ledger.get(seq)['exit_code']}")
        elif a.cmd == "solve":
            from .agents import task_runner
            from .fm import FM
            from .memory import Memory
            from .web import Web
            from .worker import solve_step
            fm = FM()
            try:
                say = lambda text: print(text, flush=True)
                queue = AgentQueue(ledger, task_runner(solve_step(fm, Web(), say), say=say))
                queue.start(a.goal)
                for r in queue.run():
                    print(f"{r['id']:5s} {r['status']:8s} {r['summary'] or r['error'] or ''}")
                print(Memory(ledger).get("Current state"))
                from .recap import write
                print("\n" + write(ledger, queue, Memory(ledger)))
            finally:
                fm.close()
        elif a.cmd == "recap":
            from .memory import Memory
            from .recap import build
            print(build(ledger, AgentQueue(ledger, None, resume=False), Memory(ledger)))
        elif a.cmd == "agents":
            for r in AgentQueue(ledger, None, resume=False).agents():
                print(f"{r['id']:5s} {r['status']:8s} tries={r['attempts']} parent={r['parent'] or '-':5s} {r['goal'][:70]}")
        elif a.cmd == "trail":
            for r in ledger.trail(a.task, a.agent):
                print(f"#{r['seq']} {r['agent']} task {r['task_id']} [{r['kind'] or '-'}] {r['action']} {r['element'] or ''} {r['status']}")
        elif a.cmd == "diff":
            print(ledger.diff(a.a, a.b))
        elif a.cmd == "list":
            for r in ledger.list():
                _show(r)
        elif a.cmd == "show":
            r = ledger.get(a.seq)
            _show(r)
            if r["action"] == "run_command":
                print(f"$ {json.loads(r['args'])['cmd']}\n{ledger.output(a.seq)}", end="")
                return 0
            print(f"@@ {r['path']}:{r['start_line']} @@")
            print("".join(f"-{l}" for l in (r["removed"] or "").splitlines(True)), end="")
            print("".join(f"+{l}" for l in (r["added"] or "").splitlines(True)), end="")
        elif a.cmd == "undo":
            seqs = ledger.rewind(a.to) if a.to is not None else [ledger.undo(a.seq) if a.seq else ledger.undo_last()]
            print("undone by " + ", ".join(f"#{s}" for s in seqs))
        elif a.cmd == "redo":
            print(f"redone by #{ledger.redo()}")
        elif a.cmd == "verify":
            problems = ledger.verify()
            print("\n".join(problems) or "ledger matches the workspace")
            return 1 if problems else 0
    except ActionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
