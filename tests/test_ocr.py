"""Image clean-up algorithms, engine fallback, and real OCR on the samples
(the last group is skipped automatically if Tesseract isn't installed)."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageOps

from modules import matching, ocr, parsing
from seed import INGREDIENTS

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TESSERACT_OK, _ = ocr.TesseractEngine().available()
needs_tesseract = pytest.mark.skipif(not TESSERACT_OK, reason="Tesseract not installed")


def test_otsu_splits_two_groups():
    arr = np.array([30] * 500 + [220] * 500, dtype=np.uint8)
    t = ocr.otsu_threshold(arr)
    assert 30 <= t < 220


def test_deskew_finds_known_tilt():
    gray = ImageOps.grayscale(Image.open(SAMPLES / "bill_grocery.png"))
    tilted = gray.rotate(5, resample=Image.BICUBIC, expand=True, fillcolor=255)
    assert ocr.estimate_skew_angle(tilted) == pytest.approx(-5, abs=0.5)


def test_preprocess_returns_black_and_white():
    out = ocr.preprocess(Image.open(SAMPLES / "bill_photo_tilted.png").convert("RGB"))
    assert set(np.unique(np.asarray(out))) <= {0, 255}


class _MissingEngine:
    name = "Missing"

    def available(self):
        return False, "not installed"

    def image_to_text(self, image):
        raise AssertionError("should never be called")


class _BrokenEngine:
    name = "Broken"

    def available(self):
        return True, "ok"

    def image_to_text(self, image):
        raise RuntimeError("boom")


@pytest.mark.parametrize("engine, needle", [(_MissingEngine(), "not installed"), (_BrokenEngine(), "boom")])
def test_extract_text_never_raises(engine, needle):
    old = ocr.get_engine()
    ocr.set_engine(engine)
    try:
        result = ocr.extract_text(Image.new("RGB", (50, 50), "white"))
    finally:
        ocr.set_engine(old)
    assert not result.ok and needle in result.error


@needs_tesseract
@pytest.mark.parametrize("filename", ["bill_grocery.png", "bill_photo_tilted.png"])
def test_bill_sample_end_to_end(filename):
    result = ocr.extract_text(ocr.load_image(SAMPLES / filename))
    assert result.ok, result.error
    items = parsing.parse_bill(result.text).items
    library = [matching.Candidate(i, n, [a.strip() for a in al.split(",")])
               for i, (n, _c, _u, _p, al) in enumerate(INGREDIENTS, start=1)]
    matched = {matching.best_match(it.name, library).ingredient_name for it in items}
    assert {"Maida", "Butter", "Sugar", "Eggs", "Fresh Cream"} <= matched
    maida = next(it for it in items if it.name == "Maida")
    assert (maida.quantity, maida.unit, maida.amount) == (2.0, "kg", 92.0)


@needs_tesseract
def test_recipe_sample_end_to_end():
    result = ocr.extract_text(ocr.load_image(SAMPLES / "recipe_cupcakes.png"))
    recipe = parsing.parse_recipe(result.text)
    assert recipe.title == "Eggless Chocolate Cupcakes"
    assert (recipe.yield_quantity, recipe.yield_unit) == (12, "pcs")
    assert len(recipe.lines) == 8


@pytest.mark.parametrize("confidences, expected", [
    ([95, 92, 96, 90, 94], False),            # crisp print
    ([70, 75, 80, 72, 78], True),             # low average -> handwriting
    ([96, 95, 30, 20, 94, 93, 40, 95], True), # many weak words
    ([40, 30], False),                        # too few words to judge
    ([], False),
])
def test_looks_handwritten(confidences, expected):
    assert ocr.looks_handwritten(confidences) is expected


@needs_tesseract
@pytest.mark.parametrize("filename, handwritten", [
    ("bill_grocery.png", False), ("bill_photo_tilted.png", False), ("bill_handwritten.png", True),
])
def test_handwritten_bill_detected(filename, handwritten):
    result = ocr.extract_text(ocr.load_image(SAMPLES / filename))
    assert result.ok and result.confidence is not None
    assert result.likely_handwritten is handwritten
