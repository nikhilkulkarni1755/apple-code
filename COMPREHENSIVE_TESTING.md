# Comprehensive testing for a small local model

Status: plan. Written before the build; the results of each stage are appended under "Results" as they come in.

## Why

The ladder runs (`AFTER_RESEARCH_COMMENTED_OUT.md`, "The ladder") solved 0 of 12 prompts. Most code files passed on the first attempt. What
failed was the test file the model wrote for them:

| Kind | Examples from the runs | Can the model fix it? |
|---|---|---|
| Mechanical | Forgot `from PIL import Image`. Used an `image` fixture that does not exist. Did not import `bar_chart`. Test file failed at collection. | Mostly yes. It is sloppiness, and the harness can catch it. |
| Semantic | Asserted that `chunk` raises for size < 1 when the goal never says so. Used `Stack.items`, which does not exist. Wrong `predict` expectation. | No. The model has no source of truth for the expected value, so it invents a plausible one. |

A test the model writes from the same spec carries the same reading of the spec as the code it tests. If the reading is right the test adds
little; if it is wrong, test and code agree on the wrong thing, or the test invents a rule the code cannot meet. Earlier measurements
(`PLAN.md` section 20) agree: supplied correct tests solved 11 of 14; examples the model proposed itself solved 3 of 10, with one false positive.

So the expected values must come from somewhere other than the model. The author gives a couple of examples with every prompt. That is good
for the author (a few lines of plain Python comparisons, not a test suite) and it is the one oracle (source of truth) we have.

## Principle: three tiers

| Tier | Source of truth | On failure |
|---|---|---|
| **A. Enforced** | The author's examples, plus checks that need no expected value (syntax, undefined names, no hang, signature matches) | The step is rejected. |
| **B. Checked, reported** | Properties the model proposes (statements true for every input) | A property that fails on random inputs is a finding with the failing values: the code or the property is wrong, and the author decides. It never blocks a step. |
| **C. Reported** | An edge-input sweep: the harness runs the code on generated inputs and records what happens | A finding for the author, never a rejection. |

A flaw is then a finding with evidence ("`chunk([1, 2], 0)` raises `ZeroDivisionError`; the spec is silent"), not a failing test the model made up.
Findings are of two kinds:

- **Hard**: the code contradicts an example, raises an uncaught exception, or hangs.
- **Soft**: the behaviour is undefined by the spec. The author decides what it should be.

The examples are not the whole spec. They show how the function should work in most cases, with a few known flaws; everything the model
finds beyond them is reported, not assumed.

## Every kind of testing, and where it fits an 8k-token local model

