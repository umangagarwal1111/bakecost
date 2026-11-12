"""
Generate synthetic bill and recipe photos for testing OCR without a camera.

    python scripts/make_samples.py

Creates in samples/:
  bill_grocery.png       a clean, straight printed bill
  bill_photo_tilted.png  the same bill tilted 4 degrees, greyish and noisy,
                         like a quick phone photo (exercises the deskew step)
  recipe_cupcakes.png    a typed recipe card
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "samples"

# A clear monospaced font if the OS has one; Pillow's bundled font otherwise.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplied/Courier New.ttf",       # macOS
    "/Library/Fonts/Courier New.ttf",
    "C:/Windows/Fonts/cour.ttf",                            # Windows
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # Linux
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if bold:
            path = path.replace("Courier New.ttf", "Courier New Bold.ttf").replace("cour.ttf", "courbd.ttf")
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


BILL_ITEMS = [
    # name, qty column, rate, amount
    ("Maida 1kg", "2", "46.00", "92.00"),
    ("Amul Butter 500g", "1", "285.00", "285.00"),
    ("Sugar 1kg", "3", "49.00", "147.00"),
    ("Cocoa Powder 200g", "1", "198.00", "198.00"),
    ("Farm Eggs", "30", "7.50", "225.00"),
    ("Amul Fresh Cream 1L", "1", "235.00", "235.00"),
    ("Baking Powder 100g", "1", "52.00", "52.00"),
]


def make_bill() -> Image.Image:
    W, H = 1100, 1250
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    f, fb, fs = font(30), font(36, bold=True), font(26)
    y = 50
    d.text((W // 2, y), "SHREE GANESH PROVISION STORE", font=fb, fill=0, anchor="mt"); y += 55
    d.text((W // 2, y), "MG Road, Bengaluru  Ph: 080-2222 0000", font=fs, fill=0, anchor="mt"); y += 45
    d.text((60, y), "Bill No: 4521", font=fs, fill=0)
    d.text((W - 60, y), "Date: 08/11/2026", font=fs, fill=0, anchor="ra"); y += 50
    d.line((60, y, W - 60, y), fill=0, width=2); y += 20
    cols = (60, 640, 820, W - 60)
    d.text((cols[0], y), "Item", font=f, fill=0)
    d.text((cols[1], y), "Qty", font=f, fill=0, anchor="ra")
    d.text((cols[2], y), "Rate", font=f, fill=0, anchor="ra")
    d.text((cols[3], y), "Amount", font=f, fill=0, anchor="ra"); y += 50
    d.line((60, y, W - 60, y), fill=0, width=2); y += 25
    total = 0.0
    for name, qty, rate, amount in BILL_ITEMS:
        d.text((cols[0], y), name, font=f, fill=0)
        d.text((cols[1], y), qty, font=f, fill=0, anchor="ra")
        d.text((cols[2], y), rate, font=f, fill=0, anchor="ra")
        d.text((cols[3], y), amount, font=f, fill=0, anchor="ra")
        total += float(amount)
        y += 58
    d.line((60, y, W - 60, y), fill=0, width=2); y += 25
    d.text((cols[0], y), "TOTAL", font=fb, fill=0)
    d.text((cols[3], y), f"{total:.2f}", font=fb, fill=0, anchor="ra"); y += 70
    d.text((W // 2, y), "Thank you! Visit again", font=fs, fill=0, anchor="mt")
    return img


def photo_effect(img: Image.Image, angle: float, seed: int = 7) -> Image.Image:
    """Tilt, grey paper, slight blur and speckle noise - a cheap phone photo."""
    rnd = random.Random(seed)
    tilted = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    px = tilted.load()
    w, h = tilted.size
    for yy in range(h):                      # uneven lighting: darker towards the bottom
        shade = int(25 * yy / h)
        for xx in range(0, w):
            v = px[xx, yy]
            v = v - shade - (rnd.randint(0, 30) if rnd.random() < 0.04 else 0)
            px[xx, yy] = max(0, min(255, int(v * 0.85 + 20)))
    return tilted.filter(ImageFilter.GaussianBlur(0.8))


RECIPE_LINES = [
    ("Eggless Chocolate Cupcakes", "title"),
    ("Makes 12 pcs", "normal"),
    ("", "normal"),
    ("Ingredients:", "bold"),
    ("1 1/2 cups maida", "normal"),
    ("1/2 cup cocoa powder", "normal"),
    ("3/4 cup sugar", "normal"),
    ("1 cup curd", "normal"),
    ("1/2 cup vegetable oil", "normal"),
    ("1 tsp baking powder", "normal"),
    ("1/2 tsp baking soda", "normal"),
    ("1 tsp vanilla essence", "normal"),
    ("", "normal"),
    ("Method:", "bold"),
    ("1. Whisk curd and sugar until smooth.", "normal"),
    ("2. Bake at 180 C for 20 minutes.", "normal"),
]


def make_recipe() -> Image.Image:
    W, H = 1000, 1050
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    fonts = {"title": font(40, bold=True), "bold": font(32, bold=True), "normal": font(30)}
    y = 60
    for text, style in RECIPE_LINES:
        if text:
            d.text((70, y), text, font=fonts[style], fill=0)
        y += 70 if style == "title" else 52
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    bill = make_bill()
    bill.save(OUT / "bill_grocery.png")
    photo_effect(bill, angle=4).save(OUT / "bill_photo_tilted.png")
    make_recipe().save(OUT / "recipe_cupcakes.png")
    print(f"Wrote sample images to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
