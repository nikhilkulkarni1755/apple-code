mkdir calc-chain && cd calc-chain

apple-code solve "Write three small Python files with plain Python and no extra packages. File 1 is tokens.py with tokenize(text: str) -> list that returns the numbers (int, or float when there is a decimal point) and the characters + - * / ( ) in order, skips spaces, and raises ValueError on any other character, for example tokenize('2+3*4') == [2, '+', 3, '*', 4], tokenize('(1 + 2.5)') == ['(', 1, '+', 2.5, ')'], tokenize('') == [], tokenize('2 # 3') raises ValueError, and its tests are in tests/test_tokens.py. File 2 is calc.py with evaluate(text: str) -> float that uses tokenize from tokens.py, does * and / before + and -, goes left to right, does brackets first, raises ZeroDivisionError for division by zero and raises ValueError for a malformed expression, for example evaluate('2 + 3 * 4') == 14, evaluate('(2+3)*4') == 20, evaluate('10 / 4') == 2.5, evaluate('2 - 3 - 1') == -2, evaluate('1/0') raises ZeroDivisionError, evaluate('2 *') raises ValueError, evaluate('(1+2') raises ValueError, and its tests are in tests/test_calc.py. File 3 is run.py with run(text: str) -> str that uses evaluate from calc.py and returns the result as text with whole numbers shown without a decimal point, and returns 'error' when evaluate raises ZeroDivisionError or ValueError, for example run('2 + 3') == '5', run('10 / 4') == '2.5', run('6 / 3') == '2', run('1/0') == 'error', run('2 *') == 'error', and its tests are in tests/test_run.py."

apple-code test tests

cat > check.py <<'EOF'
from tokens import tokenize
from calc import evaluate
from run import run

assert tokenize("2+3*4") == [2, "+", 3, "*", 4] and tokenize("(1 + 2.5)") == ["(", 1, "+", 2.5, ")"] and tokenize("") == []
assert type(tokenize("2")[0]) is int and type(tokenize("2.5")[0]) is float
def raises(err, f, *a):
    try:
        f(*a)
    except err:
        return True
    return False
assert raises(ValueError, tokenize, "2 # 3")
assert evaluate("2 + 3 * 4") == 14 and evaluate("(2+3)*4") == 20 and evaluate("10 / 4") == 2.5 and evaluate("2 - 3 - 1") == -2 and evaluate("8 / 2 / 2") == 2
assert raises(ZeroDivisionError, evaluate, "1/0") and raises(ValueError, evaluate, "2 *") and raises(ValueError, evaluate, "(1+2")
assert run("2 + 3") == "5" and run("10 / 4") == "2.5" and run("6 / 3") == "2" and run("1/0") == "error" and run("2 *") == "error"
print("PROMPT2 independent check: all passed")
EOF

python3 check.py
apple-code recap
