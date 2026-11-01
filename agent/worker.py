"""The worker loop. One fresh model session per attempt; between attempts the only thing carried over is a short failure summary.
The harness runs the tests and judges the evidence (agent/verify.py), so the model cannot declare itself done."""
import json

from .evidence import gather
from .research import research_step
from .runner import run_tests
from .tools import make_tools, visible_files
from .verify import (acceptance_args, failure_summary, frozen_check, is_test_path, judge, protected_violations, red_check, syntax_errors,
                     unmentioned_deletions, vacuous_tests)

INSTRUCTIONS = ("You are a coding agent. Do the task by editing files with the tools. Write or extend a test that proves it works. "
                "Use replace_text with the exact existing text, insert_after, or create_file. Never change existing tests. "
                "Call run_tests when you are done, then reply DONE.")
TOOLS = ("read_file", "list_files", "grep", "create_file", "replace_text", "insert_after", "run_tests")
RESERVE = 2500   # tokens left free for tool results and the answer


def _edits_of_the_task(ledger, agent, task_id, iteration=None):
    return [r for r in ledger.edits(agent, task_id, iteration) if not r["path"].endswith("MEMORY.md")]   # bookkeeping is not work


def work_task(fm, ledger, memory, task, agent="root", web=None, max_iterations=4, max_tool_calls=12, say=None):
    """Carry out one code task. Returns {'ok', 'summary', 'evidence'}; on failure the task's edits are rolled back (redo restores them)."""
    say = say or (lambda text: None)
    text = f"{task.title} {task.detail} {task.done_when}"
    args = acceptance_args(task.done_when)
    start_files = ledger._snapshot()                                               # the project as it is before any work
    originals = {p: t for p, t in start_files.items() if is_test_path(p)}
    roots = {p.split("/")[0].removesuffix(".py") for p in start_files if p.endswith(".py") and not is_test_path(p)}
    names = TOOLS + (("delete_file",) if any(w in text.lower() for w in ("delete", "remove")) else ()) + (("web_search", "fetch_url") if web else ())
    tools = [t for t in make_tools(ledger, web) if t.name in names]

    facts = gather(ledger, text)                     # what is in the project, found by the harness: no model tokens spent reading files
    with ledger.scope(task=task.id, kind="code"):
        ledger.log_read("evidence", task.id, {"chars": len(facts)}, facts)

    def run_acceptance(iteration):
        with ledger.scope(task=task.id, kind="test", iteration=iteration):
            seq = run_tests(ledger, args)
        r = ledger.get(seq)
        return seq, r["exit_code"], ledger.output(seq), r["error"]

    def record(iteration, verdict, seq, extra=""):
        say(f"  attempt {iteration}: " + ("VERIFIED" if verdict else "rejected - " + " ".join((extra or "not verified").split())[:140]))
        with ledger.scope(task=task.id, kind="test", iteration=iteration):
            ledger.log_read("verify", task.id, {"iteration": iteration, "ok": verdict, "run": seq},
                            error=None if verdict else (extra or "not verified"))

    def prompt_for(failure, lean, it):
        head = [f"Task {task.id}: {task.title}", task.detail, f"Done when: {('pytest ' + args).strip()} passes with at least one test"]
        extra = [] if lean else [facts, "Notes:\n" + memory.context(900)]
        for parts, failed in ((head + extra, failure), (head, failure), (head, failure[:300])):   # full, then without notes, then minimal
            label = "The tests currently fail:" if it == 1 else "Your last attempt failed:"
            text = "\n".join(p for p in parts + ([label + "\n" + failed] if failed else []) if p)
            if _fits(fm, tools, text):
                break
        return text

    seq, code, out, err = run_acceptance(0)            # the "red" run: what the tests say before any work
    baseline = judge(args, code, out, err, edited=True)
    passed_before = baseline.summary.get("passed", 0)
    failure = "" if baseline.ok else failure_summary(out) or "; ".join(baseline.reasons)
    last, stalls, lean = ["nothing was attempted"], 0, False
    for it in range(1, max_iterations + 1):
        with ledger.scope(task=task.id, kind="code", iteration=it):
            r = fm.run(prompt_for(failure, lean, it), tools, INSTRUCTIONS, max_tool_calls=max_tool_calls)
            ledger.log_session(r)
        notes = []
        if "error" in r:
            lean = lean or r["error"] == "context_overflow"
            notes.append(f"the model stopped early ({r['error']})")
        rows = _edits_of_the_task(ledger, agent, task.id, it)
        bad = protected_violations(ledger.root, originals) + unmentioned_deletions(rows, text)
        if bad:
            ledger.undo_task(agent, task.id, it)
            failure = "Your edits were undone because you " + "; ".join(bad)
            record(it, False, None, failure)
            last = bad
            continue
        broken = syntax_errors(ledger.root, [r["path"] for r in rows])
        if broken:
            failure = "Syntax error: " + "; ".join(broken)
            record(it, False, None, failure)
            last = broken
            continue
        seq, code, out, err = run_acceptance(it)
        mine = _edits_of_the_task(ledger, agent, task.id)
        verdict = judge(args, code, out, err, edited=bool(mine), baseline_passed=passed_before)
        custom = False   # a failure found by the harness's own checks below, not shown by the test output
        if verdict.ok:
            roots_now = roots | {p.split("/")[0].removesuffix(".py") for p in {r["path"] for r in mine} if p.endswith(".py") and not is_test_path(p)}
            hollow = vacuous_tests(ledger.root, originals, sorted({r["path"] for r in mine if is_test_path(r["path"])}), roots_now)
            if hollow:
                verdict.ok, verdict.reasons, custom = False, hollow, True
        if verdict.ok:
            frozen_ok, why = frozen_check(ledger.root, originals)
            if not frozen_ok:
                verdict.ok, verdict.reasons, custom = False, [f"the original tests, unchanged, fail against your code:\n{why}"], True
        if verdict.ok and baseline.ok and any(not is_test_path(r["path"]) for r in mine):   # code changed on a green project: the new tests must start red
            red_ok, why = red_check(ledger.root, start_files)
            if not red_ok:
                verdict.ok, verdict.reasons, custom = False, [why], True
        record(it, verdict.ok, seq, "; ".join(verdict.reasons))
        if verdict.ok:
            files = sorted({r["path"] for r in _edits_of_the_task(ledger, agent, task.id)})
            return {"ok": True, "summary": f"verified after {it} attempt(s); changed {', '.join(files)}",
                    "evidence": f"run #{seq}: pytest exit 0, {verdict.summary.get('passed')} passed"}
        new = "; ".join(verdict.reasons) if custom else failure_summary(out) or "; ".join(verdict.reasons)
        stalls = stalls + 1 if (not rows and new == failure) else 0
        failure, last = new, verdict.reasons
        if stalls >= 2:
            break
    ledger.undo_task(agent, task.id)
    return {"ok": False, "summary": f"not verified after {it} attempt(s): {'; '.join(last)[:200]}; edits rolled back"}


def _fits(fm, tools, prompt):
    count = getattr(fm, "count", None)
    if count is None:
        return True
    c = count(tools, INSTRUCTIONS, prompt)
    return c["tools"] + c["instructions"] + c["prompt"] + RESERVE <= c.get("context", 8192)


def solve_step(fm, web=None, say=None):
    """The step for agents.task_runner: research tasks are researched, code tasks are worked and verified."""
    return research_step(fm, web, worker=lambda ctx, task: work_task(fm, ctx.ledger, ctx.memory, task, agent=ctx.id, web=web, say=say))
