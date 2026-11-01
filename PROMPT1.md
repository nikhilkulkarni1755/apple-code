mkdir slug-words && cd slug-words

apple-code solve "Build two small Python helpers in two rounds. Each round has three tasks in order: research, write the code, write the tests. Round 1: research how to turn text into a URL slug, write slugify(text) in slugs.py (lowercase, words joined by single hyphens, punctuation removed), then write tests for it in tests/test_slugs.py. Round 2: research how to count words, write word_count(text) in words.py (words are separated by any whitespace, empty text is 0), then write tests for it in tests/test_words.py."

apple-code test tests

cat > check.py <<'EOF'
from slugs import slugify
from words import word_count

assert slugify("Hello, World!") == "hello-world"
assert slugify("  A   b ") == "a-b"
assert slugify("") == ""
assert word_count("a b  c") == 3
assert word_count("") == 0
assert word_count(" one\ttwo\nthree ") == 3
print("PROMPT1 independent check: all passed")
EOF

python3 check.py
apple-code recap
