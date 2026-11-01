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
