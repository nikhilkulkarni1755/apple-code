# PLAN: Local coding agent on Apple Foundation Models

Terminal CLI, written in Python, building Python projects with pytest. Commands auto-run (plan mode later).

## 1. Design constraints from the FM

The on-device FM is a ~3B model with an **8192-token context window** (measured in Phase 0, §11), shared between input and output. It has a tool-calling protocol and guided generation into typed structs. It is good at structured output and short tasks, and weak at long reasoning.

- **Fresh context every step.** Tool output, file contents and history cannot pile up in one session. This is why the Ralph loop fits: each iteration is a fresh session with a small prompt, and state lives on disk.
- **Intelligence goes in the harness.** The harness owns planning state, the loop, safety, the ledger, and judging completion. The model proposes small actions.
- **Use guided generation** for plans, tool args and "done" claims.

## 2. Architecture

```
CLI (REPL + `run "goal"`)
  └─ Orchestrator (the Ralph loop, deterministic code)
       ├─ Planner: goal → task list (guided generation)
       ├─ Worker: fresh FM session per iteration, tools attached
       ├─ Verifier: harness runs the acceptance command itself
       ├─ Memory: MD files, injected under a token budget
       └─ Ledger (SQLite): every action, with state needed to undo it
Model-facing tools: read_file, list_files, grep, create_file, replace_text, insert_after, delete_file,
       run_command, run_tests (later: web_search, fetch_url, memory_read/append)
Ledger actions (atomic, line-based, never offered to the model): create/insert_lines/delete_lines/replace_lines/delete_file
Executor interface: LocalExecutor now, AppleContainerExecutor later
```

## 3. FIRST DELIVERABLE: the action ledger

Everything else depends on this, so it is built and tested before any model is involved. Every action the agent takes is recorded accurately, and every file-mutating action is reversible.

### 3.1 Atomic actions

Each tool does exactly one small thing, so each is trivially invertible.

| Action | Effect | Inverse |
|---|---|---|
| `create_file(path, content)` | New file; fails if it exists | `delete_file(path)` (checks content unchanged) |
| `insert_lines(path, at_line, lines)` | Add lines before `at_line` | `delete_lines(path, at_line, n)` of those exact lines |
| `delete_lines(path, start, end)` | Remove lines | `insert_lines` with the removed text (stored) |
| `replace_lines(path, start, end, lines)` | Remove then insert, one action | `replace_lines` with old text (stored) |
| `delete_file(path)` | Remove file | `create_file` with stored content |
| `run_command` / `run_tests` | Execute, capture output | Not undoable; **replayable** (see 3.4) |
| `read_file`, `grep`, `list`, `web_*` | Read-only | None needed; still logged |

No "overwrite whole file" or "edit by fuzzy match" tool. Whole-file rewrites are a `replace_lines` over the full range, so the line diff stays honest. Appending is `insert_lines` at end of file.

### 3.2 What each ledger record stores

- `seq` (monotonic), `run_id`, `task_id`, `iteration_id`, timestamp, duration
- `action` name and exact arguments (JSON)
- `status`: ok / error / rejected, plus error text
- **Pre-state and post-state**: path, sha256 of the whole file before and after
- **Exact text removed and exact text added**, with line numbers (this is the line-level diff)
- **Inverse action**: the precomputed action and arguments that undoes this one
- Content blobs for file content are stored content-addressed (sha256 → bytes), so any past file version can be restored exactly
- Result returned to the model (truncated version) and the full output reference

### 3.3 Undo semantics

- **Undo is itself an action** appended to the ledger. History is never edited or deleted, so you can always see what happened including the undo.
- **Safe undo:** before applying an inverse, check the file's current hash equals the record's post-state hash. If not, something changed it later: refuse with a conflict, or undo later actions first.
- **Rewind to seq N:** undo records in reverse order down to N. Also supports `undo --last`, `undo --task <id>`, `undo --iteration <id>`.
- **Redo:** undoing an undo record. Each record has a `depth` (0 for an action, +1 per revert of it); even depth means an effect in force, odd means an undo that `redo` can reverse. `undo --last` and rewind only touch effects in force, so repeated `undo` never toggles.
- **Verify:** `ledger verify` replays the whole ledger forward from an empty workspace snapshot and checks resulting hashes match the current tree. This is the test that the ledger is accurate.

