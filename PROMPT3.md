mkdir four-classes && cd four-classes

apple-code solve "Write four small Python files with plain Python and no extra packages. File 1 is counter.py with class Counter: Counter(start: int = 0) stores start, add(n: int = 1) -> None adds n to the value, value() -> int returns the value, reset() -> None sets the value back to start, for example c = Counter(5) then c.add(2) then c.value() == 7 then c.reset() then c.value() == 5, and Counter().value() == 0, and its tests are in tests/test_counter.py. File 2 is stack.py with class Stack: push(item) -> None adds item on top, pop() returns and removes the top item, peek() returns the top item without removing it, size() -> int returns the number of items, is_empty() -> bool, and pop and peek raise IndexError when the stack is empty, for example s = Stack() then s.push(1) then s.push(2) then s.pop() == 2 then s.size() == 1, and Stack().pop() raises IndexError, and its tests are in tests/test_stack.py. File 3 is inventory.py with class Inventory: add(name: str, qty: int) -> None adds qty to the count of name, remove(name: str, qty: int) -> None subtracts qty and raises ValueError when the count of name is less than qty, count(name: str) -> int returns the count and is 0 for an unknown name, total() -> int returns the sum of all counts, names() -> list returns all names that were ever added, sorted, for example i = Inventory() then i.add('apple', 3) then i.add('apple', 2) then i.count('apple') == 5, and i = Inventory() then i.add('apple', 1) then i.remove('apple', 9) raises ValueError, and Inventory().count('pear') == 0, and its tests are in tests/test_inventory.py. File 4 is report.py with report(inventory: Inventory) -> list that imports Inventory from inventory.py and returns one string 'name: qty' for each name from inventory.names() whose count is more than 0, sorted by name, for example i = Inventory() then i.add('apple', 5) then i.add('fig', 0) then i.add('pear', 4) then report(i) == ['apple: 5', 'pear: 4'], and its tests are in tests/test_report.py."

apple-code test tests

cat > check.py <<'EOF'
from counter import Counter
from stack import Stack
from inventory import Inventory
from report import report

c = Counter(5)
c.add(2)
assert c.value() == 7
c.add()
assert c.value() == 8
c.reset()
assert c.value() == 5 and Counter().value() == 0

s = Stack()
assert s.is_empty() and s.size() == 0
s.push(1)
s.push(2)
assert s.peek() == 2 and s.size() == 2 and s.pop() == 2 and s.size() == 1 and not s.is_empty()
s.pop()
for call in (s.pop, s.peek):
    try:
        call()
        raise SystemExit("an empty stack should raise IndexError")
    except IndexError:
        pass

i = Inventory()
assert i.count("apple") == 0 and i.total() == 0 and i.names() == []
i.add("pear", 4)
i.add("apple", 3)
i.add("apple", 2)
assert i.count("apple") == 5 and i.total() == 9 and i.names() == ["apple", "pear"]
try:
    i.remove("apple", 9)
    raise SystemExit("remove of too many should raise ValueError")
except ValueError:
    pass
assert i.count("apple") == 5
i.add("fig", 1)
i.remove("fig", 1)
assert report(i) == ["apple: 5", "pear: 4"]
print("PROMPT3 independent check: all passed")
EOF

python3 check.py
apple-code recap
