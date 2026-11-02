mkdir rendering && cd rendering

apple-code solve "Write three small Python files that use the Pillow package (from PIL import Image, ImageDraw). File 1 is shapes.py with filled_rect(size: tuple, box: tuple, color: tuple) -> Image that returns a new RGB image of that size with a white background and a filled rectangle of that color over box (x0, y0, x1, y1), where x1 and y1 are included, for example with image = filled_rect((10, 10), (2, 2, 4, 4), (255, 0, 0)), image.getpixel((3, 3)) == (255, 0, 0), image.getpixel((4, 4)) == (255, 0, 0), image.getpixel((5, 5)) == (255, 255, 255), image.getpixel((0, 0)) == (255, 255, 255), and its tests are in tests/test_shapes.py. File 2 is checker.py with checkerboard(cells: int, cell_size: int) -> Image that returns a square RGB image of cells * cell_size pixels where the cell at row r and column c is black (0, 0, 0) when r + c is even and white (255, 255, 255) otherwise, for example with image = checkerboard(2, 3), image.size == (6, 6), image.getpixel((0, 0)) == (0, 0, 0), image.getpixel((3, 0)) == (255, 255, 255), image.getpixel((3, 3)) == (0, 0, 0), and its tests are in tests/test_checker.py. File 3 is chart.py with bar_chart(values: list, bar_width: int, height: int) -> Image that returns a white RGB image of len(values) * bar_width by height pixels with one blue (0, 0, 255) bar per value, where bar i fills the columns from i * bar_width to (i + 1) * bar_width - 1 and is value pixels tall from the bottom edge, for example with image = bar_chart([2, 4], 3, 5), image.size == (6, 5), image.getpixel((0, 4)) == (0, 0, 255), image.getpixel((0, 2)) == (255, 255, 255), image.getpixel((3, 1)) == (0, 0, 255), image.getpixel((5, 0)) == (255, 255, 255), and its tests are in tests/test_chart.py."

apple-code test tests

cat > check.py <<'EOF'
from shapes import filled_rect
from checker import checkerboard
from chart import bar_chart

R, W, B, BL = (255, 0, 0), (255, 255, 255), (0, 0, 0), (0, 0, 255)
i = filled_rect((10, 10), (2, 2, 4, 4), R)
assert i.size == (10, 10) and i.getpixel((3, 3)) == R and i.getpixel((4, 4)) == R and i.getpixel((5, 5)) == W and i.getpixel((0, 0)) == W and i.getpixel((2, 2)) == R
c = checkerboard(2, 3)
assert c.size == (6, 6) and c.getpixel((0, 0)) == B and c.getpixel((3, 0)) == W and c.getpixel((0, 3)) == W and c.getpixel((3, 3)) == B
b = bar_chart([2, 4], 3, 5)
assert b.size == (6, 5) and b.getpixel((0, 4)) == BL and b.getpixel((0, 3)) == BL and b.getpixel((0, 2)) == W and b.getpixel((3, 1)) == BL and b.getpixel((3, 0)) == W and b.getpixel((5, 0)) == W
print("PROMPT7 independent check: all passed")
EOF

python3 check.py
apple-code recap
