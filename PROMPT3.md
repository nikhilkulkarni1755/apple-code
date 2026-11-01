mkdir calculator && cd calculator

apple-code solve "Build a small arithmetic calculator in two rounds. Each round has three tasks in order: research, write the code, write the tests. Round 1: research how to split an expression into tokens, write tokenize(text) in tokens.py returning a list of numbers (int, or float when there is a decimal point) and the operator or bracket characters + - * / ( ), raising ValueError on any other character, then write tests in tests/test_tokens.py. Round 2: research operator precedence, write evaluate(text) in calc.py using tokenize, with * and / before + and -, left to right, and brackets, raising ZeroDivisionError for division by zero and ValueError for a malformed expression, then write tests in tests/test_calc.py."

apple-code test tests

cat > check.py <<'EOF'
from tokens import tokenize
from calc import evaluate

assert tokenize("2+3*4") == [2, "+", 3, "*", 4]
assert tokenize("(1 + 2.5)") == ["(", 1, "+", 2.5, ")"]
try:
    tokenize("2 $ 3")
    raise SystemExit("tokenize of a bad character should raise ValueError")
except ValueError:
    pass
assert evaluate("2 + 3 * 4") == 14
assert evaluate("(2+3)*4") == 20
assert evaluate("10 / 4") == 2.5
assert evaluate("2 - 3 - 1") == -2
for bad, err in (("1/0", ZeroDivisionError), ("2 *", ValueError), ("(1+2", ValueError)):
    try:
        evaluate(bad)
        raise SystemExit(f"evaluate({bad!r}) should raise {err.__name__}")
    except err:
        pass
print("PROMPT3 independent check: all passed")
EOF

python3 check.py
apple-code recap
