"""Photo input + OCR steps shared by the bill and recipe scan pages (not a page)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st
from PIL import Image

from modules import ocr

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def use_fixed_width_text_boxes() -> None:
    """Show text boxes in a fixed-width font so bill columns line up like on
    paper. Streamlit has no option for this, so we add a little CSS."""
    st.markdown(
        """<style>
        [data-testid="stTextArea"] textarea {
            font-family: ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
            font-size: 0.9rem;
            line-height: 1.55;
            white-space: pre;          /* keep columns: scroll sideways instead of wrapping */
        }
        </style>""",
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False, max_entries=8)
def _cleaned(image_bytes: bytes) -> Image.Image:
    """Cache the clean-up so it isn't redone on every click."""
    return ocr.preprocess(ocr.load_image(image_bytes))


def pick_image(prefix: str, sample_glob: str) -> bytes | None:
    """Let the user upload, photograph, or choose a bundled sample image."""
    source = st.radio("Where is the photo?", ["📁 Upload a photo", "📸 Take a photo", "🧪 Use a sample"],
                      horizontal=True, key=f"{prefix}_source")
    if source.startswith("📁"):
        f = st.file_uploader("Choose an image", type=["png", "jpg", "jpeg", "webp"], key=f"{prefix}_upload")
        return f.getvalue() if f else None
    if source.startswith("📸"):
        st.caption("Tip: lay the paper flat, fill the frame, use good light and hold the phone straight above it.")
        f = st.camera_input("Take a photo", key=f"{prefix}_camera")
        return f.getvalue() if f else None
    samples = sorted(SAMPLES_DIR.glob(sample_glob))
    if not samples:
        st.warning("No sample images found. Run `python scripts/make_samples.py` to create them.")
        return None
    path = st.selectbox("Sample image", samples, format_func=lambda p: p.name, key=f"{prefix}_sample")
    return path.read_bytes()


HANDWRITTEN_BILL_MESSAGE = (
    "✋ **This bill looks handwritten.** Please upload a printed or digital bill "
    "(a shop receipt, a printed invoice, or a screenshot of an online order). "
    "If it *is* printed, retake the photo closer, flat and in good light."
)


def ocr_step(image_bytes: bytes | None, text_key: str, reject_handwriting: bool = False,
             tidy: Callable[[str], str] | None = None) -> None:
    """Show the photo, run OCR on request and put the text into st.session_state[text_key].

    reject_handwriting=True (bills): a handwritten photo is refused with an
    error and nothing is filled in, because a misread price would silently
    corrupt the ingredient library. Otherwise (recipes) it is only a warning.
    `tidy`, if given, reformats the text before showing it (bills: line up columns).

    Must run BEFORE the text area that uses `text_key` is drawn, because
    Streamlit only lets us set a widget's value before the widget exists.
    """
    ok, message = ocr.engine_status()
    if not ok:
        st.warning(f"**Automatic reading is unavailable.** {message} "
                   "You can still type or paste the text in the box below.", icon="✍️")
    if not image_bytes:
        return
    try:
        image = ocr.load_image(image_bytes)
    except Exception:
        st.error("That file doesn't look like a picture. Please choose a JPG or PNG photo.")
        return

    c1, c2 = st.columns(2)
    c1.image(image, caption="Your photo", width="stretch")
    with c2.expander("🔬 See the cleaned-up image (what the reader sees)"):
        st.caption("Grayscale → contrast → straightened → black & white.")
        st.image(_cleaned(image_bytes), width="stretch")
    clean = c2.toggle("Clean up the photo first (recommended)", value=True, key=f"{text_key}_clean")
    if c2.button("🔍 Read the text", type="primary", disabled=not ok, key=f"{text_key}_read"):
        with st.spinner("Reading the photo… (the first read can take a few seconds)"):
            result = ocr.extract_text(image, clean_up=clean)
        if not result.ok:
            st.error(result.error)
        elif result.likely_handwritten and reject_handwriting:
            st.error(HANDWRITTEN_BILL_MESSAGE)
        else:
            if result.likely_handwritten:
                st.warning("This looks handwritten, so some lines may be misread. "
                           "Check every quantity and name below.", icon="✍️")
            st.session_state[text_key] = tidy(result.text) if tidy else result.text
            st.session_state[f"{text_key}_from_ocr"] = True
