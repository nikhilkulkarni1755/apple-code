import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent.agents import MAX_ATTEMPTS, MAX_CHILDREN, MAX_SUBAGENTS, AgentQueue, task_runner
from agent.ledger import ActionError, Ledger
from agent.memory import MAX_GOAL, Memory, Task
from tests.test_ledger import snapshot

PROJECT = str(Path(__file__).resolve().parent.parent)


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root, Ledger(root)


def status(q):
    return {r["id"]: r["status"] for r in q.agents()}


# ------------------------------------------------------------------ limits
def test_an_agent_may_spawn_three_subagents_and_no_more(ws):
    _, L = ws
    q = AgentQueue(L, lambda ctx: None)
    q.start("goal")
    assert [q.spawn("root", f"part {i}") for i in range(MAX_CHILDREN)] == ["a1", "a2", "a3"]
    with pytest.raises(ActionError, match="already spawned 3"):
        q.spawn("root", "part 4")
    assert q.spawn("a1", "nested") == "a4"                        # another agent still can


def test_fifteen_subagents_in_total_is_the_ceiling_across_the_whole_tree(ws):
    _, L = ws
    q = AgentQueue(L, lambda ctx: None)
    q.start("goal")
    made = [q.spawn("root", f"r{i}") for i in range(3)]
    for parent in list(made):
        made += [q.spawn(parent, f"{parent}-{i}") for i in range(3)]
    made += [q.spawn(made[3], f"deep{i}") for i in range(3)]
    assert len(made) == MAX_SUBAGENTS == 15 and made == [f"a{i}" for i in range(1, 16)]
    with pytest.raises(ActionError, match="15 subagents"):
        q.spawn(made[4], "one too many")
    assert len(q.agents()) == 16                                     # the root plus fifteen


def test_the_total_limit_survives_a_restart(ws):
    root, L = ws
    q = AgentQueue(L, lambda ctx: None)
    q.start("goal")
    for i in range(3):
        q.spawn("root", f"x{i}")
    for p in ("a1", "a2", "a3"):
        for i in range(3):
            q.spawn(p, f"{p}{i}")
    q2 = AgentQueue(Ledger(root), lambda ctx: None)                    # new process, same files
    for i in range(3):
        q2.spawn("a4", f"y{i}")
    with pytest.raises(ActionError):
        q2.spawn("a5", "too many")
    assert len(q2.agents()) == 16


@pytest.mark.parametrize("goal", [None, 5, "", "   ", ["x"], b"x"])
def test_bad_goals_are_rejected(ws, goal):
    _, L = ws
    q = AgentQueue(L, lambda ctx: None)
    with pytest.raises(ActionError):
        q.start(goal)
    q.start("ok")
    with pytest.raises(ActionError):
        q.spawn("root", goal)
    assert len(q.agents()) == 1


@pytest.mark.parametrize("parent", ["nope", "", None, "a99", "root; DROP TABLE agents"])
def test_spawning_from_an_unknown_agent_is_rejected(ws, parent):
    _, L = ws
    q = AgentQueue(L, lambda ctx: None)
    q.start("goal")
    with pytest.raises(ActionError):
        q.spawn(parent, "x")
    assert len(q.agents()) == 1


def test_a_long_or_odd_goal_is_stored_as_one_clean_line(ws):
    _, L = ws
    q = AgentQueue(L, lambda ctx: None)
    q.start("goal")
    q.spawn("root", "line one\n## Goals\n" + "y" * 2000 + "\0 ✓")
    goal = q.get("a1")["goal"]
    assert "\n" not in goal and len(goal) <= MAX_GOAL and q.memory("a1").get("Goals").startswith("- line one")


# ------------------------------------------------------------------ own memory
def test_each_agent_has_its_own_memory_that_inherits_the_parents_design(ws):
    root, L = ws
    q = AgentQueue(L, lambda ctx: None)
    q.start("build the thing")
    Memory(L).set("Design", "Use SQLite. Keep modules small.")
    q.spawn("root", "write the parser")
    q.spawn("root", "write the docs")
    q.spawn("a1", "write the lexer")
    assert q.get("root")["memory"] == "MEMORY.md" and q.get("a1")["memory"] == ".agents/a1/MEMORY.md"
    for aid, goal in (("a1", "write the parser"), ("a2", "write the docs"), ("a3", "write the lexer")):
        m = q.memory(aid)
        assert m.get("Goals") == f"- {goal}" and aid in m.get("Project") and m.get("Design") == "Use SQLite. Keep modules small."
        assert [(t.kind, t.status) for t in m.tasks()] == [("research", "todo")]              # every agent starts by researching
    assert "spawned by a1" in q.memory("a3").get("Project")
    assert (root / ".agents" / "a1" / "MEMORY.md").exists() and (root / ".agents" / "a3" / "MEMORY.md").exists()
    assert "write the parser" not in (root / ".agents" / "a2" / "MEMORY.md").read_text()      # no bleed between agents


