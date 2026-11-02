mkdir vague-classes && cd vague-classes

apple-code solve "Write four small Python files. counter.py has a Counter class with add, value and reset, for example c = Counter() then c.add(2) then c.value() == 2. stack.py has a Stack class with push, pop, peek, size and is_empty, for example s = Stack() then s.push(1) then s.push(2) then s.pop() == 2. inventory.py has an Inventory class with add, remove, count, total and names, for example i = Inventory() then i.add('apple', 3) then i.count('apple') == 3. report.py has a report function that takes an Inventory and lists what is in stock, for example i = Inventory() then i.add('pear', 4) then report(i) == ['pear: 4']. Put the tests in tests/test_counter.py, tests/test_stack.py, tests/test_inventory.py and tests/test_report.py."

apple-code test tests

cat > check.py <<'EOF'
from counter import Counter
from stack import Stack
from inventory import Inventory
from report import report

c = Counter()
c.add(2)
c.add(3)
assert c.value() == 5
c.reset()
assert c.value() == 0
s = Stack()
assert s.is_empty()
s.push(1)
s.push(2)
assert s.peek() == 2 and s.size() == 2 and s.pop() == 2 and s.size() == 1
s.pop()
try:
    s.pop()
    raise SystemExit("popping an empty stack should raise an error")
except Exception as e:
    assert not isinstance(e, SystemExit)
i = Inventory()
i.add("pear", 4)
i.add("apple", 3)
i.add("apple", 2)
assert i.count("apple") == 5 and i.count("kiwi") == 0 and i.total() == 9 and set(i.names()) == {"apple", "pear"}
try:
    i.remove("apple", 9)
    raise SystemExit("removing more than is in stock should raise an error")
except Exception as e:
    assert not isinstance(e, SystemExit)
i.add("fig", 1)
i.remove("fig", 1)
text = str(report(i))
assert "apple" in text and "5" in text and "pear" in text and "fig" not in text and text.index("apple") < text.index("pear")
print("PROMPT9 independent check: all passed")
EOF

python3 check.py
apple-code recap