| Type | What it catches | Built by | Fit |
|---|---|---|---|
| Example tests (the author's) | Wrong core behaviour | Harness, from the examples in the goal | Essential. Doctests are a cheap variant. |
| Static checks (undefined names, syntax, later `mypy`) | Missing imports, type mismatches | Harness, deterministic | Fixes most mechanical ladder failures. |
| Contract / signature | Wrong names or arguments | Harness (`inspect`) | Cheap. |
| Edge-input sweep | `None`, empty, 0, negative, huge, unicode, wrong types | Harness generates inputs from the annotations | Cheap, no model needed. Results go to tier C. |
| Property-based (`hypothesis`) | Invariants: idempotence, round trip, sorted output, length preserved | Model proposes the property, harness generates the inputs | Good. Properties are short. |
| Metamorphic | Relations such as `total(a, b) == total(b, a)`, `reverse(reverse(x)) == x` | Model | Good. Needs no expected value. |
| Differential | Logic errors | Model writes a naive reference, harness compares | Moderate: both may share one misreading. |
| Fuzz / robustness | Crashes and hangs on random input | Harness | Cheap. |
| Timeout / complexity | Infinite loops (the `tokenize` run) | Harness | Cheap. Needs a per-test limit, not only the 300 s run limit. |
| Stateful sequences | Bugs in classes from random call orders (Stack, Counter, Inventory) | Harness picks the calls, model supplies the invariant | Good for P3 and P9. |
| Coverage | Code no test touches | Harness | Grounds tests in the code. |
| Mutation | Tests that pass but check nothing | Harness (AST mutations such as `+` to `-`) | Measures whether every test is grounded in the code. |
| Integration | Modules that do not fit together (the P2 chain) | Model, from an example | Needed for multi-file goals. |
| Characterisation / snapshot | Unintended change on later edits | Harness records current outputs | Useful once a feature is accepted. |
| Regression | Re-broken fixes | Harness keeps old tests | Already partly there (`frozen_check`). |
| Security (`bandit`, path traversal, injection) | Unsafe input handling | Harness | Later. |
| Model code review | Flaws against the spec text | Model reads the code and lists suspicions | Report only, never enforce. |
| End to end / system | The whole thing works as a user runs it | Model plus an environment | See below. |
| Frontend: browser, visual, accessibility | UI behaviour | Playwright | Out of scope for now. |
| Load, compatibility, API contract | Scale, versions, schemas | Specialised | Out of scope for now. |

## How the author writes examples

Any sentence of the goal may carry examples. The harness finds them, so the author writes plain Python, not a test file:

| Form | Example |
|---|---|
| A result | `total(2, 3) == 5` (also `is`, `in`, `<`, ...) |
| An error | `chunk([1], 0) raises ValueError` |
| Steps in a row, for a class | `c = Counter(5) then c.add(2) then c.value() == 7` |
| A library object | `from PIL import Image then to_gray(Image.new('L', (1, 1))).mode == 'L'` |
| A program | `cli('wc.py', 'data.txt', files={'data.txt': 'a b c'})[1].strip() == '3'` (exit code, output) |

- Separate examples with commas, `and`, `;` or sentence ends. Brackets and quotes may contain commas.
- A comparison needs a plain value on one side. An example must end in a check and contain a call.
- An example that uses a name set up nowhere (`image` with no `image = ...` before it, or a bare `pop()` with no object) is dropped, because its
  test could only fail. One step that is not code voids the whole example: a step is never skipped silently.
- Imports are allowed only from a short list of safe modules (`PIL`, `numpy`, `sklearn`, `pydicom`, `math`, `re`, `json`, `datetime`,
  `collections`, `itertools`, `functools`, `string`, `decimal`, `fractions`, `statistics`).
- The harness writes `test_<module>_spec.py` before the model starts. The model then writes only the code; the test files the goal names are not
  written by the model.

## Grounding a test in the code

Grounding comes from feedback the harness computes, not from asking the model to try harder:

1. Run coverage and mutation testing.
2. Show the model the uncovered lines and the surviving mutants: "changing `<` to `<=` on line 12 breaks nothing; what input tells them apart?"
3. The model writes one test at a time, so the prompt stays small enough for 8k.
4. A new test is kept only if it covers a new line or kills a new mutant, and does not contradict an example.

## End to end and environments

The harness owns the environment profile; the model only writes the test.

- It detects the project type from files (`pyproject.toml`, `package.json`, a CLI entry point).
- A Python CLI or library gets an isolated venv (the workspace `.venv`, created with `uv`). The test runs the program with `subprocess` in a
  temporary folder and checks stdout and the exit code.
- A frontend needs Node and a browser. When the environment is missing the harness reports "environment missing, not run" and does not fail
  the step, so a missing environment never looks like bad code.
- Python and CLI first. A frontend is a separate project.

## Build order and test rounds

| Stage | Build | Then run |
|---|---|---|
| 1 | Items 1 to 3: examples in the goal become tests (commas, sentences); a static check for undefined names in every file the model writes; a time limit for each test | 2 runs each of P1, P2, P3, P8, P9 and the niche prompts P4 to P7 |
| 2 | Items 4 and 5: the edge-input sweep reported as findings; coverage and mutation as the feedback loop | Same set, 2 runs each |
| 3 | Items 6 and 7: properties the model proposes, run on random inputs by the harness (no `hypothesis` package needed); end to end for command line programs (an example may call `cli('tool.py', 'arg', files={...})`) | Same set plus P10 (a command line program), 2 runs each |

A run counts as solved when the independent `check.py` in the prompt file passes, not when the harness says verified.

## Results

### Stage 1: examples become tests, a name check, a time limit (PRs #30, #31)

Two runs of each prompt, 4 attempts per step, 1800 s cap. "Solved" means the independent `check.py` of the prompt passes.

| Prompt | Run a | Run b | What stopped the unsolved runs (all in the model's code) |
|---|---|---|---|
| P1 functions | solved | no | `unique`/`chunk` returned a generator, not a list |
| P2 chain | no | no | `tokenize` and `evaluate` wrong; `calc.py` never imported `tokenize` after four tries |
| P3 classes | no | no | `from typing import int`; `report` called `.items()` on a list |
| P4 Pillow | no | no | `histogram_counts` returned 0 where 1 was expected |
| P5 numpy, scikit-learn | no | no | `min_max` returned a list; `UnboundLocalError` in `evaluate` |
| P6 pydicom | no | no | misused `PersonName` |
| P7 Pillow drawing | no | solved | a module whose step failed was missing for the next |
| P8 vague functions | solved | solved | |
| P9 vague classes | no | no | a failed step leaves its module missing for the steps that import it |

**4 of 18 solved, against 0 of 12 in the ladder before.** The shape of the failures changed: no run was stopped by a test the model
wrote (the harness writes them now). Every rejection is the model's code failing the author's examples, and the error text the model gets back
is exact (`assert 0 == 1`, `'list' object has no attribute 'items'`). It still repeated the same mistake over four attempts in most
cases, so the next limit is the model's ability to act on a precise failure, not the tests.

Two harness bugs the runs exposed, fixed before the numbers above (PR #31): a later step rewrote a module an earlier step had verified (circular
imports in P2, P3, P4), and a step's Spec picked up the examples of the next file's sentence. An earlier partial pass with them is not counted.

Also seen: a step whose file an earlier step already made is rejected with "no files were changed" even though its tests pass; and a blocked
step rolls back its file, so the steps that import it fail too.

### Stage 2: edge-input sweep and small changes to the code, reported (PR #33)

Same set, two runs each. Findings never reject a step, so the solve rate should not move: **3 of 18 solved** (P1 twice, P8 once), against 4 of 18 in
stage 1, which is run-to-run noise. The 18 runs logged 67 findings.

- **Useful**: `average([]) raises ZeroDivisionError` and `chunk([1, 2, 3], 0) raises ValueError` on the vague prompt P8, where the task states neither;
  `primes_up_to(2)` gives `[2]` and `[]` with `n + 1` changed to `n - 1`, so no example settles the upper bound; `size < 1` against `size <= 1`
  in `chunk`. Each names an input the author could turn into an example.
- **Noise**: the same error repeated for every edge input of a function (`age_on('', ...)`, `age_on(' ', ...)`, ...: eight lines for one
  fact), and a distinguishing input chosen by order, so it was sometimes a 1000-item list. Fixed in the next change: one finding for each function
  and error, with the shortest input.
- **Limit**: the sweep and the changes find unpinned code, not missing behaviour. A function that forgets a case (no `.lower()`) shows nothing,
  because nothing in the code was changed to expose it. Classes get little, since a method is only called on a fresh object.

