mkdir word-count-cli && cd word-count-cli

apple-code solve "Write one small Python command line program with plain Python and no extra packages. wc.py is run as python wc.py FILE and prints the number of words in the text file FILE, is run as python wc.py --lines FILE and prints the number of lines in FILE instead, and exits with code 1 when FILE does not exist. Here cli runs the program and returns its exit code and its output. For example cli('wc.py', 'data.txt', files={'data.txt': 'a b c'})[1].strip() == '3', and cli('wc.py', '--lines', 'data.txt', files={'data.txt': 'a b'})[1].strip() == '1', and cli('wc.py', 'data.txt', files={'data.txt': ''})[1].strip() == '0', and cli('wc.py', 'missing.txt')[0] == 1, and its tests are in tests/test_wc.py."

apple-code test tests

cat > check.py <<'PYEOF'
import pathlib, subprocess, sys, tempfile

def run(*args, text=None):
    with tempfile.TemporaryDirectory() as tmp:
        shutil_src = pathlib.Path("wc.py").read_text()
        pathlib.Path(tmp, "wc.py").write_text(shutil_src)
        if text is not None:
            pathlib.Path(tmp, "data.txt").write_text(text)
        done = subprocess.run([sys.executable, "wc.py", *args], cwd=tmp, capture_output=True, text=True, timeout=10)
    return done.returncode, done.stdout.strip()

assert run("data.txt", text="a b c") == (0, "3")
assert run("data.txt", text="") == (0, "0")
assert run("data.txt", text="one two\nthree four five\n") == (0, "5")
assert run("--lines", "data.txt", text="a\nb\nc\n") == (0, "3")
assert run("--lines", "data.txt", text="") == (0, "0")
assert run("--lines", "data.txt", text="a b") == (0, "1")
code, out = run("nothing.txt")
assert code == 1 and out == ""
print("PROMPT10 independent check: all passed")
PYEOF

python3 check.py
apple-code recap