### 3.4 Non-undoable commands

Running tests or shell commands cannot be undone, but they can be re-run, and their file side effects can be captured.

- Record: command, cwd, env diff, exit code, stdout/stderr (full, by hash), duration, timeout flag.
- **Workspace snapshot diff around every `run_command`:** hash the tree (excluding `.venv`, `.git`, caches, `.agent/`) before and after. Any file the command created, changed or deleted is recorded as an observed change with an inverse, same as the atomic actions. This catches `pip install`, formatters, generated files, and scripts that write files.
- `ledger replay <seq>` re-runs a recorded command against the current tree and stores a new record linked to the original.
- **Your example, as a workflow:** add a test (`insert_lines`) → run tests (record A) → undo the insert (inverse) → run tests again (record B). `ledger diff A B` shows the evidence difference. This becomes a built-in `experiment` helper later.

### 3.5 Storage

- One SQLite file at `.agent/ledger.db`, plus blob directory `.agent/blobs/`. Written only by the harness, never by the model.
- Tables: `runs`, `tasks`, `iterations`, `actions` (the ledger), `blobs`, `evidence`.
- Writes happen inside a transaction together with the file change: the DB record and the file mutation both succeed or neither does (write the record first as `pending`, apply, then mark `ok`; recover `pending` rows on startup).

### 3.6 CLI for the ledger

`python -m agent do ACTION JSON`, `list`, `show <seq>` (removed/added lines), `undo [seq | --to N]` (no argument = last), `redo`, `verify`. These work with no model at all. `replay` and `diff` between runs arrive with Milestone 2; `undo --task/--iteration` and the `run_id`/`task_id`/`iteration_id` columns arrive with the orchestrator (Milestone 3).

### 3.7 Exit criteria for this deliverable

- Pytest suite for the ledger itself, including: every atomic action followed by its undo restores byte-identical files; random sequences of actions then full rewind return the tree to the starting hash; undo refuses on external modification; crash between record and apply is recovered; `verify` passes after any sequence.
- The model is not required. Actions are driven by a plain Python/CLI harness.

## 4. The loop (Ralph style)

1. **Plan once.** Goal → typed task list: id, goal, likely files, **acceptance command** (e.g. `pytest tests/test_x.py::test_y`). Plan is written to `.agent/plan.md` and is editable by the user.
2. **For each task, loop until verified or the budget is spent:**
   - Build a minimal prompt: task, acceptance command, last failure summary, relevant memory snippets, short repo map.
   - Fresh FM session with tools. Cap tool calls per iteration.
   - The model may claim completion; the harness runs the acceptance command and decides.
   - On failure, compress the failure (failing assertion, not the full traceback) into the next prompt.
3. **Stop conditions:** verified pass, iteration cap, wall-clock cap, or stall. A stopped task is marked blocked with a reason.

The loop lives in orchestrator code, not as a model-callable tool, so nested contexts are avoided and the model cannot skip it. "Work on task N until its condition holds" is still a function; subagents later are the same worker loop with a different tool set and prompt.

## 5. Completion with evidence

- Each task has an acceptance command, and the harness runs it.
- Evidence record: command, exit code, duration, output hash, pytest summary counts, ledger range for the task (all actions taken), timestamp.
- **Red then green** for new behavior: confirm the test fails before the implementation. The ledger makes this checkable (`replay` on the pre-change tree).
- Done only when exit code is 0, at least one test ran, and none were skipped/xfailed/deselected unexpectedly.
- A full-suite regression run gates the end of the run.

## 6. Dumb cases to handle

