mkdir vision && cd vision

apple-code solve "Write three small Python files that use the Pillow package (import PIL). File 1 is gray.py with to_gray(image) -> Image that returns the image as mode L with the same size, for example from PIL import Image then to_gray(Image.new('RGB', (2, 2), (255, 255, 255))).mode == 'L', and from PIL import Image then to_gray(Image.new('RGB', (2, 2), (255, 255, 255))).getpixel((0, 0)) == 255, and from PIL import Image then to_gray(Image.new('RGB', (3, 1), (0, 0, 0))).size == (3, 1), and its tests are in tests/test_gray.py. File 2 is threshold.py with threshold(image, level: int) -> Image that takes a mode L image and returns a mode L image of the same size where each pixel with a value of level or more is 255 and every other pixel is 0, for example from PIL import Image then im = Image.new('L', (2, 1), 10) then im.putpixel((1, 0), 200) then list(threshold(im, 100).getdata()) == [0, 255], and from PIL import Image then im = Image.new('L', (2, 1), 10) then im.putpixel((1, 0), 200) then list(threshold(im, 201).getdata()) == [0, 0], and its tests are in tests/test_threshold.py. File 3 is stats.py with histogram_counts(image) -> list that returns 256 ints for a mode L image where item v is how many pixels have the value v, and percent_bright(image, level: int) -> float that uses threshold from threshold.py and returns the share of pixels at level or more as a number from 0.0 to 1.0, for example from PIL import Image then im = Image.new('L', (2, 1), 10) then im.putpixel((1, 0), 200) then histogram_counts(im)[10] == 1 then sum(histogram_counts(im)) == 2 then percent_bright(im, 100) == 0.5, and its tests are in tests/test_stats.py."

apple-code test tests

cat > check.py <<'EOF'
from PIL import Image
from gray import to_gray
from threshold import threshold
from stats import histogram_counts, percent_bright

g = to_gray(Image.new("RGB", (2, 2), (255, 255, 255)))
assert g.mode == "L" and g.getpixel((0, 0)) == 255 and to_gray(Image.new("RGB", (3, 1), (0, 0, 0))).size == (3, 1)
img = Image.new("L", (2, 1))
img.putpixel((0, 0), 10)
img.putpixel((1, 0), 200)
def px(i):
    return [i.getpixel((0, 0)), i.getpixel((1, 0))]
assert px(threshold(img, 100)) == [0, 255] and px(threshold(img, 200)) == [0, 255] and px(threshold(img, 201)) == [0, 0]
assert threshold(img, 100).mode == "L" and threshold(img, 100).size == (2, 1)
h = histogram_counts(img)
assert len(h) == 256 and h[10] == 1 and h[200] == 1 and sum(h) == 2
assert percent_bright(img, 100) == 0.5 and percent_bright(img, 0) == 1.0 and percent_bright(img, 255) == 0.0
print("PROMPT4 independent check: all passed")
EOF

python3 check.py
apple-code recap
