"""
ocr_tools.py — OCR for scanned pages. Surya primary, Tesseract fallback (D30).

Fallback chain for scanned pages:
  surya OCR (GPU-accelerated, reading-order-aware) → D30
  tesseract (CPU fallback)                         → D22
  raw PyMuPDF text blocks (last resort)            → caller handles

Tesseract requires the binary:
  Windows : winget install UB-Mannheim.TesseractOCR
  Linux   : sudo apt install tesseract-ocr
  macOS   : brew install tesseract

Surya models are downloaded from HuggingFace on first use and cached locally.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from cloak.config import OCR_LANG

_MIN_OCR_PX       = 2000            # upscale long edge before Tesseract — better accuracy
_TESSERACT_CONFIG = "--oem 3 --psm 3"  # LSTM engine, fully automatic page segmentation

# Windows default install paths to check when Tesseract is not on PATH
_WIN_TESSERACT_PATHS = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]

# Lazy-loaded surya predictor singletons (avoid reloading models per page)
_surya_rec: Any = None
_surya_det: Any = None


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


# ── Surya OCR ─────────────────────────────────────────────────────────────────

def _load_surya() -> tuple[Any, Any]:
    """Lazy-load surya recognition + detection predictors. Cached after first load."""
    global _surya_rec, _surya_det
    if _surya_rec is None or _surya_det is None:
        try:
            from surya.recognition import RecognitionPredictor
            from surya.detection import DetectionPredictor
            _surya_rec = RecognitionPredictor()
            _surya_det = DetectionPredictor()
        except Exception as exc:
            raise OCRError(f"Surya model load failed: {exc}") from exc
    return _surya_rec, _surya_det


def _ocr_page_surya(image: Image.Image) -> str:
    """
    Run Surya OCR on a page image. Preserves reading order (sort_lines=True).
    Returns cleaned text. Raises OCRError on any failure.
    """
    try:
        rec, det = _load_surya()
        results  = rec([image], det_predictor=det, sort_lines=True)
        lines    = results[0].text_lines
        text     = "\n".join(ln.text for ln in lines if ln.text.strip())
        return clean_ocr_text(text)
    except OCRError:
        raise
    except Exception as exc:
        raise OCRError(f"Surya OCR failed: {exc}") from exc


def is_surya_available() -> bool:
    """Return True if surya is installed (GPU not required for the check itself)."""
    try:
        import surya.recognition  # noqa: F401
        return True
    except ImportError:
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def ocr_page(image: Image.Image, lang: str = OCR_LANG) -> str:
    """
    OCR a scanned page image. Tries Surya first (D30), falls back to Tesseract.

    Raises OCRError only when both engines fail.
    Callers should catch OCRError and fall back to raw PyMuPDF text.
    """
    # Try Surya primary (D30)
    if is_surya_available():
        try:
            return _ocr_page_surya(image)
        except OCRError:
            pass  # fall through to Tesseract

    # Tesseract fallback (D22)
    return _ocr_page_tesseract(image, lang)


def _ocr_page_tesseract(image: Image.Image, lang: str = OCR_LANG) -> str:
    """Run Tesseract OCR. Raises OCRError on any failure."""
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
    """Return True if at least one OCR engine (Surya or Tesseract) is ready."""
    return is_surya_available() or _is_tesseract_available()


def _is_tesseract_available() -> bool:
    """Return True if Tesseract is installed and callable."""
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
