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
