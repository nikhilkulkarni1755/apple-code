import ast

from agent.probe import findings, mutants, propose_properties, sweep

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


class Proposer:
    """Stands in for the model: calls add_property with each (variables, check) it is given."""

    def __init__(self, props):
        self.props, self.tools, self.prompts = props, [], []

    def run(self, prompt, tools, instructions="", max_tool_calls=8, **kw):
        self.prompts.append(prompt)
        self.tools.append([t.name for t in tools])
        self.answers = [tools[0].handler(v, c) for v, c in self.props]
        return {"content": "", "stopped": False, "calls": []}


def test_properties_are_proposed_through_one_tool_and_only_safe_checks_are_kept():
    fm = Proposer([("x: list", "len(first(x)) >= 0"), ("x: list", "open(x) == 1"), ("n: int", "positive(n) == (n > 0)"), ("n: int", "positive(n) or True"),
                   ("n: int", "positive(n) == 1"), ("n: int", "positive(n) == 2")])
    got = propose_properties(fm, "Create m.py")
    assert fm.tools == [["add_property"]] and "Create m.py" in fm.prompts[0] and len(got) == 3   # at most three
    assert got[0] == ({"x": "list"}, "len(first(x)) >= 0") and fm.answers[1].startswith("not accepted") and fm.answers[0] == "property 1 added"


def test_a_property_that_fails_names_the_values_and_one_that_holds_is_silent(tmp_path):
    (tmp_path / "m.py").write_text(PROJECT)
    props = [({"x": "list"}, "len(first(x)) >= 0"), ({"n": "int"}, "positive(n) == (n > 0)"), ({"n": "int"}, "positive(n) == (n > 5)"), ({"text": "str"}, "len(shout(text)) == len(text)")]
    got = findings(tmp_path, "m.py", "first, positive, shout", EXAMPLES, props)
    failing = [f for f in got if "the property" in f]
    assert len(failing) == 2 and not any("positive(n) == (n > 0)" in f for f in failing)
    assert any("`positive(n) == (n > 5)` does not hold for {'n':" in f and "the code or the property is wrong" in f for f in failing)
    assert any("`len(first(x)) >= 0` raises" in f for f in failing)


def test_one_finding_for_each_function_and_error_with_the_shortest_input(tmp_path):
    (tmp_path / "m.py").write_text("def second(items: list) -> int:\n    return items[1]\n")
    got = findings(tmp_path, "m.py", "second", ["second([1, 2]) == 2"])
    assert got == ["soft: second([]) raises IndexError, which the task does not mention (and 1 other inputs of second)"]
