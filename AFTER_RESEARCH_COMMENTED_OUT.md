# After research was commented out

Findings from running PROMPT1, PROMPT2 and PROMPT3 verbatim through `experiments/measure_budget.py` (budget 7168, 780 s cap per run, one run each unless noted). New findings are appended as they come in. "Solved" means the independent `check.py` of that prompt passed; it checks the code, not the test files.

Design under test: a goal is planned once into a flat list of code and test steps (1.1, 1.2, ...; at most 12), never nested except a `Continue:` handoff. No research steps, no web tools. Each step is worked by the write-code / run-tests loop until the harness verifies it.

## P1 (slugify + word_count), history before this document
| Version | Result | Why |
|---|---|---|
| Research on, goal cut to 200 chars | 0 of 8 solved | Round 2 (`word_count`) was never visible to any model session |
| Whole goal, research on | not solved (781 s cap) | Research on `1.1` nested a duplicate subtree (1.1.1 ... 1.1.6.5); round 2 never reached |
| Flat plan, first version | not solved, 13 s | Model titled steps `write_slug`; the identifier guard dropped all 6. Also a step with no file matched every later step of its kind |
| Flat plan, fixed (PR #2) | **solved** (781 s cap) | Plan of 6 steps in one 13 s session; 1.2 and 1.3 verified; 1.1 and 1.4 ended in handoffs after 2 hard `context_overflow`s |

P1 observations on the fixed version: planning is no longer the bottleneck (973-token start, 6 calls, 13 s). Workers take the time: 18 sessions, several 160-170 s. The model still writes "research ..." steps because the goal text asks for research; the harness now drops them.

## P2 (Inventory class + report), P3 (tokenize + evaluate): first runs on the flat planner
Two runs each (the second with the ledger and every session's prompt and calls saved: `experiments/measure_budget.py` now writes `OUT.dir/`).

| Run | Result | Time |
|---|---|---|
| P2 first | not solved: `report.py` never written (task 1.3 blocked) | 395 s |
| P2 second | not solved: `report()` returned tuples, the goal asks for `"name: qty"` strings | 614 s |
| P3 first | not solved: `tokenize` rejected spaces; `evaluate` never written; stray `calc.py` / `test_evaluate.py` in the project root | 792 s (cap) |
| P3 second | not solved: **only round 1 was planned** (2 steps), everything rolled back | 230 s |

What the transcripts show:
1. **The plan covers one round or too much.** P3's planning session made 2 `add_actionable` calls (round 1 only) and stopped; round 2 (`evaluate`) was never planned. P2's made 8 calls hitting the call cap, with filler: a research step titled `inventory.py: research stock count modeling` (so the start-of-title "research" filter missed it), three "verify ..." test steps, and `tests/full_check: run comprehensive validation` (no file).
2. **Step details lose the requirements.** P2's `report` step detail said "generate filtered name:qty list" and the model built tuples. The exact wording lives only in the Goals text in Notes.
3. **Retry prompts show stale evidence.** `facts` (the project outline) is gathered once before attempt 1, so attempt 2 still says "The project has no files yet" although the model wrote files in attempt 1. The model also never sees the file the traceback names, and P2 task 1.3 failed four times with the same `import sorted` error (`ModuleNotFoundError: No module named 'sorted'`).
4. **A code step writes its own tests.** P2 task 1.1 wrote `Inventory` and a test that used a different attribute name; four attempts failed on their own mismatch. The separate test step (1.2) then passed on attempt 1.
5. **Titles can name the wrong file.** P3's step was titled `tokenize.py` (the goal says `tokens.py`); a file called `tokenize.py` also shadows the standard library module.

Planned edits for the next cycle (one PR each): (C) regenerate the evidence on every attempt and include files the failure names; (B) plan hygiene: drop `file: research ...` steps and test steps that name no file; (A) a "plan the rest of the goal" step at the end of the list that plans again, flat, once the earlier steps are done (at most 2 times).

### Edits made after the first P2/P3 runs
- **C (PR #3):** a retry re-gathers the evidence, including the contents of files the failure text names.
- **B (plan hygiene):** a step is dropped if its title is `research ...` or `file.py: research ...`, or if it is a test step that names no file.
- **A (plan the rest):** after a goal is planned, a last step `Plan the rest of the goal` is queued. When the earlier steps are done it runs the planner again on the goal with the line `Already done: ...`, under the same goal so ids stay flat (1.1, 1.2, ...), and queues another such step. A goal is planned at most 3 times (`MAX_REPLANS = 2` re-plans) and has at most 12 plus 2 steps. Planning again that adds nothing new ends the goal. Side effect: `Last task completed` in the notes can be the planning step.

## Rerun round 1 (after edits C, B, A) — P1, P2, P3 once each
| Prompt | Result | Time | Plan |
|---|---|---|---|
| P1 | not solved; all 4 steps blocked, everything rolled back | 637 s | 4 steps (both rounds) + plan-the-rest; done |
| P2 | not solved; `inventory.py` + its tests verified, `report` blocked | 348 s | 4 steps + plan-the-rest |
| P3 | not solved; 785 s cap; the plan-the-rest step added `calc.py` steps (round 2 *is* planned now) but the tokenize steps never passed | 785 s | 2 + (2 more after the first re-plan) |

Planning is now complete and stable: round 2 shows up in all three runs (before: P3 planned round 1 only). The failures are in the worker:
1. **Wasted `run_tests` calls.** In P1 task 1.3, 10 of 24 tool calls were `run_tests` with arguments that cannot work: `pytest tests/test_words.py` (the word `pytest` is taken as a path; exit 4) and `test_words.py` (the file is in `tests/`; exit 4). Each costs a call out of 12 and the result tokens.
2. **The same fixable failure repeated four times.** The model's test used `word_count` without importing it (`NameError: name 'word_count' is not defined`) on all four attempts (P1 1.3), and again in P2 1.4. The pytest message names the problem; the model does not act on it.
3. **"code" and "test" steps do not differ for the worker.** It gets the same instructions for both, and the verifier needs a new passing test for either. A test-only step has no code to pass against (P3 1.1 wrote `tokens.py` itself), and a code step writes its own tests. Splitting one unit of work into two steps doubles the sessions and the chances of a mismatch (P2 1.1 earlier: code and test disagreed on an attribute name).
4. **Model quality limits remain:** logic that stays wrong across four attempts (`slugify` returning `helloworld`, P1 1.1), test code written into the module file, `replace_text` misses (found 0 times) in P3.

Planned edits for the next cycle (one PR each): (D) normalize the model's `run_tests` arguments; (E) when the failure is `NameError: name 'X' is not defined` and a project module defines X, add the import line to the failure text; (F) plan one unit of work as one step, code and its tests together, naming both files.

### Edits made after rerun round 1
- **D (PR #6):** `run_tests` drops a leading `pytest` / `python -m pytest` and finds a bare test file under `tests/`.
- **E:** when a failure is `NameError: name 'X' is not defined` and a top-level module defines `X`, the next prompt gets `Hint: add \`from <module> import X\` at the top of the test file`.
- **F (code and its tests are one step):** the planner is asked for this in so many words, but the model ignored the instruction on two replays (it still listed code and test steps separately). So the harness does it: a `test` step that comes right after a `code` step in the same planning pass is appended to that step's detail (`Also write tests ...`) and no longer a step of its own. A test step with no code step before it (tests for existing code) stays a step. The instruction was left as it was.

## Rerun round 2 (after edits D, E, F) — P1, P2, P3 once each
| Prompt | Result | Time | What was verified |
|---|---|---|---|
| P1 | not solved (cap) | 795 s | `word_count` + its tests verified (1.2); `slugify` blocked after 4 attempts; the plan-the-rest steps added `test_slugs.py` / `test_words.py` steps |
| P2 | not solved (cap) | 794 s | nothing; a garbage step `finalize round 1 and round 2` (no file) was planned |
| P3 | not solved (cap) | 784 s | nothing; 1 handoff, 1 hard `context_overflow`; round 2 steps were planned by the re-plan (1.4, 1.5) but not reached |

Observations:
1. **The planning edits work.** Both rounds are planned in every run and the plan-the-rest step ran 1-2 times without nesting. Steps are now merged (code + its tests): P1 got `write slugify in slugs.py` / `write word_count in words.py` as single steps.
2. **Retries repeat themselves.** P2 task 1.1: 4 attempts, 1 distinct failure text (`assert 1 == 5` for `count('apple')`). P1 `slugify` returned `helloworld` (spaces removed, no hyphen) on attempts 1 and 2 in this run and in every earlier run. With the same prompt and the model's default sampling the 3B model keeps its answer.
3. **Syntax errors from the model dominate P3.** 12 of the 19 rejections were `Syntax error: tokens.py:NN: invalid syntax / unexpected indent`, and the message does not show the line, so the model cannot see what is wrong at line NN.
4. **Steps with no file still get through** (`finalize round 1 and round 2`, kind code).
5. Still open: logic the model gets wrong repeatedly (`slugify`), unicode arrows (`→`) in code, one hard `context_overflow` per P3 run.

Planned edits for the next cycle (one PR each): (G) sample at a higher temperature from the second attempt on; (H) show the offending source line in a syntax error; (I) drop any step that names no file.

### Edits made after rerun round 2
- **G (PR #9):** from the second attempt on, the model is sampled at temperature 0.8 (first attempt at the default).
- **H (PR #10):** a syntax error message ends with the offending line's text.
- **I:** any step that names no file (`finalize round 1 and round 2`) is dropped, not only test steps.

## Rerun round 3 (after edits G, H, I) — P1, P2, P3 once each
| Prompt | Result | Time | What was verified |
|---|---|---|---|
| P1 | not solved | 922 s (cap; a long session started before the deadline) | `slugs.py` written but wrong; `words.py` never reached; 1 handoff, 2 hard `context_overflow` |
| P2 | **closest so far**: `inventory.py` and `report.py` both written and verified on the first attempt, tests for Inventory verified | 324 s | the independent check failed: `report(inventory)` iterates the `Inventory` object, the check passes an `Inventory` (the model's own tests passed lists) |
| P3 | not solved | 740 s | `tokens.py` written; `calc.py` never reached; 2 handoffs nested to `1.1.1.1` |

Observations:
1. **Plan-the-rest and the file-name rule work as intended.** No filler steps. P2's plan had both modules as separate single steps plus tests for existing code.
2. **Test-first plans escape the merge.** P3's plan listed `tests/test_tokens.py` before `tokens.py`, so the test step stood alone (the merge only looked at a test step *after* a code step) and got worked first, with no code to pass against: it spent 3 handoffs (`1.1`, `1.1.1`, `1.1.1.1`).
3. **The exact wording of the requirement is not in the step.** The step said "write report function in report.py"; the goal says `report(inventory)` returns "name: qty" strings sorted by name, skipping zero stock. The model built a function over a list and tested that. The sentence is only in the Notes' Goals text.
4. Hint effects: G (temperature) changed the failures between attempts (P1 1.1: `helloworld`, then `hello-world-`), H showed the bad line (`in \`def test_slugify_punctuation():\``), but the model rarely used either to fix the code.

Planned edits for the next cycle (one PR each): (K) pair a code step with the tests for the same module in any order and position (by module name: `slugs.py` and `tests/test_slugs.py`); (J) append to each step the sentences of the goal that mention the step's files, so the worker gets the exact wording in its task text.

### Edits made after rerun round 3
- **K (PR #12):** a code step and the tests for the same module (`slugs.py` / `tests/test_slugs.py`) pair up in any order and position.
- **J:** each step's detail gets `Spec: <the goal's sentences that name the step's files>` (at most 700 characters), so the worker sees the exact wording (`report(inventory)`, `name: qty strings`, `lowercase, joined by hyphens`) in the task text itself.

## Rerun round 4 (after edits K, J) — P1, P2, P3 once each
| Prompt | Result | Time | What was verified |
|---|---|---|---|
| P1 | **solved** (independent check passed) | 239 s | `slugs.py` (1.1) and `words.py` (1.3) verified on the first attempt each; two later test-only steps were blocked but the code was already correct |
| P2 | not solved | 346 s | nothing: `Inventory.add()` took 2 positional arguments while the model's own tests passed 3 (`add(name, qty)`); then `unterminated triple-quoted string` / stray prose in the files |
| P3 | not solved | 225 s | `tokens.py` verified on the first attempt; `calc.py` failed 4 times (missing `tokenize` import, then `unexpected indent`) |

Observations:
1. A step can now pass on its first attempt (P1 both steps, P2 earlier, P3 `tokens.py`), which did not happen before round 3. When it does, the run is short (239 s).
2. The remaining failures are the 3B model writing malformed or inconsistent code: syntax errors in the file it just wrote (unterminated docstring, `unexpected indent`, prose in the file), a method signature that does not match its own test, a missing import in the code file (the import hint is worded for test files). The spec text in the step did not stop the signature mismatch in P2.
3. After a verified code step the plan-the-rest passes mostly add test-only steps for code that already passes (P1 1.5, 1.6; blocked with `no new test passes`), so they cost attempts without changing the result.

## Summary across the rounds
| Run | P1 | P2 | P3 |
|---|---|---|---|
| Before the flat planner (research on, goal cut to 200 chars) | 0 of 8 | – | – |
| Flat planner, fixed titles/dedupe (PR #2) | solved (cap) | not solved | not solved |
| Round 1 (after C, B, A) | not solved | not solved | not solved |
| Round 2 (after D, E, F) | not solved | not solved | not solved |
| Round 3 (after G, H, I) | not solved | not solved (closest: 2 modules written, verified) | not solved |
| Round 4 (after K, J) | **solved, 239 s** | not solved | not solved |

One run per cell, so single results are noisy. What the evidence supports:
- Planning is no longer the bottleneck (complete, flat, round 2 present, no filler since round 3).
- The loop is now limited by what the model writes inside one step, not by the task list.
- Ideas not done, in order of expected value: (1) make the plan-the-rest step skip a code step whose module already passes (it adds test-only steps that cannot be verified); (2) widen the import hint to code files and extend it to `ImportError`; (3) when a file the task wrote has a syntax error, put the whole broken file (not only the line) in the next prompt; (4) hard `context_overflow` still happens once per run on P3; the soft guard counts only before a tool call; (5) repeat each prompt 3 times to measure the real solve rate before more tuning.

## Edits after round 4 (all four open items)
- **No test-only steps for covered modules (PR #15):** after a code step exists for a module, a test step for the same module is dropped (the code step already includes its tests, and a test-only step cannot pass: `no new test passes`).
- **Import hint (PR #16):** says "the file that uses it" (not only the test file) and also names `ImportError: cannot import name X from module` (define X in module.py).
- **Retry evidence (PR #17):** a retry prompt allows 3,000 characters of project facts (1,800 on the first attempt), so a longer broken file is shown whole (files up to 60 lines).
- **Hard `context_overflow` (this PR), cause found:** the model may ask for several tools at once. All of a batch passed the "budget reached?" check before the first result had been counted, so the guard never fired. Measured with the real model at a 2,600-token budget: 6 calls, estimated 3,877 tokens, not stopped. The helper now runs tool calls one at a time through a gate and checks the budget inside it, and stops 900 tokens early so one more result and the answer still fit. Same test after the fix: stopped after 3 calls at an estimated 2,043; at 7,168 a normal run finishes untouched. The installed copy needs `./install.sh` to rebuild the helper.

## Rerun round 5 (after the four edits above) — P1, P2, P3 once each
| Prompt | Result | Time | What happened |
|---|---|---|---|
| P1 | not solved | 192 s | `slugs.py` verified (first attempt). The first planning pass listed only slugify; the re-plan produced a mangled step (`Plan the rest of the goal: research how to count words, write word_count(text) in words.py`) that failed on a stray ``` line inside `words.py` and `tests/test_words.py` |
| P2 | not solved | 164 s | the first planning pass listed only `Inventory`; the step was blocked (`DID NOT RAISE ValueError`, 3 times the same); the re-plan then added nothing, so `report.py` was never planned and the goal ended |
| P3 | not solved | 1025 s | one `pytest` run **hung for 300 s** (generated code that does not terminate) and the model's first attempts failed `DID NOT RAISE ValueError`; no budget wind-down or `context_overflow` in any of the three runs |

Observations:
1. **The overflow fix held:** 0 hard `context_overflow` in 13 worker sessions (before: 1-2 per P1/P3 run).
2. **Planning is complete only when the model lists every file.** In two of three runs the first pass named one module. The re-plan then saw no *done* step to build on (the one step was blocked), listed nothing new, and the goal ended "done" without ever attempting the other module. The goal itself names every file (`words.py`, `report.py`, `calc.py`), so a gap can be found without the model: a file the goal names that no step names.
3. **A stray fence in the middle of a file:** `create_file` strips a fence that wraps the whole content, but not a closing ``` after the code (P1: `words.py:14: invalid syntax in `` ` `` `).
4. **A hung test costs the run its time:** the default test timeout is 300 s of a 780 s run.

Next edits I would make, in order: (1) after the model plans, add a step for every `.py` file the goal names that no step names (`Create words.py` + the goal's sentence as its spec); (2) cut a file's content at a closing ``` line; (3) lower the test timeout from 300 s to 120 s.

## Tool: `check_syntax`
The worker is offered a `check_syntax(path)` tool (empty path = every Python file) that returns `ok` or each syntax error with its line text, and its instructions say to call it after changing code. Until now a syntax error was only found by the harness after the session ended, which cost a whole attempt (12 of 19 rejections in P3 round 2). The call is logged in the ledger like the other reads. Whether the model uses it, and how often, is part of the next experiment.

## Twelve attempts per step (with `check_syntax`)

One run per prompt, 12 attempts per step, no new session after 3600 s.

| Prompt | Solved | Time | Result |
|---|---|---|---|
| P1 | no | 1595 s | `slugs.py` passed only after 12 failed attempts plus a continuation that passed on its 8th; `words.py` blocked after 5 attempts, so it is missing |
| P2 | no | 3333 s | `inventory.py` and `report.py` both written; the check fails with a circular import (`report.py` imports from `report`) |
| P3 | no | 4188 s (cap hit) | only `tokens.py` written; `tokenize` missing from it; step 1.1 was still running at the cap |

- The model calls `check_syntax` often (37, 97 and 12 calls), but the failures were logic and import mistakes, not syntax errors.
- More attempts did not solve any prompt. The failures repeat the earlier families: a wrong import, a signature mismatch and slow sessions.
- Session ends in P2: 38 ok, 15 call cap, 7 budget wind-down, 3 hard `context_overflow`.
