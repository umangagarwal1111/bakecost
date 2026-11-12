"""
Everything OCR lives here, behind a tiny interface, so the engine can be
swapped (e.g. for EasyOCR or a cloud API) without touching any page.

    extract_text(image) -> OCRResult(text, ok, engine, error)
    engine_status()     -> (available: bool, message: str)
    set_engine(engine)  -> plug in anything with .name, .available(), .image_to_text()

extract_text() never raises: if Tesseract is missing or crashes, it returns
ok=False with a friendly message and the app falls back to manual entry.

Image clean-up pipeline (preprocess)
------------------------------------
Phone photos of bills are grey, uneven and tilted; Tesseract reads best from
crisp black text on white, in straight lines. So we:

1. Fix phone rotation (EXIF) and convert to grayscale.          O(P) for P pixels
2. Resize so text is a sensible size (Tesseract likes ~30 px tall letters).
3. Stretch the contrast (autocontrast).                         O(P)
4. Deskew with a projection profile: try rotating by a range of angles; the
   angle where the row sums of ink are most "spiky" (text rows line up with
   pixel rows, gaps between lines are empty) is the right one. Done coarse
   (1 degree steps) then fine (0.1 degree steps) on a small copy.
   O(A * p) for A angles (~40) on a p-pixel thumbnail.
5. Threshold to pure black/white using Otsu's method: pick the grey level that
   best separates the histogram into two groups (ink vs paper) by maximising
   between-class variance. O(P + 256).
"""

from __future__ import annotations

import io
import os
import shutil
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from PIL import Image, ImageOps


@dataclass
class OCRResult:
    text: str
    ok: bool
    engine: str
    error: str | None = None


class OCREngine(Protocol):
    """What any OCR engine must provide to plug into BakeCost."""

    name: str

    def available(self) -> tuple[bool, str]: ...

    def image_to_text(self, image: Image.Image) -> str: ...


# ---------------------------------------------------------------------------
# Default engine: Tesseract via pytesseract
# ---------------------------------------------------------------------------

_WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


class TesseractEngine:
    name = "Tesseract"

    def __init__(self, lang: str = "eng", psm: int = 6):
        # psm 6 = "assume a single uniform block of text": right for bills and
        # typed recipes, and keeps each printed line on one output line.
        self.lang, self.psm = lang, psm
        self._configured = False

    def _configure(self):
        """Point pytesseract at the tesseract program if it isn't on PATH
        (the usual case on Windows after running the installer)."""
        if self._configured:
            return
        import pytesseract  # imported lazily so the app starts even if it's missing

        custom = os.environ.get("TESSERACT_CMD")
        if custom:
            pytesseract.pytesseract.tesseract_cmd = custom
        elif not shutil.which("tesseract") and os.path.exists(_WINDOWS_DEFAULT):
            pytesseract.pytesseract.tesseract_cmd = _WINDOWS_DEFAULT
        self._configured = True

    def available(self) -> tuple[bool, str]:
        try:
            import pytesseract
        except ImportError:
            return False, "The 'pytesseract' Python package isn't installed (pip install pytesseract)."
        self._configure()
        try:
            version = pytesseract.get_tesseract_version()
            return True, f"Tesseract {version} is ready."
        except Exception:
            return False, ("Tesseract isn't installed or can't be found. Install it (see README), "
                           "or set the TESSERACT_CMD environment variable to tesseract's path.")

    def image_to_text(self, image: Image.Image) -> str:
        import pytesseract

        self._configure()
        return pytesseract.image_to_string(image, lang=self.lang, config=f"--psm {self.psm}")


_engine: OCREngine = TesseractEngine()


def set_engine(engine: OCREngine) -> None:
    """Swap the OCR engine used by the whole app."""
    global _engine
    _engine = engine


def get_engine() -> OCREngine:
    return _engine


