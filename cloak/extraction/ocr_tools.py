"""
ocr_tools.py — OCR for scanned pages. GLM-OCR primary, Surya fallback, Tesseract last resort (D45).

Fallback chain for scanned pages:
  glm-ocr  (Ollama, 2.2 GB — #1 OmniDocBench, document-specialised)  → D45
  surya    (GPU-accelerated, reading-order-aware)                      → D30
  tesseract (CPU fallback)                                             → D22
  raw PyMuPDF text blocks (last resort)                               → caller handles

Tesseract requires the binary:
  Windows : winget install UB-Mannheim.TesseractOCR
  Linux   : sudo apt install tesseract-ocr
  macOS   : brew install tesseract

Surya models are downloaded from HuggingFace on first use and cached locally.
GLM-OCR runs via Ollama — requires `ollama pull glm-ocr`.
"""
from __future__ import annotations

import io
import re
import queue
import threading
from pathlib import Path
from typing import Any

import ollama
from PIL import Image, ImageFilter

from cloak.config import GLM_OCR_MODEL, GLM_OCR_TIMEOUT, MODEL_KEEP_ALIVE, OCR_LANG

_MIN_OCR_PX       = 2000            # upscale long edge before Tesseract — better accuracy
_TESSERACT_CONFIG = "--oem 3 --psm 3"  # LSTM engine, fully automatic page segmentation

# Windows default install paths to check when Tesseract is not on PATH
_WIN_TESSERACT_PATHS = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]

# Lazy-loaded surya predictor singletons (avoid reloading models per page)
_surya_foundation: Any = None
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
    """Lazy-load surya foundation, recognition, and detection predictors. Cached after first load."""
    global _surya_foundation, _surya_rec, _surya_det
    if _surya_rec is None or _surya_det is None:
        try:
            from surya.foundation import FoundationPredictor
            from surya.recognition import RecognitionPredictor
            from surya.detection import DetectionPredictor
            _surya_foundation = FoundationPredictor()
            _surya_det = DetectionPredictor()
            _surya_rec = RecognitionPredictor(_surya_foundation)
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


# ── GLM-OCR (Ollama, primary) ─────────────────────────────────────────────────

_GLM_OCR_PROMPT = """\
Extract all content from this document page into clean markdown.
- Preserve reading order top to bottom, left to right.
- Reproduce tables in markdown table format with | header | ... | separator rows.
- Reproduce mathematical formulas as LaTeX: inline $formula$ or block $$formula$$.
- Preserve all text exactly — do NOT summarise or paraphrase.
- Output only the extracted markdown content, no preamble or commentary."""


def _ocr_page_glm(image: Image.Image) -> str:
    """
    Run GLM-OCR (Ollama) on a page image. #1 on OmniDocBench V1.5 (D45).
    Handles text, tables, formulas, and complex layouts in one pass.
    Raises OCRError on failure — caller falls back to surya.
    """
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            resp = ollama.chat(
                model=GLM_OCR_MODEL,
                messages=[{
                    "role":    "user",
                    "content": _GLM_OCR_PROMPT,
                    "images":  [img_bytes],
                }],
                options={"num_ctx": 4096},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", exc))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=GLM_OCR_TIMEOUT)
    except queue.Empty:
        raise OCRError(f"GLM-OCR timed out after {GLM_OCR_TIMEOUT}s")

    if kind == "err":
        raise OCRError(f"GLM-OCR failed: {value}")
    if not value:
        raise OCRError("GLM-OCR returned empty response")
    return value


def is_glm_ocr_available() -> bool:
    """True when glm-ocr is present in Ollama's local model list."""
    try:
        models = ollama.list()
        names = [m.model for m in models.models]
        return any("glm-ocr" in n for n in names)
    except Exception:
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def ocr_page(image: Image.Image, lang: str = OCR_LANG) -> str:
    """
    OCR a scanned page image. Fallback chain: GLM-OCR → surya → tesseract (D45).

    Raises OCRError only when all engines fail.
    Callers should catch OCRError and fall back to raw PyMuPDF text.
    """
    # GLM-OCR primary (D45) — document-specialised, handles tables + formulas
    if is_glm_ocr_available():
        try:
            return _ocr_page_glm(image)
        except OCRError:
            pass  # fall through to surya

    # Surya fallback (D30) — reading-order-aware
    if is_surya_available():
        try:
            return _ocr_page_surya(image)
        except OCRError:
            pass  # fall through to tesseract

    # Tesseract last resort (D22)
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


def extract_table_glm(image: Image.Image) -> str:
    """
    Extract a table image crop into markdown table format via GLM-OCR (D45).
    Returns "" when GLM-OCR is unavailable or fails — caller keeps docling/pdfplumber result.
    """
    if not is_glm_ocr_available():
        return ""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            resp = ollama.chat(
                model=GLM_OCR_MODEL,
                messages=[{
                    "role": "user",
                    "content": (
                        "Extract this table into markdown table format. "
                        "Use | column | headers | with a | --- | --- | separator row. "
                        "Preserve all cell values exactly including numbers and units. "
                        "For merged/spanning cells repeat the value in each affected cell. "
                        "Output only the markdown table, no preamble."
                    ),
                    "images": [img_bytes],
                }],
                options={"num_ctx": 2048},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", exc))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=GLM_OCR_TIMEOUT)
    except queue.Empty:
        return ""

    if kind == "err" or not value:
        return ""
    return value


def is_available() -> bool:
    """Return True if at least one OCR engine (GLM-OCR, Surya, or Tesseract) is ready."""
    return is_glm_ocr_available() or is_surya_available() or _is_tesseract_available()


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
