"""Winding down at the context budget. A model session is stopped at about 7K of the 8K window, before the hard limit throws; what is
unfinished is written down as a handoff note and queued as a continuation task, so the next agent starts from facts, not from nothing."""
import json
import re

BUDGET = 7168          # tokens: 7K of the 8,192-token window. Past this the session is wound down at its next tool call.
MAX_CONTINUATIONS = 2  # a continuation of a continuation of a task is the limit; after that the task is blocked instead
_PREFIX = re.compile(r"^Continue: ")


def continuation_depth(title):
    depth = 0
    while _PREFIX.match(title):
        title, depth = _PREFIX.sub("", title, count=1), depth + 1
    return depth


def looked_at(ledger, agent, task_id):
    """What was already read or searched while working on this task, from the ledger (so the next agent does not repeat it)."""
    rows = [r for r in ledger.trail(task_id, agent) if r["task_id"] == task_id and r["status"] == "ok"]
    files = list(dict.fromkeys(r["element"] for r in rows if r["action"] == "read_file"))
    searched = list(dict.fromkeys(json.loads(r["args"]).get("pattern", r["element"]) for r in rows if r["action"] == "grep"))
    web = list(dict.fromkeys(r["element"] for r in rows if r["action"] == "web_search"))
    parts = []
    if files:
        parts.append("read " + ", ".join(files[:8]))
    if searched:
        parts.append("searched the code for " + ", ".join(repr(p) for p in searched[:5]))
    if web:
        parts.append("searched the web for " + ", ".join(repr(q) for q in web[:3]))
    return "; ".join(parts)


def note(task, *, done, problem, looked=""):
    """One line that a fresh session can act on: the original ask, what exists now, what is still wrong, what was already looked at."""
    pieces = [f"Task: {task.title}. {task.detail}".strip()]
    pieces.append("Done so far: " + (done or "nothing kept") + ".")
    if problem:
        pieces.append("Still wrong: " + " ".join(problem.split())[:260] + ".")
    if looked:
        pieces.append("Already looked at: " + looked + ".")
    pieces.append("The last session ran out of room: do one small piece at a time.")
    return " ".join(pieces)[:900]
