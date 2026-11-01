"""The worker loop. One fresh model session per attempt; between attempts the only thing carried over is a short failure summary.
The harness runs the tests and judges the evidence (agent/verify.py), so the model cannot declare itself done."""
import json

from .evidence import gather
from .handoff import BUDGET, MAX_CONTINUATIONS, continuation_depth, looked_at, note
from .ledger import ActionError
from .memory import Task
from .research import research_step
from .spec import ensure_spec_test, join_examples, module_of, propose_examples
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
    return [r for r in ledger.edits(agent, task_id, iteration)
            if not r["path"].endswith("MEMORY.md") and r["iteration"] != 0]   # bookkeeping and the harness's own spec tests are not the model's work


def work_task(fm, ledger, memory, task, agent="root", web=None, max_iterations=4, max_tool_calls=12, say=None, propose=False):
    """Carry out one code task. Returns {'ok', 'summary', 'evidence'}; on failure the task's edits are rolled back (redo restores them)."""
    say = say or (lambda text: None)
    ledger.sync_external()                           # files you changed by hand since the last record are noted, not refused
    text = f"{task.title} {task.detail} {task.done_when}"
    args = acceptance_args(task.done_when)
    try:   # a step that gives examples gets its tests written by the harness, before the model starts (see agent/spec.py)
        with ledger.scope(task=task.id, kind="test", iteration=0):
            spec_path = ensure_spec_test(ledger, text, f"{task.done_when};{task.detail}")
    except ActionError:
        spec_path = None
    if spec_path is None and propose and module_of(text):   # no examples given: ask the model for some, keep them with the task, and test against them
        examples = join_examples(propose_examples(fm, f"{task.title}. {task.detail}"))
        if examples:
            try:
                with ledger.scope(task=task.id, kind="test", iteration=0):
                    spec_path = ensure_spec_test(ledger, text, examples)
                    ledger.log_read("examples", task.id, {"proposed": True}, examples)
                memory.set_done_when(task.id, examples)
                say("  examples proposed: " + examples[:140])
            except ActionError:
                pass
    start_files = ledger._snapshot()                                               # the project as it is before any work
    written = ledger.written_by_tasks()
    originals = {p: t for p, t in start_files.items() if is_test_path(p) and p not in written}   # tests an earlier task wrote may be extended
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

    earlier = memory.handoff(task.id)                # a note left by an agent that ran out of room on this task or its parent

    def prompt_for(failure, lean, it, tight=False):
        head = [f"Task {task.id}: {task.title}", task.detail, f"Done when: {('pytest ' + args).strip()} passes with at least one test"]
        if spec_path:
            head.append(f"The tests are already written in {spec_path}. Write only the code that makes them pass, and do not edit that file.")
        if earlier:
            head.append("Handoff from the agent before you:\n" + earlier)
        if tight:
            head.append("The last session ran out of room. Do ONE small piece, call run_tests, and stop.")
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
    last, stalls, lean, budget_hit = ["nothing was attempted"], 0, False, False
    for it in range(1, max_iterations + 1):
        with ledger.scope(task=task.id, kind="code", iteration=it):
            r = fm.run(prompt_for(failure, lean, it, budget_hit), tools, INSTRUCTIONS, max_tool_calls=max_tool_calls, budget_tokens=BUDGET)
            ledger.log_session(r)
        if r.get("reason") == "budget":
            budget_hit = True
            say(f"  attempt {it}: used {r.get('estimated') or r.get('tokens')} of {r.get('context', 8192)} tokens: wound down")
        if "error" in r:
            lean = lean or r["error"] == "context_overflow"
            budget_hit = budget_hit or r["error"] == "context_overflow"   # the hard limit was hit before the soft one: the same problem
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
            frozen_ok, why = frozen_check(ledger.root, originals, args=args)
            if not frozen_ok:
                verdict.ok, verdict.reasons, custom = False, [f"the original tests, unchanged, fail against your code:\n{why}"], True
        if verdict.ok and baseline.ok and any(not is_test_path(r["path"]) for r in mine):   # code changed on a green project: the new tests must start red
            red_ok, why = red_check(ledger.root, start_files)
            if not red_ok:
                verdict.ok, verdict.reasons, custom = False, [why], True
        why = "; ".join(verdict.reasons)
        new = why if custom else failure_summary(out) or why
        record(it, verdict.ok, seq, why if new == why else f"{why} | {new}")   # the ledger row says what pytest reported, not only that it failed
        if verdict.ok:
            files = sorted({r["path"] for r in _edits_of_the_task(ledger, agent, task.id)})
            return {"ok": True, "summary": f"verified after {it} attempt(s); changed {', '.join(files)}",
                    "evidence": f"run #{seq}: pytest exit 0, {verdict.summary.get('passed')} passed"}
        stalls = stalls + 1 if (not rows and new == failure) else 0
        failure, last = new, verdict.reasons
        if stalls >= 2:
            break
    if budget_hit and continuation_depth(task.title) < MAX_CONTINUATIONS:
        handed = _hand_off(ledger, memory, task, agent, originals, last, failure, say)
        if handed:
            return handed
    ledger.undo_task(agent, task.id)
    return {"ok": False, "summary": f"not verified after {it} attempt(s): {'; '.join(last)[:200]}; edits rolled back"}


def _hand_off(ledger, memory, task, agent, originals, problems, failure, say):
    """The task did not fit in the context window. Keep the partial work if it is safe (it parses and the original tests still pass),
    write down what is done and what is left, and queue a continuation task. Returns the step result, or None if that is not possible."""
    mine = _edits_of_the_task(ledger, agent, task.id)
    safe = (not syntax_errors(ledger.root, [r["path"] for r in mine]) and not protected_violations(ledger.root, originals)
            and frozen_check(ledger.root, originals, args=acceptance_args(task.done_when))[0])
    done = ", ".join(sorted({r["path"] for r in mine})) if safe and mine else ""
    if not safe:
        ledger.undo_task(agent, task.id)
    text = note(task, done=done, problem=failure or "; ".join(problems), looked=looked_at(ledger, agent, task.id))
    try:
        memory.add_children(task.id, [Task(f"Continue: {task.title}", f"Finish what the handoff for {task.id} says is left.", task.done_when, kind="code")])
    except ActionError:
        return None   # the task is not in MEMORY.md (a one-off run): there is nowhere to queue the rest
    memory.add_handoff(task.id, text)
    with ledger.scope(task=task.id, kind="code"):
        ledger.log_read("handoff", task.id, {"kept": bool(done)}, text)
    say(f"  handed off: {'partial work kept' if done else 'partial work rolled back'}; the rest is a new task")
    return {"ok": True, "summary": f"handed off: ran out of room ({'work kept' if done else 'work rolled back'}); the rest is queued as a new task"}


def _fits(fm, tools, prompt):
    count = getattr(fm, "count", None)
    if count is None:
        return True
    c = count(tools, INSTRUCTIONS, prompt)
    return c["tools"] + c["instructions"] + c["prompt"] + RESERVE <= c.get("context", 8192)


def solve_step(fm, web=None, say=None):
    """The step for agents.task_runner: research tasks are researched, code tasks are worked and verified."""
    return research_step(fm, web, worker=lambda ctx, task: work_task(fm, ctx.ledger, ctx.memory, task, agent=ctx.id, web=web, say=say))
