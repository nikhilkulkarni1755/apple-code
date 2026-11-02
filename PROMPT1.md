mkdir four-helpers && cd four-helpers

apple-code solve "Write four small Python files with plain Python and no extra packages. File 1 is maths.py with total(a: int, b: int) -> int that returns a plus b, and average(numbers: list) -> float that returns the sum divided by the count, or 0.0 for an empty list, for example total(2, 3) == 5, total(-1, 1) == 0, average([1, 2, 3]) == 2.0, average([]) == 0.0, and its tests are in tests/test_maths.py. File 2 is text.py with count_vowels(text: str) -> int that counts the letters a e i o u in any case, and reverse_words(text: str) -> str that reverses the order of the words and joins them with one space, for example count_vowels('Hello') == 2, count_vowels('') == 0, reverse_words('a b c') == 'c b a', reverse_words('') == '', and its tests are in tests/test_text.py. File 3 is lists.py with unique(items: list) -> list that removes repeats and keeps the first of each, and chunk(items: list, size: int) -> list that splits items into lists of at most size items and raises ValueError when size is less than 1, for example unique([3, 1, 3, 2, 1]) == [3, 1, 2], chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]], chunk([], 3) == [], and its tests are in tests/test_lists.py. File 4 is primes.py with is_prime(n: int) -> bool that is True only for primes (so 0, 1 and negative numbers are False), and primes_up_to(n: int) -> list that returns the primes from 2 up to n including n and uses is_prime, for example is_prime(2) == True, is_prime(9) == False, is_prime(1) == False, primes_up_to(10) == [2, 3, 5, 7], primes_up_to(1) == [], and its tests are in tests/test_primes.py."

apple-code test tests

cat > check.py <<'EOF'
from maths import total, average
from text import count_vowels, reverse_words
from lists import unique, chunk
from primes import is_prime, primes_up_to

assert total(2, 3) == 5 and total(-1, 1) == 0
assert average([1, 2, 3]) == 2.0 and average([]) == 0.0 and average([1, 2]) == 1.5
assert count_vowels("Hello") == 2 and count_vowels("") == 0 and count_vowels("AEIOU xyz") == 5
assert reverse_words("a b c") == "c b a" and reverse_words("") == "" and reverse_words("one") == "one"
assert unique([3, 1, 3, 2, 1]) == [3, 1, 2] and unique([]) == []
assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]] and chunk([], 3) == [] and chunk([1, 2], 5) == [[1, 2]]
try:
    chunk([1], 0)
    raise SystemExit("chunk with size 0 should raise ValueError")
except ValueError:
    pass
assert is_prime(2) is True and is_prime(9) is False and is_prime(1) is False and is_prime(0) is False and is_prime(-7) is False and is_prime(13) is True
assert primes_up_to(10) == [2, 3, 5, 7] and primes_up_to(1) == [] and primes_up_to(2) == [2] and primes_up_to(13)[-1] == 13
print("PROMPT1 independent check: all passed")
EOF

python3 check.py
apple-code recap