def engine_status() -> tuple[bool, str]:
    try:
        return _engine.available()
    except Exception as exc:  # a broken custom engine must not crash the page
        return False, f"OCR engine '{getattr(_engine, 'name', '?')}' failed its check: {exc}"


# ---------------------------------------------------------------------------
# Image loading and clean-up
# ---------------------------------------------------------------------------


def load_image(source) -> Image.Image:
    """Open bytes / a file path / an uploaded file, applying phone EXIF rotation."""
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    img = Image.open(source)
    return ImageOps.exif_transpose(img).convert("RGB")


def otsu_threshold(gray: np.ndarray) -> int:
    """Otsu's method: the grey level that best splits pixels into ink and paper.

    For each candidate threshold t we split the histogram into a dark class
    and a light class and compute w0 * w1 * (mean0 - mean1)^2 (between-class
    variance). The best t maximises it. Using cumulative sums makes all 256
    candidates cost O(256) after one O(P) histogram.
    """
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 128
    levels = np.arange(256)
    w0 = np.cumsum(hist)                       # pixels at or below t
    w1 = total - w0
    sum0 = np.cumsum(hist * levels)
    mean0 = np.divide(sum0, w0, out=np.zeros(256), where=w0 > 0)
    mean1 = np.divide(sum0[-1] - sum0, w1, out=np.zeros(256), where=w1 > 0)
    between = w0 * w1 * (mean0 - mean1) ** 2
    return int(np.argmax(between))


def _profile_score(ink: Image.Image, angle: float) -> float:
    """How 'line-like' the ink is after rotating by `angle` (higher = straighter)."""
    rotated = np.asarray(ink.rotate(angle, resample=Image.NEAREST, fillcolor=0), dtype=np.float64)
    rows = rotated.sum(axis=1)
    return float(np.sum(np.diff(rows) ** 2))


def estimate_skew_angle(gray: Image.Image, max_angle: float = 10.0) -> float:
    """Angle (degrees, counter-clockwise) that straightens the text lines."""
    small = gray.copy()
    small.thumbnail((600, 600))
    arr = np.asarray(small)
    ink = Image.fromarray(((arr < otsu_threshold(arr)) * 255).astype(np.uint8))  # text = white

    coarse = np.arange(-max_angle, max_angle + 0.001, 1.0)
    best = max(coarse, key=lambda a: _profile_score(ink, a))
    fine = np.arange(best - 1.0, best + 1.001, 0.1)
    best = max(fine, key=lambda a: _profile_score(ink, a))
    return round(float(best), 1)


def preprocess(image: Image.Image) -> Image.Image:
    """Grayscale -> resize -> contrast -> deskew -> black/white (see module doc)."""
    gray = ImageOps.grayscale(image)

    # Phone photos are huge (slow) and screenshots tiny (unreadable): aim for ~1600 px wide.
    w, h = gray.size
    scale = 1600 / w if (w > 2400 or w < 1000) else 1.0
    if scale != 1.0:
        gray = gray.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    gray = ImageOps.autocontrast(gray, cutoff=1)

    angle = estimate_skew_angle(gray)
    if abs(angle) >= 0.3:
        gray = gray.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)

    arr = np.asarray(gray)
    t = otsu_threshold(arr)
    return Image.fromarray(((arr > t) * 255).astype(np.uint8))


def extract_text(image: Image.Image, clean_up: bool = True) -> OCRResult:
    """Run OCR on an image. Never raises - check `.ok` and show `.error`."""
    name = getattr(_engine, "name", "OCR")
    ok, message = engine_status()
    if not ok:
        return OCRResult("", False, name, message)
    try:
        prepared = preprocess(image) if clean_up else image
        text = _engine.image_to_text(prepared)
    except Exception as exc:
        return OCRResult("", False, name, f"Couldn't read the image ({exc}). You can type the details instead.")
    if not text.strip():
        return OCRResult("", False, name, "No text found. Try a sharper, brighter photo taken straight on.")
    return OCRResult(text, True, name)