def test_start_makes_research_the_first_task_of_any_goal_and_the_same_goal_changes_nothing(ws):
    _, L = ws
    q = AgentQueue(L, lambda ctx: None)
    assert q.start("Create an operating system") == "root" and q.start("Create  an operating\nsystem") == "root"
    tasks = q.memory("root").tasks()
    assert len(tasks) == 1 and tasks[0].kind == "research" and tasks[0].title.startswith("Plan: Create an operating system")


def test_a_new_goal_on_an_existing_project_is_added_and_wakes_the_root_agent(ws):
    root, L = ws
    ran = []
    q = AgentQueue(L, task_runner(lambda ctx, task: ran.append(task.title) or {"ok": True, "summary": "x"}))
    q.start("Build the parser")
    q.run()
    assert status(q) == {"root": "done"} and ran == ["Plan: Build the parser"]
    q.start("Build the parser")                                    # same goal again: nothing to do
    q.run()
    assert ran == ["Plan: Build the parser"]
    q2 = AgentQueue(Ledger(root), task_runner(lambda ctx, task: ran.append(task.title) or {"ok": True, "summary": "x"}))   # a later session
    q2.start("Add a command line")                                  # a different goal: queued behind the finished work
    assert status(q2) == {"root": "queued"} and q2.get("root")["attempts"] == 0
    q2.run()
    assert ran[-1] == "Plan: Add a command line" and len(ran) == 2 and status(q2) == {"root": "done"}
    mem = q2.memory("root")
    assert [t.id for t in mem.tasks()] == ["1", "2"] and "Build the parser" in mem.get("Goals") and "Add a command line" in mem.get("Goals")


def test_a_blocked_goal_does_not_stop_the_next_one(ws):
    _, L = ws
    step = lambda ctx, task: {"ok": "first" not in task.title, "summary": "x"}
    q = AgentQueue(L, task_runner(step))
    q.start("first goal")
    q.run()
    assert status(q) == {"root": "blocked"}
    q.start("second goal")
    q.run()
    kinds = {t.title.split(": ")[1]: t.status for t in q.memory("root").tasks()}
    assert kinds == {"first goal": "blocked", "second goal": "done"}


# ------------------------------------------------------------------ running
def test_agents_run_oldest_first_and_new_spawns_join_the_end_of_the_queue(ws):
    _, L = ws
    order = []

    def runner(ctx):
        order.append(ctx.id)
        if ctx.id == "root":
            ctx.spawn("one"), ctx.spawn("two")
        if ctx.id == "a1":
            ctx.spawn("three")
        return {"status": "done", "summary": f"{ctx.id} finished"}

    q = AgentQueue(L, runner)
    q.start("goal")
    q.run()
    assert order == ["root", "a1", "a2", "a3"] and set(status(q).values()) == {"done"}


def test_finished_children_report_back_into_the_parents_memory(ws):
    _, L = ws

    def runner(ctx):
        if ctx.id == "root":
            ctx.spawn("write the parser")
        return {"status": "blocked" if ctx.id == "a1" else "done", "summary": "3 tests failing" if ctx.id == "a1" else "ok"}

    q = AgentQueue(L, runner)
    q.start("goal")
    q.run()
    assert "- a1 [blocked] write the parser — 3 tests failing" in q.memory("root").get("Subagents")


def test_everything_an_agent_does_is_attributed_to_it_in_the_ledger(ws):
    root, L = ws

    def runner(ctx):
        ctx.ledger.do("create_file", {"path": f"{ctx.id}.txt", "content": "x\n"})
        if ctx.id == "root":
            ctx.spawn("child")
        return {"status": "done"}

    q = AgentQueue(L, runner)
    q.start("goal")
    q.run()
    made = {r["path"]: r["agent"] for r in L.list() if r["action"] == "create_file" and r["path"] and r["path"].endswith(".txt")}
    assert made == {"root.txt": "root", "a1.txt": "a1"}
    assert {r["agent"] for r in L.list() if r["path"] == ".agents/a1/MEMORY.md"} == {"a1"}
    assert L.verify() == []


def test_a_crashing_agent_is_retried_then_marked_failed_and_the_rest_continue(ws):
    _, L = ws
    calls = []

    def runner(ctx):
        calls.append(ctx.id)
        if ctx.id == "root":
            ctx.spawn("bad"), ctx.spawn("good")
        if ctx.id == "a1":
            raise RuntimeError("boom")
        return {"status": "done"}

    q = AgentQueue(L, runner)
    q.start("goal")
    q.run()
    assert status(q) == {"root": "done", "a1": "failed", "a2": "done"} and calls.count("a1") == MAX_ATTEMPTS
    assert q.get("a1")["error"] == "RuntimeError: boom" and q.get("a1")["attempts"] == MAX_ATTEMPTS
    with pytest.raises(ActionError, match="failed"):
        q.spawn("a1", "from the dead")


