"""The recap: what every agent did, how it went, and what was noticed along the way. Built from the ledger and MEMORY.md, so it is
facts, not a model's impression. `solve` writes it into MEMORY.md (section Recap) when it finishes."""
import json
import re
from collections import Counter

from .memory import _walk

# how a rejected attempt is described in the recap: (pattern in the verify record's error, label)
_REASONS = [("Syntax error", "syntax errors"), ("no tests were collected", "no tests found"), ("asserts nothing", "hollow tests (no assertion)"),
            ("does not use any code", "tests that never touch the project's code"), ("also pass on the code as it was", "tests that pass without the change"),
            ("changed existing test code", "existing tests altered"), ("deleted the existing test", "existing tests deleted"),
            ("original tests, unchanged", "broke the original tests"), ("does not mention", "files deleted that the task never named"),
            ("no new test passes", "no new passing test"), ("no files were changed", "nothing changed"), ("skipped", "skipped tests"),
            ("pytest exited", "tests failed")]


def _label(error):
    return next((label for pattern, label in _REASONS if re.search(pattern, error or "")), "other")


def build(ledger, queue, memory):
    rows = ledger.list()
    plan = [t for t in _walk(memory.tasks())]
    leaves = [t for t in plan if not t.children]
    verify = [r for r in rows if r["action"] == "verify"]
    sessions = [json.loads(r["args"]) for r in rows if r["action"] == "session" and r["status"] == "ok"]
    errors = Counter(r["element"] for r in rows if r["action"] == "model_error")
    attempts = Counter(r["task_id"] for r in verify)
    out = ["Recap"]

    states = Counter(t.status for t in leaves)
    out.append(f"Tasks: {states['done']} done, {states['blocked']} blocked, {states['todo'] + states['doing']} not finished, of {len(leaves)}.")
    for t in leaves:
        why = ""
        if t.status == "blocked":
            last = [r for r in verify if r["task_id"] == t.id and r["status"] == "rejected"]
            why = f" - last problem: {(last[-1]['error'] or '')[:110]}" if last else ""
        out.append(f"  {t.id} [{t.status}] {t.title} ({attempts.get(t.id, 0)} attempt(s)){why}")

    out.append("Agents: " + "; ".join(f"{a['id']} {a['status']}" + (f" ({a['summary'] or a['error']})" if (a['summary'] or a['error']) else "")
                                      for a in queue.agents()))

    files = sorted({r["path"] for r in rows if r["path"] and r["status"] == "ok" and r["depth"] == 0 and r["reverted_by"] is None
                    and not r["path"].endswith("MEMORY.md")})
    out.append("Files now in the project that the agent wrote or changed: " + (", ".join(files) or "none"))
    evidence = [r for r in verify if r["status"] == "ok"]
    out.append(f"Verified by the harness: {len(evidence)} task(s); pytest ran {sum(1 for r in rows if r['action'] == 'run_command' and 'pytest' in (r['element'] or ''))} time(s).")

    issues = Counter(_label(r["error"]) for r in verify if r["status"] == "rejected")
    if issues:
        out.append("Attempts rejected, by reason: " + ", ".join(f"{label} x{n}" for label, n in issues.most_common()))
    if errors:
        out.append("Model errors: " + ", ".join(f"{k} x{n}" for k, n in errors.items()))
    if sessions:
        used = [s["tokens"] / s["context"] for s in sessions if s.get("context")]
        out.append(f"Context window: {len(sessions)} model session(s), average {sum(s['tokens'] for s in sessions) // len(sessions)} tokens used, "
                   f"largest {max(s['tokens'] for s in sessions)} of {sessions[0].get('context')} ({max(used):.0%}).")
    stamps = [r["ts"] for r in rows if r["ts"]]
    if stamps:
        out.append(f"Time: {(max(stamps) - min(stamps)) / 60:.0f} minute(s) from first to last recorded action.")

    notes = []
    if issues["syntax errors"] >= 2:
        notes.append("Syntax errors kept recurring: the model writes long code in one go and slips. Smaller steps (one function each) help.")
    if issues["hollow tests (no assertion)"] + issues["tests that never touch the project's code"] >= 1:
        notes.append("The model wrote tests that could not fail; the harness rejected them.")
    if states["blocked"] > states["done"]:
        notes.append("More tasks were blocked than finished: try a smaller, more specific goal, or run the same goal again to retry.")
    if sessions and used and max(used) > 0.8:
        notes.append("At least one session used over 80% of the context window; it may have been cut short.")
    if errors:
        notes.append("The on-device model reported errors (see above); guardrail errors usually mean the wording of the task tripped a filter.")
    if notes:
        out.append("Noticed:")
        out += [f"  - {n}" for n in notes]
    return "\n".join(out)


def write(ledger, queue, memory):
    """Build the recap, keep it in MEMORY.md (section Recap) and in the ledger under task 'recap', and return the text."""
    text = build(ledger, queue, memory)
    with ledger.scope(task="recap", kind="recap"):
        memory.set("Recap", text)
        ledger.log_read("recap", "MEMORY.md", {"chars": len(text)}, text)
    return text
