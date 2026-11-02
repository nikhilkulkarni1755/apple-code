"""Calls the public functions (and the methods of the classes) of one module with edge inputs and prints what happened, as JSON.
Run from the project's folder:  python probe_run.py MODULE '["f(1, 2) == 3", ...]' '[{"vars": {"x": "list"}, "check": "len(f(x)) >= 0"}, ...]'
(the examples tell what the arguments look like; each property is tried on random values of its variables)"""
import os
import sys

sys.path[0] = os.getcwd()   # as `python -m` does: the project's folder, not this file's
import ast
import copy
import importlib
import inspect
import json
import random
import signal

EDGE = {int: [0, 1, -1, 2, 1000], float: [0.0, 1.5, -2.5], str: ["", " ", "a", "A b", "é"], bool: [True, False], list: [[], [0], [1, 1], [3, 1, 2]],
        tuple: [(), (1, 2)], dict: [{}, {"a": 1}]}
MAX_CALLS = 80
RUNS = 40
SECONDS = 2


class Hang(BaseException):   # not an Exception: the code under test cannot swallow it
    pass


def stop(*_):
    raise Hang


def example_args(examples):
    """{function or method name: the literal arguments of its first call in the examples}."""
    out = {}
    for ex in examples:
        for call in (n for n in ast.walk(ast.parse(ex)) if isinstance(n, ast.Call)):
            name = getattr(call.func, "id", getattr(call.func, "attr", None))
            try:
                out.setdefault(name, [ast.literal_eval(a) for a in call.args])
            except ValueError:
                pass
    return out


def variants(fn, seen):
    """Argument lists for fn: each parameter in turn takes every edge value of its type while the others keep the example's value (or a plain one)."""
    params = [p for p in inspect.signature(fn).parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.name != "self"]
    given = seen.get(fn.__name__, [])
    kinds = [p.annotation if p.annotation in EDGE else type(given[i]) if i < len(given) and type(given[i]) in EDGE else None for i, p in enumerate(params)]
    if not params or None in kinds:
        return []
    base = [given[i] if i < len(given) else EDGE[k][1] for i, k in enumerate(kinds)]
    return [base[:i] + [v] + base[i + 1:] for i, k in enumerate(kinds) for v in EDGE[k]]


def attempt(make, fn_name, args):
    """What calling fn on a fresh object does: {'call', 'out', 'notes'}."""
    shown = f"{fn_name}({', '.join(map(repr, args))})"
    before, notes = repr(args), []
    signal.signal(signal.SIGALRM, stop)
    signal.alarm(SECONDS)
    try:
        fn = make()
        result = fn(*args)
        out = "= " + repr(result)[:60]
        want = inspect.signature(fn).return_annotation
        if want in EDGE and (not isinstance(result, want) or (want is int and isinstance(result, bool))):
            notes.append(f"returns a {type(result).__name__}, annotated {want.__name__}")
        if repr(args) != before:
            notes.append("changes an argument it was given")
    except Hang:
        out = "hang"
    except Exception as e:
        out = "raises " + type(e).__name__
    finally:
        signal.alarm(0)
    return {"call": shown, "out": out, "notes": notes}


def properties(module, props):
    """For each property: whether its check holds for RUNS random values of its variables, and the first values for which it does not."""
    rng = random.Random(0)
    make = {"int": lambda: rng.choice([0, 1, -1, 2, rng.randint(-5, 50)]), "float": lambda: rng.choice([0.0, 1.5, -2.5, rng.uniform(-9, 9)]),
            "str": lambda: "".join(rng.choice("ab Ab1é") for _ in range(rng.randint(0, 6))), "bool": lambda: rng.random() < 0.5,
            "list": lambda: [rng.randint(-3, 9) for _ in range(rng.randint(0, 5))]}
    out = []
    for prop in props:
        result = {"check": prop["check"], "input": None, "out": "ok"}
        for _ in range(RUNS):
            values = {k: make[t]() for k, t in prop["vars"].items()}
            shown = repr(values)
            signal.signal(signal.SIGALRM, stop)
            signal.alarm(SECONDS)
            try:
                held = eval(compile(prop["check"], "<property>", "eval"), dict(vars(module)), copy.deepcopy(values))
                outcome = "ok" if held else "false"
            except Hang:
                outcome = "hang"
            except Exception as e:
                outcome = "raises " + type(e).__name__
            finally:
                signal.alarm(0)
            if outcome != "ok":
                result.update(input=shown, out=outcome)
                break
        out.append(result)
    return out


def main():
    module, seen = importlib.import_module(sys.argv[1]), example_args(json.loads(sys.argv[2]))
    jobs = []
    for name, obj in vars(module).items():
        if name.startswith("_") or getattr(obj, "__module__", None) != module.__name__:
            continue
        if inspect.isfunction(obj):
            jobs += [(name, (lambda o=obj: o), a) for a in variants(obj, seen)]
        elif inspect.isclass(obj):
            for m, fn in inspect.getmembers(obj, inspect.isfunction):
                if not m.startswith("_"):
                    jobs += [(f"{name}().{m}", (lambda c=obj, m=m: getattr(c(), m)), a) for a in variants(fn, seen)]
    print(json.dumps({"calls": [attempt(make, n, copy.deepcopy(a)) for n, make, a in jobs[:MAX_CALLS]],
                      "properties": properties(module, json.loads(sys.argv[3])) if len(sys.argv) > 3 else []}))


main()
