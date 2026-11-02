mkdir vague-helpers && cd vague-helpers

apple-code solve "Write four small Python files. maths.py has total(a, b) and average(numbers). text.py has count_vowels(text) and reverse_words(text). lists.py has unique(items) and chunk(items, size). primes.py has is_prime(n) and primes_up_to(n). Put the tests in tests/test_maths.py, tests/test_text.py, tests/test_lists.py and tests/test_primes.py."

apple-code test tests

cat > check.py <<'EOF'
from maths import total, average
from text import count_vowels, reverse_words
from lists import unique, chunk
from primes import is_prime, primes_up_to

assert total(2, 3) == 5 and average([1, 2, 3]) == 2
assert count_vowels("Hello") == 2 and reverse_words("a b c") == "c b a"
assert unique([3, 1, 3, 2, 1]) == [3, 1, 2] and chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
assert is_prime(7) and not is_prime(9) and not is_prime(1) and primes_up_to(10) == [2, 3, 5, 7]
print("PROMPT8 independent check: all passed")
EOF

python3 check.py
apple-code recap