| Failure | Defense |
|---|---|
| Model says "done" without running anything | Harness verifier is the only judge |
| Edits tests to make them pass | Acceptance tests are frozen; ledger flags writes to protected paths |
| `pytest` collects 0 tests (exit 5) or skips all | Treated as failure |
| Malformed/hallucinated tool args, unknown tool | Short, specific error returned, counts toward iteration budget |
| Stale or wrong edit location | The model edits by exact text that must match once (§12), so there are no line numbers to go stale; ambiguous or missing matches are rejected with a message |
| Same tool call repeated | Loop detector → hint → abort iteration |
| No progress across iterations | Stall detector on tree hash plus failure-summary hash |
| Huge outputs blowing the 8K window | Hard truncation per tool, head and tail with a marker |
| Hanging commands | Per-command timeout, kill the process group |
| Path escape, `rm -rf`, `sudo` | Workspace-rooted paths; denylist even in auto-run mode |
| Flaky tests | Rerun once before trusting a result |
| Context overflow | Pre-flight exact `tokenCount`, trim lowest-priority sections first |
| Syntax-breaking edits | Python files are syntax-checked after write; failure is reported to the model, and the ledger makes it one `undo` to revert |

## 7. Memory (MD files, phase one)

- `AGENTS.md`: conventions and how to run tests.
- `.agent/progress.md`: append-only log of completed tasks and what changed (generated from the ledger).
- `.agent/lessons.md`: short learned facts.
- Retrieval: grep-based, a few snippets per iteration within a fixed token budget.
- Memory file writes go through the same ledger actions, so they are reversible too.

## 8. Tooling decisions

- **Language:** Python for the harness. The FM is reached through a small Swift helper process (see §11 for why the Python SDK is not usable yet). The ledger, loop, verifier and tools all stay in Python; Swift only runs model sessions.
- **Execution:** local terminal, auto-run everything. No plan mode yet. `Executor` interface leaves room for Apple's `container` later (VM start plus dependency install per task; deferred).
- **Target projects:** Python, pytest, a venv per project, test runs always use that venv.
- **Web search:** one `web_search` returning title/URL/snippet and one `fetch_url` returning cleaned, truncated text. Backend still open (see §10).

## 9. Milestones

