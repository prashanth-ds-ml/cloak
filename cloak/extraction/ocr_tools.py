"""
ocr_tools.py — Tesseract OCR wrapper for scanned pages.
Called by parser_agent for pages where RouteMap[page_num] == "scanned".
See DECISIONS.md §D22.

Requires the Tesseract binary to be installed separately:
  Windows : winget install UB-Mannheim.TesseractOCR
             (or download from https://github.com/UB-Mannheim/tesseract/wiki)
  Linux   : sudo apt install tesseract-ocr
  macOS   : brew install tesseract

If the binary is missing, ocr_page() raises OCRError and the caller
falls back to raw PyMuPDF text blocks — no crash, just lower quality.
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageFilter

from cloak.config import OCR_LANG

_MIN_OCR_PX = 2000       # upscale long edge to this before OCR — better accuracy
_TESSERACT_CONFIG = "--oem 3 --psm 3"   # LSTM engine, fully automatic page segmentation

# Windows default install paths to check when Tesseract is not on PATH
_WIN_TESSERACT_PATHS = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]


class OCRError(Exception):
    """Raised when OCR cannot run — caller should fall back to raw text."""


# ── Tesseract path setup ──────────────────────────────────────────────────────

def _configure_tesseract() -> None:
    """
    Point pytesseract at the Tesseract binary if it is not on PATH.
    Tries the default Windows install locations automatically.
    Raises OCRError if no binary is found anywhere.
    """
    import pytesseract
    import shutil

    if shutil.which("tesseract"):
        return  # already on PATH

    for candidate in _WIN_TESSERACT_PATHS:
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            return

    raise OCRError(
        "Tesseract binary not found.\n"
        "  Windows : winget install UB-Mannheim.TesseractOCR\n"
        "  Linux   : sudo apt install tesseract-ocr\n"
        "  macOS   : brew install tesseract"
    )


# ── Image preprocessing ───────────────────────────────────────────────────────

def _preprocess(image: Image.Image) -> Image.Image:
    """
    Prepare a page image for Tesseract.
    1. Convert to grayscale — reduces noise and speeds up OCR.
    2. Upscale to ≥ _MIN_OCR_PX on the long edge — Tesseract accuracy
       degrades on images below ~200 DPI; upscaling compensates.
    3. Mild sharpen — improves character edge detection.
    """
    img = image.convert("L")

    w, h = img.size
    long_edge = max(w, h)
    if long_edge < _MIN_OCR_PX:
        scale = _MIN_OCR_PX / long_edge
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    img = img.filter(ImageFilter.SHARPEN)
    return img


# ── Text cleaning ─────────────────────────────────────────────────────────────

def clean_ocr_text(raw: str) -> str:
    """
    Remove common OCR noise from Tesseract output.
    Fixes hyphenated line breaks, strips lone page numbers,
    removes single-char noise lines, and collapses excess blank lines.
    """
    # Fix hyphenated line breaks: "treat-\nment" → "treatment"
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', raw)

    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r'\d{1,4}', stripped):   # lone page number
            continue
        if len(stripped) < 3 and stripped:        # single/double char noise
            continue
        cleaned.append(line)

    text = "\n".join(cleaned)
    text = re.sub(r'\n{3,}', '\n\n', text)        # collapse 3+ blank lines → 2
    return text.strip()


# ── Public API ────────────────────────────────────────────────────────────────

def ocr_page(image: Image.Image, lang: str = OCR_LANG) -> str:
    """
    Run Tesseract OCR on a page image. Returns cleaned text string.

    Raises OCRError if:
      - pytesseract is not installed
      - Tesseract binary is not found
      - Tesseract call fails for any reason

    Callers should catch OCRError and fall back to raw PyMuPDF text.
    """
    try:
        import pytesseract
    except ImportError:
        raise OCRError("pytesseract not installed — run: pip install pytesseract")

    _configure_tesseract()

    preprocessed = _preprocess(image)

    try:
        raw = pytesseract.image_to_string(
            preprocessed,
            lang=lang,
            config=_TESSERACT_CONFIG,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRError(f"Tesseract binary not found: {exc}") from exc
    except Exception as exc:
        raise OCRError(f"Tesseract call failed: {exc}") from exc

    return clean_ocr_text(raw)


def is_available() -> bool:
    """Return True if Tesseract is installed and callable. Safe to call anytime."""
    try:
        import pytesseract
        import shutil
        if shutil.which("tesseract"):
            return True
        for candidate in _WIN_TESSERACT_PATHS:
            if candidate.exists():
                return True
        return False
    except ImportError:
        return False
