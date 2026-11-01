# End to end: what the model sees

How a goal turns into model prompts, from `apple-code solve "<goal>"` to a verified file.
The prompts below are the real strings the code builds (dry run with a scripted model, `PROMPT1.md` goal). Only the model's replies are made up.

## Finding: the goal was cut before any model saw it

The PROMPT1 goal is about 640 characters. It was shortened in four places, so no session saw "Round 2" (`word_count`, `words.py`) or the test file names:

| Where | Old limit | Effect |
|---|---|---|
| `agents.py` `_create` / `start` | 500 characters | root goal cut |
| `memory.py` `_sanitize` (task detail) | 200 characters | the root task's detail, which the router and research read, cut to "...write slugify(text) in" |
| `memory.py` `_save_plan` (Goals section) | 300 characters | Goals line cut |
| `memory.py` `context()` (per section) | 600 characters | Goals cut again in the notes |

Consequences seen in live runs: `word_count` never produced (0 of 8 threshold runs solved the problem), test file names missing, and the same goal text repeated three times in the research prompt (Task line, Goals, Next tasks).

Also found: `1.1.1` and `1.2` both created `slugs.py`. The model saw `1.2` in "Next tasks" but nothing told it not to repeat it.

## The flow

`AgentQueue.start(goal)` stores the goal and creates task `1` ("Research: <first 60 characters>") whose detail is the goal. `task_runner` then repeatedly takes the first unfinished leaf task and calls `step(task)`:

1. **Task `1` (top level, no children):** the router runs, then research.
2. **Research tasks (`1.1`, ...):** one research session each.
3. **Code and test tasks:** the worker (up to 4 attempts, a fresh session each; only the failure summary carries over). The harness runs pytest and judges the evidence.

Every session is a fresh 8,192-token window. The only things that cross sessions are text the code puts into the prompt: Goals, Current state, Last task completed, Next tasks, and the failure summary.

### Session 1: router (1 call)
- Instructions: `You are a router. Immediately call exactly one tool for the user's request.`
- Tools: `decompose_task`, `start_task` (about 207 tokens)
- Prompt: the goal. Before the fix this was the 200-character cut text; now it is the whole goal.
- Reply: `start_task()` (the choice in all 8 earlier runs). The task then goes to research.

### Session 2: research on task `1`
- Instructions (the last sentence of the three before "Steps are things" is new): `You prepare work for a coding agent. Look at the project with the tools only if it has files, and search the web only if you need outside information. Then call add_actionable once for each small step that must be done, in order (3 to 8 steps). Write each title as a short imperative phrase that names the file it changes. Use kind=code whenever the change can be made now, kind=test to write tests for existing code; use kind=research only for something the project files cannot tell you. Do not repeat a step already listed under Next tasks. Steps are things to build, not tools to call.`
- Tools: `read_file`, `list_files`, `grep`, `add_actionable`, `spawn_subagent` (about 644 tokens)
- Limits: 8 tool calls, budget 7168 tokens
- Prompt after the fix:
```
Task: Research: Build two small Python helpers in two rounds. Each round has. The whole request is under Goals.
The project has no files yet.
Project notes:
Project: Agent root, the root agent.
Goals: - Build two small Python helpers in two rounds. Each round has three tasks in order: research, write the code, write the tests. Round 1: research how to turn text into a URL slug, write slugify(text) in slugs.py (...), then write tests for it in tests/test_slugs.py. Round 2: research how to count words, write word_count(text) in words.py (...), then write tests for it in tests/test_words.py.
Current state: 0/1 tasks done. Next: 1 Research: Build two small Python helpers in two rounds. Each round has
Next tasks:
- [~] 1 [research] Research: ... — <detail cut to 200 characters, only a reminder>
Then call add_actionable for each step.
```
- Reply: several `add_actionable(title, detail, kind, done_when, examples)` calls. Each returns `actionable N added`.
- Harness after the session: drops steps that restate the parent, repeat a title, or are named after a tool; keeps the rest as children `1.1`, `1.2`, ...

### Session 3: research on `1.1`
Same instructions and tools. The prompt is the same shape, with the task line `Task: Research how to turn text into a URL slug. Look up slug rules` and updated notes: `Last task completed: 1 ...` and `Next tasks:` listing `1.1`, `1.2`, `1.3` with their kinds and "done when". The model adds the steps under `1.1` (`1.1.1`, ...).

### Sessions 4 and 5: worker on `1.1.1`
- Instructions: `You are a coding agent. Do the task by editing files with the tools. Write or extend a test that proves it works. Use replace_text with the exact existing text, insert_after, or create_file. Never change existing tests. Call run_tests when you are done, then reply DONE.`
- Tools: `read_file`, `list_files`, `grep`, `create_file`, `replace_text`, `insert_after`, `run_tests` (about 807 tokens). Limits: 12 calls, budget 7168.
- Attempt 1 prompt:
```
Task 1.1.1: Create slugs.py with slugify(text)
Write slugify in slugs.py
Done when: pytest tests/test_slugs.py passes with at least one test
The project has no files yet.            <- code outline made by the harness, no model tokens
Notes:
<the same Project / Goals / Current state / Last task completed / Next tasks block, 900 characters, the goal in full>
The tests currently fail:
ERROR: file or directory not found: tests/test_slugs.py       <- the harness's own pytest run
```
- Attempt 2 prompt: the same head, then `Your last attempt failed:` and pytest's failure summary.
- After each attempt the harness runs pytest, checks the edits (protected tests, syntax, vacuous tests, original tests still passing, red then green) and marks the task verified or rolls its edits back.

## The change

- `memory.py`: `MAX_GOAL = 15_000` characters (about 2,000 words). Task detail and the Goals section keep up to that. `context()` shows Goals whole and does not count it against `max_chars`; the "Next tasks" reminder cuts each detail to 200 characters.
- `agents.py` and `planner.py` use the same `MAX_GOAL` (it was 500 and 4,000).
- `research.py`: when the task detail is the goal itself, the Task line says `The whole request is under Goals.` so the goal appears once, not three times. New instruction: `Do not repeat a step already listed under Next tasks.`
- `agents.start` no longer treats two goals with the same first 300 characters as one.

Cost: a goal of N tokens adds about N tokens to the router, every research session and every worker session (through Notes). A 2,000-word goal is about 2.7K tokens per session, so the 7K budget leaves less room; this is the price of the model seeing the whole request.

## Still open
- The planner (`add_task`) never ran in the threshold experiment, so its prompt is not measured.
- Titles are still the first 60 characters of the goal ("Research: Build two small ... Each round has"), which reads oddly.
- A rolling plan ("finish one task, then plan the next") is not built.

## Update: planning is flat and there is no research

After the first rerun (round 2 planned, but a research step nested a duplicate subtree and the run never reached it):

- The goal is planned once, in one session, into a flat list `1.1, 1.2, ...` of `code` and `test` steps. Nothing is nested under a step, and the router and the nested splitter are no longer used for a goal.
- `kind=research` does not exist for steps: the model's `research` label becomes `code`, and the web tools are not offered. The goal's own task keeps the internal kind `research` and the title `Plan: ...`.
- A goal gets at most `MAX_STEPS = 12` steps, however often it is planned, so the list cannot grow to `1.199` and crowd out the next goal.
- A new step is dropped if the plan already has a step of the same kind that names the same files, or some of them (`slugs.py` twice, or `tests/test_slugs.py` twice), however it is worded.
- Every step is worked by the Ralph loop: write code, run the tests, repeat until the harness verifies it. Only a handoff (`Continue: ...`, at most 2 deep) is nested under a step, and only when a worker runs out of context.