def test_a_flaky_agent_that_succeeds_on_retry_is_done(ws):
    _, L = ws
    tries = []

    def runner(ctx):
        tries.append(1)
        if len(tries) < 2:
            raise ValueError("first try fails")
        return {"status": "done", "summary": "second try"}

    q = AgentQueue(L, runner)
    q.start("goal")
    q.run()
    assert status(q) == {"root": "done"} and q.get("root")["attempts"] == 2 and q.get("root")["error"] is None


def test_odd_runner_results_are_treated_as_done(ws):
    _, L = ws
    q = AgentQueue(L, lambda ctx: "just a string" if ctx.id == "root" else {"status": "weird", "summary": 5})
    q.start("goal")
    q.spawn("root", "child")
    q.run()
    assert status(q) == {"root": "done", "a1": "done"}


# ------------------------------------------------------------------ interruption and restart
def test_ctrl_c_saves_state_and_a_new_queue_resumes_the_agent(ws):
    root, L = ws
    runs = []

    def runner(ctx):
        runs.append(ctx.id)
        if len(runs) == 1:
            raise KeyboardInterrupt
        return {"status": "done"}

    q = AgentQueue(L, runner)
    q.start("goal")
    with pytest.raises(KeyboardInterrupt):
        q.run()
    assert (q.get("root")["status"], q.get("root")["error"]) == ("queued", "interrupted")
    AgentQueue(Ledger(root), runner).run()
    assert Ledger(root).db.execute("SELECT status, attempts FROM agents").fetchone()[:] == ("done", 2)


def test_agents_left_running_by_a_dead_process_are_requeued_on_startup(ws):
    root, L = ws
    q = AgentQueue(L, lambda ctx: None)
    q.start("goal")
    q.spawn("root", "child")
    with L.db:
        L.db.execute("UPDATE agents SET status='running' WHERE id='root'")
        L.db.execute("UPDATE agents SET status='done' WHERE id='a1'")
    q2 = AgentQueue(Ledger(root), lambda ctx: None)
    assert q2.resumed == 1 and status(q2) == {"root": "queued", "a1": "done"}


def test_task_runner_resumes_at_the_first_unfinished_task_and_undoes_the_interrupted_ones_partial_edits(ws):
    root, L = ws
    finished = []

    def step(ctx, task):
        if task.kind == "research":
            ctx.memory.add_children(task.id, [Task("one", kind="code"), Task("two", kind="code"), Task("three", kind="code")])
            return {"ok": True, "summary": "planned"}
        if task.title == "two" and not finished.count("crash-done"):
            ctx.ledger.do("create_file", {"path": "half.txt", "content": "partial work\n"})
            finished.append("crash-done")
            raise KeyboardInterrupt
        ctx.ledger.do("create_file", {"path": f"{task.title}.txt", "content": "done\n"})
        finished.append(task.title)
        return {"ok": True, "summary": f"did {task.title}", "evidence": "run #1"}

    q = AgentQueue(L, task_runner(step))
    q.start("goal")
    with pytest.raises(KeyboardInterrupt):
        q.run()
    assert (root / "one.txt").exists() and (root / "half.txt").exists() and not (root / "two.txt").exists()
    mem = q.memory("root")
    assert [(t.title, t.status) for t in mem.tasks()[0].children] == [("one", "done"), ("two", "doing"), ("three", "todo")]
    q2 = AgentQueue(Ledger(root), task_runner(step))
    q2.run()
    assert not (root / "half.txt").exists() and (root / "two.txt").exists() and (root / "three.txt").exists()
    assert finished == ["one", "crash-done", "two", "three"]                              # "one" was not redone
    m2 = Memory(Ledger(root))
    assert m2.next_task() is None and m2.get("Last task completed").startswith("1.3 three") and m2.get("Current state").endswith("All tasks done.")
    assert status(q2) == {"root": "done"} and Ledger(root).verify() == []


