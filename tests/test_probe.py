import ast

from agent.probe import findings, mutants, sweep

PROJECT = ("def first(items: list) -> int:\n    return items[0]\n\n\ndef positive(n: int) -> bool:\n    return n > 0\n\n\ndef shout(text: str) -> int:\n    return text.upper()\n")
SPIN = "def spin(n: int) -> int:\n    while n > 100:\n        pass\n    return n\n"
TESTS = ("from m import first, positive\n\n\ndef test_first():\n    assert first([5]) == 5\n\n\n"
         "def test_positive():\n    assert positive(5) is True and positive(-5) is False\n")
EXAMPLES = ["first([5]) == 5", "positive(5) == True", "shout('a') == 'A'"]


def test_every_small_change_is_a_working_program_that_differs_in_one_place_and_says_where():
    source = "def f(a, b):\n    if a < 3 and b:\n        return a + 1\n    return not b\n"
    got = mutants(source)
    assert sorted(what for _, what, _ in got) == sorted(["`a < 3` becomes `a <= 3`", "`a < 3 and b` becomes `a < 3 or b`", "`a + 1` becomes `a - 1`", "`1` becomes `2`", "`3` becomes `4`", "`not b` becomes `+b`"])
    assert all(ast.parse(changed) and changed != ast.unparse(ast.parse(source)) for _, _, changed in got) and mutants("x = 'text'\n") == []


def test_the_sweep_calls_each_function_with_edge_inputs_and_notes_what_it_did(tmp_path):
    (tmp_path / "m.py").write_text(PROJECT + "\n\n" + SPIN)
    got = {r["call"]: r for r in sweep(tmp_path, "m", EXAMPLES + ["spin(1) == 1"])}
    assert got["first([])"]["out"] == "raises IndexError" and got["first([0])"]["out"] == "= 0" and got["spin(1000)"]["out"] == "hang"
    assert got["shout('a')"]["notes"] == ["returns a str, annotated int"] and "positive(0)" in got


def test_findings_name_a_hang_an_error_nobody_mentioned_and_a_change_no_test_notices(tmp_path):
    (tmp_path / "m.py").write_text(PROJECT)
    (tmp_path / "test_m.py").write_text(TESTS)
    got = findings(tmp_path, "m.py", "first and positive; shout raises nothing", EXAMPLES)
    assert "soft: first([]) raises IndexError, which the task does not mention" in got
    assert "soft: shout('a') returns a str, annotated int" in got
    assert any("`n > 0` becomes `n >= 0`" in f and "positive(0) gives = False, and = True with the change" in f for f in got)
    assert findings(tmp_path, "m.py", "first raises IndexError", EXAMPLES[:1])[0] != "soft: first([]) raises IndexError, which the task does not mention"
    assert (tmp_path / "m.py").read_text() == PROJECT   # the real file is never touched


def test_a_module_that_cannot_be_imported_has_no_findings(tmp_path):
    (tmp_path / "m.py").write_text("import nothing_like_this\n")
    assert findings(tmp_path, "m.py", "", []) == []


def test_a_function_that_does_not_finish_is_a_hard_finding(tmp_path):
    (tmp_path / "m.py").write_text(SPIN)
    assert findings(tmp_path, "m.py", "spin", ["spin(1) == 1"])[0] == "hard: spin(1000) does not finish"
