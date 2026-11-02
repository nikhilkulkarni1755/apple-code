mkdir ml && cd ml

apple-code solve "Write three small Python files that use the numpy and scikit-learn packages. File 1 is scale.py with min_max(values: list) -> numpy array that scales the values to the range 0 to 1 with (x - min) / (max - min), and returns all zeros when every value is the same, for example min_max([2, 4, 6]).tolist() == [0.0, 0.5, 1.0], and min_max([3, 3]).tolist() == [0.0, 0.0], and its tests are in tests/test_scale.py. File 2 is model.py with train(features: list, labels: list) that fits a scikit-learn LogisticRegression and returns it, and predict(model, features: list) -> list that returns the predicted labels as a plain list, for example predict(train([[0], [1], [10], [11]], [0, 0, 1, 1]), [[0], [11]]) == [0, 1], and its tests are in tests/test_model.py. File 3 is score.py with accuracy(true: list, predicted: list) -> float that returns the share of equal items, 0.0 for empty lists, and raises ValueError when the lengths differ, and evaluate(features: list, labels: list) -> float that uses train and predict from model.py on the same data and returns the accuracy, for example accuracy([1, 0, 1, 1], [1, 0, 0, 1]) == 0.75, and accuracy([1], [1, 2]) raises ValueError, and evaluate([[0], [1], [10], [11]], [0, 0, 1, 1]) == 1.0, and its tests are in tests/test_score.py."

apple-code test tests

cat > check.py <<'EOF'
from scale import min_max
from model import train, predict
from score import accuracy, evaluate

assert min_max([2, 4, 6]).tolist() == [0.0, 0.5, 1.0] and min_max([3, 3]).tolist() == [0.0, 0.0] and min_max([0, 10]).tolist() == [0.0, 1.0]
f, l = [[0], [1], [10], [11]], [0, 0, 1, 1]
m = train(f, l)
assert type(predict(m, [[0], [11]])) is list and predict(m, [[0], [11]]) == [0, 1]
assert accuracy([1, 0, 1, 1], [1, 0, 0, 1]) == 0.75 and accuracy([], []) == 0.0
try:
    accuracy([1], [1, 2])
    raise SystemExit("accuracy with different lengths should raise ValueError")
except ValueError:
    pass
assert evaluate(f, l) == 1.0
print("PROMPT5 independent check: all passed")
EOF

python3 check.py
apple-code recap