0. **FM spike. DONE**, findings in §11.
1. **Ledger and atomic file actions (first deliverable, §3). DONE.** `agent/ledger.py`, `agent/__main__.py`, `tests/test_ledger.py` (46 tests pass, including 20 random action/undo/redo sequences that rewind to a byte-identical tree). Rejected actions are recorded with status `rejected`. Deleting a file also removes directories it leaves empty.
2. **`run_command` / `run_tests` with snapshot diff and replay. DONE.** `agent/runner.py` (swappable `LocalExecutor`, `run_tests`, `pytest_summary`), plus `run_command`, `replay`, `diff` in `agent/ledger.py` and `run`/`test`/`replay`/`diff` in the CLI. 63 tests pass, including the add-test / run / undo / re-run workflow (exit codes 0, 1, 0; `diff` shows the failing test appear and disappear). Behavior and limits:
   - A command's stdout and stderr are stored together as one blob; the record holds exit code, duration and timeout error. A timeout kills the whole process group.
   - Files a command creates, changes or deletes become ordinary ledger rows (`cause` = the command's seq), each with an exact line diff and an inverse, so `undo`, `rewind` and `verify` cover them. Changes are trimmed to the differing lines only.
   - Only utf-8 text files up to 1MB are tracked. Binary and larger files, and `.agent`, `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache` and `node_modules`, are ignored, so a command that changes those is not undoable.
   - Commands themselves cannot be undone. A command row left unfinished by a crash is marked `error` on startup, and its file changes may be incompletely recorded.
   - `run_tests` uses the workspace's `.venv/bin/python` if present, otherwise the current interpreter.
3. **Single-task worker loop** with the harness verifier, FM wired to the tools. *In progress:* the Swift helper (`helper/main.swift`, build with `swiftc -O helper/main.swift -o helper/fm-helper`), the Python client (`agent/fm.py`), the tool registry (`agent/tools.py`) the measurements (§12), the hardening work (§13), MEMORY.md plus the task decomposer (§14), and research, subagents and restarts (§16) are done and tested (699 tests); the worker loop, the harness verifier, and feeding `MEMORY.md` context into each worker prompt are next. Exit: "make this failing test pass" succeeds, and §6 cases have tests.
4. **Planner and multi-task orchestrator.** Exit: a small goal runs end to end, or stalls with a clear reason.
5. **Memory and web search.**
6. **Eval set and hardening.** 10–20 scripted tasks; measure pass rate and iterations per task; tune prompts.
7. **Later:** plan mode, subagents, container executor, parallel tasks.

## 10. Open decisions

- Web search backend: key-less DuckDuckGo scraping (fragile), self-hosted SearXNG, or an API like Brave (free tier, needs a key).
- How to reach the FM from Python: install full Xcode so `apple-fm-sdk` builds, or use the Swift helper (§11).

## 11. Phase 0 findings (measured on this machine, macOS 27.2, Apple Silicon)

- **Model is available** and answers via `SystemLanguageModel.default` from a plain `swiftc` build.
- **Context window is 8192 tokens**, not 4K. `SystemLanguageModel.contextSize` and `tokenCount(for:)` exist, so the harness can pre-count prompts exactly instead of estimating (~4.6 chars per token on plain English). Overflow raises a clear error ("Content contains 8472 tokens, which exceeds the maximum allowed context size of 8192"). The first overflow test with repeated "lorem ipsum" instead returned "May contain unsafe content", so a guardrail error can show up on odd input and must be handled separately.
- **Tool calling works**, including **multiple tool calls in one turn** (two `add` calls from one prompt) and arguments arriving as JSON. A session transcript records the tool calls (5 entries for one call).
- **A throwing tool aborts the whole `respond` call** with `ToolCallError`. Our tool wrappers must catch every error and return it to the model as text, so one failed command doesn't end the iteration.
- **`apple-fm-sdk` (0.2.1 on PyPI) does not install here.** It compiles Swift bindings at install time and requires full Xcode; only Command Line Tools are installed.
- **The `@Generable` macro does not work with Command Line Tools only** (macro plugin missing). Workaround proven: define tools with `GenerationSchema` / `DynamicGenerationSchema` and `GeneratedContent` arguments. This is actually what we need, since tool schemas will be defined from Python data at runtime.

**Decision:** keep the harness in Python and build a small Swift helper that speaks JSON lines over stdin/stdout: Python sends a prompt, tool schemas and limits; the helper runs one FM session and, when the model calls a tool, asks Python to execute it and returns the result. All tool execution (and so the whole ledger) stays in Python. If full Xcode gets installed later, the helper can be swapped for `apple-fm-sdk` behind the same interface.

## 12. Phase 3 measurements (`experiments/measure_fm.py`, 3 runs per prompt)

**Tool schema cost.** An empty session already costs 46 tokens of fixed overhead. Each tool costs 100–180 tokens; all 9 model-facing tools cost about 780 (10% of the 8192 window). Shortening descriptions and dropping parameter descriptions saves only ~14%, so schemas are not the constraint. Budget about 800 tokens for tools plus about 75 for instructions, leaving about 7,000 for task, file contents, tool results and the answer.

**Tool selection (9 tools, 21 prompts x 3).** 45/63 right first tool. Every miss was a read-only first step (`list_files` or `grep`) on prompts about a file the model had not seen yet; no prompt picked a wrong mutating tool. Clear intents (create, delete, run tests, run a command, search, list) were right on almost every run. Mean 1.6 s per one-tool session. Descriptions are fragile: shortening `read_file`'s description to "Read a file." flipped "show me calc.py" from `read_file` to `list_files` on all 3 runs; making the two descriptions distinct ("contents of a file" vs "file names, not their contents") fixed most of it. Any description change needs a rerun of this script.

**Editing: line-number tools fail, text-anchored tools work.** The model was given a file and asked for 8 small edits (3 runs each, exact final file compared):

| Edit tools offered | Exact result, first try |
|---|---|
| `insert_lines`/`delete_lines`/`replace_lines` with `expected` text | 1/24 |
| same, without `expected` | 1/24 |
| `replace_text(path, old, new)` only | 13/24 |
| `replace_text` + `insert_after(path, anchor, text)` | 15/24 (16/24 on the final rerun) |

Why the line tools failed: the model is off by one or two lines, and when asked to repeat the replaced text exactly it drops indentation and newlines. The `expected` guard rejected every wrong edit (46 rejections, no bad edit reached a file), so the ledger design held, but the model could not recover from the rejections. Showing `N: text` line numbers made it copy the prefix into indentation and even into file contents.

**Decision:** the model edits by text, not line numbers. `replace_text` and `insert_after` require the snippet to match exactly once; the harness finds the line range itself and records it as one atomic line action through `diff_action`, so the ledger stays exact and reversible. `read_file` shows plain text with no line numbers. A deletion that would leave an empty line removes the line. Remaining failures: appending a new function at the end of a file (0/3, the model struggles to choose an anchor), and pasting the rest of the file into `new` when deleting a function (0/3). Both are candidates for prompt wording or a larger retry budget; the worker loop will expose whether retries help.

**Safety observation:** in one run the model deleted a whole file to "fix" an edit. Auto-run is only acceptable because every action is undoable; the worker loop should also flag a task that deletes files not mentioned in it.

## 13. Hardening: adversarial tests, reversibility, terminal commands (606 tests: `.venv/bin/python -m pytest`)

**Test files.** `test_ledger` and `test_runner` (good inputs), `test_adversarial_ledger` and `test_adversarial_tools` (every hostile input must be rejected, change nothing, and leave a `rejected` record), `test_reversibility` (below), `test_commands` (command guard and environment), `test_memory`, `test_planner`, `test_fm_client` (fake helper; 3 real-model tests run with `pytest -m fm`). Three expected failures document known gaps.

**Bugs the adversarial tests found and fixed:**
- `.AGENT/x` (case alias on a case-insensitive disk) reached into the ledger directory; paths are now compared with `samefile`.
- NUL bytes, 300-character names, and "a file used as a directory" crashed instead of being rejected.
- `True` was accepted as a line number or sequence number; non-string, bytes, lone-surrogate or oversized text crashed or was accepted. Files over 1 MB are rejected so everything stays trackable.
- `grep` and the file snapshot followed a symlink to a file outside the workspace and returned its contents.
- A catastrophic regex hung `grep` forever; it now stops after 3 seconds.
- `read_file` with a negative start returned wrapped-around lines; a start past the end returned "(empty)".
- A task title containing a newline could split the `MEMORY.md` plan format; `Memory` now sanitizes everything it writes.
- Edit then undo within one second reused a stale `.pyc`, so the second test run still showed the old result. Commands now run with `PYTHONDONTWRITEBYTECODE=1`.

**Reversibility.** `test_reversibility` covers: editing, adding and deleting a test case then reversing (file bytes identical, test result back to the original); every model-facing mutating tool and several shell commands (undo restores the exact bytes, redo restores the change); undo/redo cycles; and 30 fuzz runs of 40 random tool calls each, a quarter of the arguments hostile (including `sudo`, `rm -rf /`, paths outside the workspace). The invariants after every fuzz run: nothing outside the workspace changed, the ledger verifies, no record is left pending, and `rewind(0)` restores the exact starting files.

**Terminal commands.**
- Build and test commands (`pytest`, `npm run build`, `make`) are recorded and replayable, not undoable; the text files they create or change are undoable like any edit.
- Environment changes are tracked outside the file snapshot (`agent/envstate.py`): `.venv` existence, installed Python packages (read from `site-packages`, so it works for pip and uv), and top-level `node_modules` packages (symlinked local packages are recorded as `link:<path>`). A command that changes them gets an `env_change` record with before and after states. Undo brings the environment back with `pip uninstall`, `uv pip uninstall`, reinstalling the old version, removing the directory, or `npm install --no-save`; redo does the reverse. Undo refuses if the environment was changed behind the ledger's back. Tested with real `uv`, `pip` and `npm` using local wheels and a local package (no network).
- Commands run with the workspace `.venv/bin` first on `PATH`, the agent's own venv removed from the environment, and `PIP_REQUIRE_VIRTUALENV=true` so `pip install` cannot touch the global environment.
- A denylist rejects `sudo`, anything mentioning `.agent`, `rm -rf` of `/`, `~`, `$HOME`, `..` or `.`, raw disk writes, fork bombs, `curl | sh`, and `chmod -R /`, plus empty or NUL-containing commands and timeouts outside 0 to 3600 seconds. It is a speed bump against accidents, not a sandbox: `python -c "shutil.rmtree('/')"`, `find / -delete` and base64 tricks get through (the three expected-failure tests).

**Known limits.** Restoring a removed or downgraded package needs the original source (package index or local files). Redoing the creation of a `python -m venv` environment recreates it with `uv venv`, which has no `pip` and so cannot reinstall `pip` itself. Only utf-8 text files up to 1 MB are tracked. A command that changes the environment and then the same command line uses the new environment (`uv venv && pip install x`) runs `pip` from the old `PATH`; use two commands.

## 14. MEMORY.md and the task decomposer

**MEMORY.md** (`agent/memory.py`) sits at the workspace root and holds: Project, Goals, Design, Current state, Last task completed, and the Task plan as a nested checklist (`- [x] 1.2 Title — detail (done when: ...)`). Unknown sections and human edits are kept. Every write goes through the ledger, so memory changes are recorded and undoable (`undo` restores the previous `MEMORY.md` byte for byte). `complete(task, summary, evidence)` marks a task done, rolls parents up, and rewrites Last task completed and Current state in one write. `next_task()` gives the first unfinished leaf; `context(max_chars)` builds the block a fresh model session should be given. This repository has its own `MEMORY.md` in the same format, so a new session can start from it.

**Decomposer and router** (`agent/planner.py`). `Planner.decompose(goal)` asks a backend to split a goal into tasks; any task marked `big` is split again, up to depth 3, 12 tasks per split and 200 in total. A title is used once in the whole plan. Backend output is treated as untrusted: wrong types, empty or duplicate titles, oversized strings and an endless "always big" backend are all handled, and a plan with no valid tasks is rejected without touching `MEMORY.md`. The default backend (`FMBackend`) uses the on-device model with an `add_task` tool as its structured output. The backend is pluggable; a larger model could replace it without other changes. `route(fm, goal, planner)` gives the model two tools, `decompose_task` and `start_task`, and runs the plan only if it picks the first.

**Measured on the real model** (`experiments/measure_planner.py`): the router sent 30/30 big requests to `decompose_task` and kept 30/30 small requests direct. Decomposing the OS, todo-app and compiler goals took 20 to 22 seconds each and produced 15 tasks, depth 3, 12 to 13 leaves. The structure is always valid. The quality is rough: some "done when" lines are vague ("Skeleton created") and tasks can overlap. The acceptance criteria the worker loop will actually check should be a runnable command, so the decomposer prompt or a second pass should be tuned once the loop exists.

## 15. How Apple's on-device model handles a request (documentation and measurements)

**What Apple's documentation says** (pages fetched from developer.apple.com on 2026-10-31; macOS 27 / iOS 27 docs):
- A session keeps state. `LanguageModelSession`: "A session is a single context that you use to generate content with, and maintains state between requests. You can reuse the existing instance or create a new one each time you call the model." "The framework records each call to the model in a `Transcript` that includes all prompts and responses."
- Everything in the session counts against one window. TN3193: "All the input and response in the generation process contribute tokens to the context window of the current language model session, including instructions, all prompts, the information of tools (schemas, input, and output), `Generable` schemas, and all the model's responses." "Managing the context window": "When you prompt the model using the same session, you consume more of the context window."
- A new session starts empty. "A new session has a new context window, but doesn't convey the state of the original session." Apple's advice for big work: "split the task into smaller steps, run each step with a new language model session, and then assemble the results together", and "Give the model a maximum of 3-5 tools to choose from."
- Prewarming. `prewarm(promptPrefix:)` "Loads the resources required for this session into memory ahead of a request"; passing a prefix "allows the system to process the prompt eagerly and reduce latency for the future request"; use it only with "a window of at least 1 second" before the request.
- Caching exists in the API but is not explained. Docs for the newer API: `LanguageModelSession.Usage.Input.cachedTokenCount` is "The number of input tokens that were served from a cache" and `totalTokenCount` is "The total number of input tokens from the transcript." Nothing says what is cached, for how long, or whether it spans sessions. The SDK on this machine (Command Line Tools) does not yet have the `usage` property, so cached tokens cannot be read here.
- New in the macOS 27 docs: `ContextOptions(includeSchemaInPrompt:reasoningLevel:)`, `transcriptErrorHandlingPolicy`, and `LanguageModelError.contextSizeExceeded` (this SDK still has `GenerationError.exceededContextWindowSize`).
- Model internals (Apple's 2025 tech report, arXiv 2507.13575, as summarized in search results; not re-read in full): the ~3B on-device model splits its layers into two blocks and the second block reuses the first block's key-value caches, cutting KV-cache memory by 37.5%. That is an architecture detail, separate from reusing a cache between requests.

**Answers to the questions:**
1. *Does each request use the same context window, and is it refreshed?* Within one session the window is shared and keeps filling: every prompt, tool call, tool result and answer is appended to the session's transcript and is re-sent with the next request. Measured: six calls of the same ~1,340-token prompt in one session left a 1,336, 2,673, 4,010, 5,347, 6,684, then 8,021-token transcript, one call from overflow. A new session is a fresh, empty window: six identical calls in fresh sessions never grew, and each took 0.85 s.
2. *Which size?* Apple's docs still say 4,096 tokens. This machine reports `contextSize` = 8,192 and accepted an 8,021-token transcript. Trust the `contextSize` API at runtime, not the documentation.
3. *Is there prompt caching?* The API documents cached input tokens, but on this machine the observable benefit is small or absent:
   - Across sessions, none. The same 2,206-token instructions in six new sessions took 1.38 s every time; different instructions took 1.5 s. `prewarm(promptPrefix:)` two seconds ahead changed 1.56 s to 1.49 s, about noise.
   - Within a session, appended turns are not cheaper. Five turns of ~1,320 new tokens took 0.98, 3.42, 3.87, 6.70, 7.25 s; a fresh session given the same cumulative text took 0.85, 1.75, 2.71, 4.74, 6.67 s. Complete (uncapped) replies behaved the same, so the cost is re-reading the whole transcript.
   - One partial effect, unexplained: after a long history, the first tiny follow-up took 6.98 s (as slow as a cold start) and the next ones took ~2.1 s. A session rebuilt from a transcript behaved the same (6.90 s, then 2.2 s). Something is reused when a request closely repeats the previous one, but not when new text is appended.
4. *What does it cost?* Roughly 0.7 to 1.0 ms per input token (about one second per 1,000 tokens, slightly worse as context grows): a tiny prompt 0.18 s, 1.3k tokens 0.9 s, 2.2k 1.4 s, 6.9k 6.9 s. Model load adds ~0.4 s on the very first call.

**What this means for the design.** Keep one fresh, minimal session per small task (this is what the Ralph loop does anyway): it is the only way to keep the window clear, and it is faster than carrying history. Pass state through `MEMORY.md` and the task prompt, not through the transcript. Do not count on prompt caching. Budget latency as about one second per 1,000 tokens in the prompt, so a research session that reads 4,000 tokens of files pays that on every tool step. Measure with `tokenCount(for:)`; read `contextSize` at startup instead of hard-coding 8,192. Revisit when the SDK exposes `usage.input.cachedTokenCount`, which would show directly what is cached. Probes: `experiments/` has the tool measurements; the inference timing probes were throwaway Swift programs (their numbers are above).

## 16. Research-first work, accountability, subagents, restarts

**Every ledger record is attributed.** Each record has `agent`, `task_id`, `kind` (research or code) and `element` (the file, glob, URL, query or command it acted on), set by `ledger.scope(agent=, task=, kind=)`. Reads (`grep`, `read_file`, `list_files`, `web_search`, `fetch_url`) are now logged too, with the full result stored by hash while the model sees a clipped copy; refused attempts are logged with their error. `ledger.trail("1")` returns everything done under task 1 and every task nested below it (1.1, 1.2.3, ...), `python -m agent trail 1 [--agent a2]` prints it, and `ledger.undo_task(agent, task)` reverses one task's edits. Example from a real run: `task 1: research/grep x1, research/list_files x1, research/read_file x2`.

**Research before action** (`agent/research.py`). For any task, the model first reads the codebase (and searches the web when it needs outside facts), then calls `add_actionable` once per step. The actionables become subtasks of that task in `MEMORY.md`, tagged `[code]` or `[research]`; a research subtask is researched in turn. Only reading tools are offered, so research cannot change the project. Guards against what a 3B model actually does: research that merely restates its parent task is dropped, repeated titles are dropped, nesting stops at depth 3, and a plan stops growing at 200 tasks. Web tools (`agent/web.py`): `web_search` through DuckDuckGo's HTML page (keyless scraper, replaceable) and `fetch_url`; fetching refuses non-http(s) URLs, private, loopback and cloud-metadata addresses on every redirect hop, non-text pages, and reads at most 500 KB.

**Measured on the real model** (`experiments/run_research.py`, goal "add a subtract function to calc.py with a test"): the first version never produced a concrete step; it proposed a research task restating its parent, five levels deep, with names like `add_subtract_research`. After the restatement filter, the naming example in the prompt and title de-duplication it finished in 10 s with `1.1 [code] Add subtract function to calc.py` and two test tasks (one a near-duplicate), plus the correct read records in the ledger. The code tasks are reported **blocked** because the worker loop that carries them out does not exist yet; the pipeline stops there rather than pretending.

**Subagents** (`agent/agents.py`). A queue of agents, oldest first. Every agent has its own `MEMORY.md` (the root uses `MEMORY.md`, subagent `aN` uses `.agents/aN/MEMORY.md`) which starts with its goal, the parent's Design section, and a first task: research the goal. An agent can spawn at most 3 subagents; at most 15 subagents are ever created in a workspace (the root is not counted; the count is persisted, so a restart does not reset it). Spawned agents join the end of the queue. When a subagent finishes, its result is appended to the parent's `Subagents` section. A crashing agent is retried up to 3 times, then marked failed, and the rest continue. The model can delegate through a `spawn_subagent` tool during research. `python -m agent agents` lists them. If you meant the 15 to include the root, change `MAX_SUBAGENTS` to 14.

**Restarts.** State lives in two durable places: the `agents` table in `.agent/ledger.db` and each agent's `MEMORY.md`, which is written through the ledger. `task_runner` saves a task's completion (status, last task, current state) before starting the next. On startup, agents left `running` by a dead process are queued again; a task found `doing` was interrupted, so its partial edits are undone (via the task attribution) and it is redone from the start; finished tasks are not repeated. Ctrl-C marks the agent `queued` first. Verified with a real hard kill: a child process ran `os._exit(7)` in the middle of the second of three tasks, and a fresh process finished the work, removed the half-written file and did not repeat the first task.

**macOS metadata.** `.DS_Store` and `._*` files are never tracked, listed, searched or restored by the ledger. `build.sh` builds the helper and then deletes any `.DS_Store` or `._*` file in the project; `.gitignore` lists `.DS_Store`; a test fails if the project tree contains one. There were none in the project when checked.

**How much work was #1?** About 450 new lines in four modules (research, web, agents, attribution in the ledger) and about 1,000 lines of tests. The part that is not built is the part that makes the code actionables happen: the worker loop and verifier (Milestone 3).

**Test counts.** 699 tests run by default (`pytest`), plus 5 that need the real model or the network (`pytest -m "fm or net"`), and 3 expected failures that document known gaps.
