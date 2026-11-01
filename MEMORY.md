# MEMORY

Project memory, kept current by the agent after every task. Edit freely: unknown sections are kept.

## Project

A terminal coding agent that runs on Apple's on-device Foundation Model (8192-token context, tool calling). Python harness plus a small Swift helper process (agent/helper/main.swift). Every action is recorded in a SQLite ledger (.agent/ledger.db) and is reversible. Completion is judged by the harness running tests, never by the model's own claim.

## Goals

- Run tool calls (read, edit, grep, build, test, web search) with the on-device model
- Full observability: every action, diff and command output in the ledger
- A Ralph-style loop: a fresh model session per small task, state on disk
- Big requests are split into many small tasks stored in MEMORY.md so later sessions inherit the plan
- Python projects with pytest first; subagents, plan mode and containers later
- Build the coding agent
- Raise the real-model success rate
- Distribute apple-code

## Design

- Ledger (agent/ledger.py): atomic line actions with exact diffs and inverses; undo, redo, rewind; environment changes (.venv, pip/uv, npm) tracked and reversible (agent/envstate.py)
- The model edits by exact text (replace_text, insert_after), not line numbers: line-number tools scored 1/24 on the 3B model, text tools 15/24
- Commands run with the workspace .venv first on PATH, PIP_REQUIRE_VIRTUALENV, no .pyc files, and a denylist (a speed bump, not a sandbox)
- MEMORY.md (agent/memory.py) holds project state and the nested task plan; agent/planner.py decomposes big goals and route() sends big requests to decompose_task
- Measurements and decisions: PLAN.md
- Every ledger record has agent, task, kind and element; reads are logged too (ledger.scope, ledger.trail)
- Research first: agent/research.py reads the codebase and web, then records actionables as subtasks; restating tasks are dropped
- Subagents (agent/agents.py): FIFO queue, own MEMORY.md each, max 3 children per agent, 15 subagents ever; state in ledger.db and MEMORY.md so a killed run resumes
- Fresh minimal session per task: the model re-reads the whole transcript every call (about 1 s per 1000 tokens), no cross-session prompt caching observed (PLAN.md section 15)
- Worker (agent/worker.py): fresh session per attempt (max 4); the harness runs pytest and requires exit 0, no skips, files changed, MORE passing tests than before, and the original tests unchanged against the new code (agent/verify.py). Failed tasks are rolled back.
- Commands see only an allowlisted environment (no exported secrets). Entry point: python -m agent solve 'goal'
- Installer (install.sh, npm/, scripts/make-dist.sh): global `apple-code` command via curl, npx or zip; private venv in ~/.apple-code, launcher in ~/.local/bin; Python runs with -I so a project's own agent/ folder cannot shadow it (PLAN.md section 18)

## Current state

13/19 tasks done. Next: 6 Eval set

## Last task completed

10 Installer, npm package and zip — installer, npm package, zip and release script built; 8 real-install tests pass (evidence: 808 tests + 15 marked)

## Task plan

- [x] 1 Phase 0: probe the Foundation Model — context is 8192 tokens, tools work, Python SDK needs Xcode (done when: findings in PLAN.md section 11)
- [x] 2 Milestone 1: action ledger — atomic file actions with undo, redo, verify (done when: tests/test_ledger.py passes)
- [x] 3 Milestone 2: commands and tests in the ledger — snapshot diff, replay, diff (done when: tests/test_runner.py passes)
- [x] 4 Milestone 3: wire the model to the tools (done when: a failing test gets fixed end to end by the model)
  - [x] 4.1 Swift helper, Python client, tool registry — agent/helper/main.swift, agent/fm.py, agent/tools.py (done when: measurements in PLAN.md section 12)
  - [x] 4.2 Hardening: adversarial and reversibility suites — hostile inputs, fuzzing, env tracking (done when: pytest passes)
  - [x] 4.3 MEMORY.md and the task decomposer — agent/memory.py, agent/planner.py, router (done when: tests/test_memory.py and test_planner.py pass)
  - [x] 4.4 Worker loop — fresh model session per task, tool-call cap, failure summary carried to the next try (done when: a failing test gets fixed end to end)
  - [x] 4.5 Harness verifier — run the acceptance command; exit 0, at least one test, none skipped (done when: the model cannot declare itself done)
  - [x] 4.6 Feed MEMORY.md context into each worker prompt — memory.context() under a token budget (done when: worker prompt includes next task and last completed)
  - [x] 4.7 [code] Research step, web tools, attribution — agent/research.py, agent/web.py, ledger scope/trail (done when: tests/test_research.py, test_web.py, test_attribution.py pass)
  - [x] 4.8 [code] Subagent queue with restart — agent/agents.py: 3 children, 15 total, resume after a hard kill (done when: tests/test_agents.py passes)
- [x] 5 Web search and fetch tools — one search tool, one fetch tool, truncated (done when: tool tests pass)
- [ ] 6 Eval set — 10 to 20 scripted tasks; pass rate and iterations per task (done when: numbers recorded in PLAN.md)
- [ ] 7 Later: plan mode, subagents, container executor
- [ ] 8 [code] Improve research and worker prompts — use the eval set to tune (done when: pass rate measured)
- [ ] 9 [code] Progress output while solve runs — show attempts and verdicts live
- [x] 10 [code] Installer, npm package and zip — install.sh, npm/, scripts/make-dist.sh, doctor/update/uninstall (done when: pytest -m net passes)
- [ ] 11 [code] Publish: push the code, tag a release, npm publish — needs the owner's GitHub and npm accounts (done when: the curl and npx commands in README.md work from a clean machine)
- [ ] 12 [code] Checksum check in install.sh, and a prebuilt signed helper — removes the need for Apple's command line tools