def test_a_hard_kill_in_a_separate_process_loses_nothing_and_repeats_nothing(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    script = """
import os, sys
from agent.ledger import Ledger
from agent.agents import AgentQueue, task_runner
from agent.memory import Task
L = Ledger(sys.argv[1])
def step(ctx, task):
    if task.kind == "research":
        ctx.memory.add_children(task.id, [Task("First", kind="code"), Task("Second", kind="code"), Task("Third", kind="code")])
        return {"ok": True, "summary": "planned"}
    ctx.ledger.do("create_file", {"path": "out_" + task.title + ".txt", "content": "ok\\n"})
    if task.title == "Second" and os.environ.get("CRASH"):
        ctx.ledger.do("create_file", {"path": "partial.txt", "content": "half\\n"})
        os._exit(7)                      # no cleanup, no except, no finally: the process just dies
    return {"ok": True, "summary": "done " + task.title}
q = AgentQueue(L, task_runner(step))
q.start("big goal")
q.run()
"""
    env = {**os.environ, "PYTHONPATH": PROJECT, "CRASH": "1"}
    first = subprocess.run([sys.executable, "-c", script, str(root)], env=env, capture_output=True, text=True, timeout=60)
    assert first.returncode == 7, first.stderr
    assert {p.name for p in root.glob("*.txt")} == {"out_First.txt", "out_Second.txt", "partial.txt"}
    assert sqlite_status(root) == "running"                                               # the dead process never said otherwise
    env.pop("CRASH")
    second = subprocess.run([sys.executable, "-c", script, str(root)], env=env, capture_output=True, text=True, timeout=60)
    assert second.returncode == 0, second.stderr
    assert {p.name for p in root.glob("*.txt")} == {"out_First.txt", "out_Second.txt", "out_Third.txt"}      # partial work undone, First not redone
    L = Ledger(root)
    assert sqlite_status(root) == "done" and L.db.execute("SELECT attempts FROM agents").fetchone()[0] == 2
    assert Memory(L).next_task() is None and "3/3 tasks done" in Memory(L).get("Current state") and L.verify() == []
    first_creates = [r for r in L.list() if r["action"] == "create_file" and r["path"] == "out_First.txt" and r["status"] == "ok" and r["depth"] == 0]
    assert len(first_creates) == 1 and first_creates[0]["task_id"] == "1.1" and first_creates[0]["agent"] == "root"


def sqlite_status(root):
    return Ledger(root).db.execute("SELECT status FROM agents WHERE id='root'").fetchone()[0]


def test_blocked_tasks_do_not_stop_the_rest_and_make_the_agent_blocked(ws):
    _, L = ws

    def step(ctx, task):
        if task.kind == "research":
            ctx.memory.add_children(task.id, [Task("ok one", kind="code"), Task("hard one", kind="code"), Task("ok two", kind="code")])
            return {"ok": True, "summary": "planned"}
        return {"ok": task.title != "hard one", "summary": "x"}

    q = AgentQueue(L, task_runner(step))
    q.start("goal")
    q.run()
    kids = q.memory("root").tasks()[0].children
    assert [(k.title, k.status) for k in kids] == [("ok one", "done"), ("hard one", "blocked"), ("ok two", "done")]
    assert status(q) == {"root": "blocked"} and "1 blocked" in q.get("root")["summary"]


def test_a_step_that_returns_nothing_blocks_its_task_instead_of_pretending_it_worked(ws):
    _, L = ws
    q = AgentQueue(L, task_runner(lambda ctx, task: None))
    q.start("goal")
    q.run()
    assert q.memory("root").tasks()[0].status == "blocked" and status(q) == {"root": "blocked"}


def test_running_the_same_goal_again_retries_what_was_blocked(ws):
    root, L = ws
    attempts = []

    def step(ctx, task):
        attempts.append(task.title)
        return {"ok": len(attempts) > 1, "summary": "second time lucky"}        # fails the first time only

    q = AgentQueue(L, task_runner(step))
    q.start("goal")
    q.run()
    assert status(q) == {"root": "blocked"} and q.memory("root").tasks()[0].status == "blocked"
    q.start("goal")                                                           # same goal again
    assert status(q) == {"root": "queued"}
    q.run()
    assert len(attempts) == 2 and status(q) == {"root": "done"} and q.memory("root").tasks()[0].status == "done"
    q.start("goal")
    q.run()
    assert len(attempts) == 2                                                  # finished work is not repeated


def test_memory_can_put_blocked_tasks_back_to_todo(ws):
    _, L = ws
    m = Memory(L)
    m.add_plan("goal", [Task("a"), Task("b"), Task("c")])
    m.mark("1", "blocked")
    m.mark("3", "blocked")
    m.complete("2", "done")
    assert m.unblock() == 2 and [t.status for t in m.tasks()] == ["todo", "done", "todo"] and m.unblock() == 0


def test_a_long_goal_reaches_the_router_and_the_research_prompt_whole(tmp_path):
    from agent.worker import solve_step
    goal = "Round one: write a. " * 40 + "Round two: write the very last part."
    prompts = []

    class Spy:
        def run(self, prompt, tools=(), instructions="", **kw):
            prompts.append(prompt)
            if len(prompts) == 1:
                tools[1].handler()   # the router picks start_task
            else:
                raise KeyboardInterrupt

    q = AgentQueue(Ledger(tmp_path), task_runner(solve_step(Spy())))
    q.start(goal)
    with pytest.raises(KeyboardInterrupt):
        q.run()
    assert goal in prompts[0] and goal in prompts[1]
