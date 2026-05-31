"""
math_ocr.py — math OCR layer (D35, D40).

Two backends:
  mathpix  — cloud API (Mathpix OCR). LlamaParse-quality LaTeX. Requires API keys.
             Sends image data to api.mathpix.com — opt-in only (D40).
  pix2tex  — local LatexOCR model (~100 MB). No network. Fallback when Mathpix not configured.

Two modes:
  equation(image)  — single FormulaItem crop → LaTeX string
  page(image)      — full rendered page → structured text with inline LaTeX
                     Used by exam_mode (D39) for fragmented-math pages.

Engine selection (config.MATH_OCR_ENGINE):
  "mathpix"  — use Mathpix when keys configured; silently fall back to pix2tex otherwise
  "pix2tex"  — always use local pix2tex; never call Mathpix
  "auto"     — prefer Mathpix when keys set, pix2tex otherwise (recommended default)
"""
from __future__ import annotations

import base64
import io
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as PILImage


# ── pix2tex (local) ────────────────────────────────────────────────────────────

_pix2tex_model: object = None
_pix2tex_available: bool | None = None


def is_pix2tex_available() -> bool:
    global _pix2tex_available
    if _pix2tex_available is None:
        try:
            import pix2tex  # noqa: F401
            _pix2tex_available = True
        except ImportError:
            _pix2tex_available = False
    return _pix2tex_available


def _load_pix2tex() -> object:
    global _pix2tex_model
    if _pix2tex_model is None:
        from pix2tex.cli import LatexOCR
        _pix2tex_model = LatexOCR()
    return _pix2tex_model


def _pix2tex_equation(image: "PILImage.Image") -> str:
    if not is_pix2tex_available():
        return ""
    try:
        model = _load_pix2tex()
        result = model(image)
        return result.strip() if result else ""
    except Exception:
        return ""


def unload_model() -> None:
    """Release pix2tex model from memory."""
    global _pix2tex_model
    _pix2tex_model = None


# ── Mathpix (cloud, opt-in) ────────────────────────────────────────────────────

def is_mathpix_available() -> bool:
    """True when both MATHPIX_APP_ID and MATHPIX_APP_KEY are configured."""
    from cloak.config import MATHPIX_APP_ID, MATHPIX_APP_KEY
    return bool(MATHPIX_APP_ID and MATHPIX_APP_KEY)


def _image_to_b64(image: "PILImage.Image") -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _mathpix_call(src_b64: str, formats: list[str], timeout: float = 30.0) -> dict:
    """POST to Mathpix /v3/text. Returns parsed JSON or {} on failure."""
    import httpx
    from cloak.config import MATHPIX_APP_ID, MATHPIX_APP_KEY, MATH_OCR_TIMEOUT
    try:
        resp = httpx.post(
            "https://api.mathpix.com/v3/text",
            headers={
                "app_id":       MATHPIX_APP_ID,
                "app_key":      MATHPIX_APP_KEY,
                "Content-Type": "application/json",
            },
            json={
                "src":     f"data:image/png;base64,{src_b64}",
                "formats": formats,
                "include_smiles": False,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _mathpix_equation(image: "PILImage.Image") -> str:
    """Single equation crop → LaTeX via Mathpix. Returns "" on failure."""
    data = _mathpix_call(
        _image_to_b64(image),
        formats=["latex_simplified"],
    )
    latex = data.get("latex_simplified", "").strip()
    # Strip surrounding \( \) or \[ \] that Mathpix sometimes wraps
    for wrap in (r"\[", r"\]", r"\(", r"\)"):
        latex = latex.replace(wrap, "").strip()
    return latex


def _mathpix_page(image: "PILImage.Image") -> str:
    """
    Full page → structured text with LaTeX via Mathpix (D39 exam_mode).

    Returns Mathpix 'text' format: plain text with inline \(...\) and display \[...\]
    LaTeX, which we normalise to $...$ and $$...$$ markdown convention.
    """
    data = _mathpix_call(
        _image_to_b64(image),
        formats=["text"],
        timeout=45.0,
    )
    text = data.get("text", "").strip()
    if not text:
        return ""
    # Normalise Mathpix LaTeX delimiters → standard markdown/KaTeX convention
    text = text.replace(r"\(", "$").replace(r"\)", "$")
    text = text.replace(r"\[", "$$\n").replace(r"\]", "\n$$")
    return text


# ── Public dispatch ────────────────────────────────────────────────────────────

def _active_engine() -> str:
    """Resolve the active engine: 'mathpix', 'pix2tex', or 'none'."""
    from cloak.config import MATH_OCR_ENGINE
    if MATH_OCR_ENGINE == "mathpix":
        return "mathpix" if is_mathpix_available() else "pix2tex"
    if MATH_OCR_ENGINE == "auto":
        return "mathpix" if is_mathpix_available() else ("pix2tex" if is_pix2tex_available() else "none")
    if MATH_OCR_ENGINE == "pix2tex":
        return "pix2tex" if is_pix2tex_available() else "none"
    return "none"


def ocr_equation(image: "PILImage.Image") -> str:
    """
    Extract LaTeX from a single equation crop.
    Dispatches to Mathpix or pix2tex based on config and availability.
    Returns "" on failure — caller falls back to docling text.
    """
    engine = _active_engine()
    if engine == "mathpix":
        result = _mathpix_equation(image)
        return result if result else _pix2tex_equation(image)   # fallback on empty
    if engine == "pix2tex":
        return _pix2tex_equation(image)
    return ""


def ocr_page(image: "PILImage.Image") -> str:
    """
    Extract full page content with LaTeX equations (D39 exam_mode).
    Mathpix only — pix2tex cannot do full-page extraction.
    Returns "" when Mathpix is not configured (caller falls back to vision model).
    """
    if not is_mathpix_available():
        return ""
    return _mathpix_page(image)


# ── Legacy alias (kept for any external callers) ───────────────────────────────

def pix2tex_equation(image: "PILImage.Image") -> str:
    """Legacy: calls ocr_equation() — uses active engine (Mathpix or pix2tex)."""
    return ocr_equation(image)
