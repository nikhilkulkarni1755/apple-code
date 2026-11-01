mkdir inventory && cd inventory

apple-code solve "Build a small inventory module in two rounds. Each round has three tasks in order: research, write the code, write the tests. Round 1: research how to model stock counts, write an Inventory class in inventory.py with add(name, qty), remove(name, qty) which raises ValueError when there is not enough stock, count(name) which is 0 for an unknown name, and total(), then write tests in tests/test_inventory.py. Round 2: write report(inventory) in report.py that returns a list of name: qty strings sorted by name and skipping items with zero stock, then write tests in tests/test_report.py."

apple-code test tests

cat > check.py <<'EOF'
from inventory import Inventory
from report import report

i = Inventory()
assert i.count("apple") == 0 and i.total() == 0
i.add("apple", 3)
i.add("apple", 2)
i.add("pear", 4)
assert i.count("apple") == 5 and i.total() == 9
i.remove("apple", 5)
assert i.count("apple") == 0 and i.total() == 4
try:
    i.remove("pear", 9)
    raise SystemExit("remove of too many should raise ValueError")
except ValueError:
    pass
assert i.count("pear") == 4
i.add("fig", 1)
assert report(i) == ["fig: 1", "pear: 4"]
print("PROMPT2 independent check: all passed")
EOF

python3 check.py
apple-code recap
