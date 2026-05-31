"""
doc_profiler.py — document-level profiling layer (D28, D29).

Two-step profiling before any model is loaded:
  1. DocProfile   — aggregates per-page profiles into doc-level signals
  2. ParsePlan    — derives adaptive strategy from DocProfile

Docling layout pass (D29):
  run_docling_pass(pdf_path) → DoclingPageMap | None
  Returns None if docling is not installed or conversion fails.
  Discards PageHeader / PageFooter items — they never pollute content.
"""
from __future__ import annotations

import gc
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cloak.profiling.page_profiler import PageProfile


# ── Docling element types ─────────────────────────────────────────────────────

@dataclass
class DoclingElement:
    """Parsed representation of a single docling layout element."""
    label: str                                    # "section_header", "text", "table", "picture", …
    text: str                                     # element text (empty for pictures)
    level: int                                    # heading level 1–3 (section_header only); 0 otherwise
    bbox_norm: tuple[float, float, float, float]  # (l, t, r, b) normalised [0,1] top-left origin
    table_md: str                                 # export_to_markdown() for TableItem; "" otherwise
    caption: str                                  # caption text for picture/table; "" otherwise


DoclingPageMap = dict[int, list[DoclingElement]]  # {page_num_0indexed: [elements]}


# ── DocProfile ────────────────────────────────────────────────────────────────

@dataclass
class DocProfile:
    """Document-level aggregate built from per-page profiles."""
    page_count:        int
    type_distribution: dict[str, float]   # {"text_rich": 0.82, "image_heavy": 0.12, …}
    vision_dependency: str                # "none" | "low" | "medium" | "high"
    complexity_score:  float              # 0.0–1.0; drives adaptive round budget
    size_tier:         str                # "small" | "medium" | "large" | "huge"
    formula_count:     int = 0            # D35: total FormulaItem elements across all pages


# ── ParsePlan ─────────────────────────────────────────────────────────────────

@dataclass
class ParsePlan:
    """Adaptive execution strategy derived from DocProfile. Overrides fixed constants."""
    model_tier:        str          # "none" | "fallback" | "primary" — vision model to load
    max_rounds:        int          # judge-patch round budget (overrides MAX_ROUNDS from config)
    judge_sample_rate: float        # fraction of pages to judge per round (1.0 = all)
    use_docling:       bool         # True when a valid DoclingPageMap is available
    use_math_ocr:      bool = False # D35: crop FormulaItems and run pix2tex
    math_ocr_engine:   str  = "none"# D35: "pix2tex" | "none"
    slide_mode:        bool = False # D38: per-slide VLM extraction for presentation decks
    exam_mode:         bool = False # D39: full-page math extraction for JEE/GATE/ESE papers
    poster_mode:       bool = False # D51: full-page VLM extraction for clinical flowcharts/posters


# ── Adaptive round table (D28) ────────────────────────────────────────────────

_BASE_ROUNDS: dict[str, int]   = {"small": 4, "medium": 3, "large": 2, "huge": 1}
_BASE_SAMPLE: dict[str, float] = {"small": 1.0, "medium": 0.6, "large": 0.3, "huge": 0.1}


# ── DocProfile builder ────────────────────────────────────────────────────────

def build_doc_profile(
    profiles: list[PageProfile],
    element_map: DoclingPageMap | None = None,
) -> DocProfile:
    """
    Build DocProfile from per-page profiles and optional docling element map.
    vision_dependency is refined when element_map is provided — only pages
    that actually have PictureItems count toward the vision fraction.
    """
    n = len(profiles)
    if n == 0:
        return DocProfile(0, {}, "none", 0.0, "small")

    type_counts: dict[str, int] = {}
    for p in profiles:
        type_counts[p.page_type] = type_counts.get(p.page_type, 0) + 1
    type_distribution = {k: round(v / n, 3) for k, v in type_counts.items()}

    # Vision dependency — refined by actual picture counts from docling
    if element_map is not None:
        pages_with_pictures = sum(
            1 for elems in element_map.values()
            if any(el.label == "picture" for el in elems)
        )
        vision_frac = pages_with_pictures / n
    else:
        vision_frac = sum(
            v for k, v in type_distribution.items()
            if k in ("image_heavy", "mixed", "scanned")
        )

    if vision_frac < 0.05:
        vision_dependency = "none"
    elif vision_frac < 0.20:
        vision_dependency = "low"
    elif vision_frac < 0.50:
        vision_dependency = "medium"
    else:
        vision_dependency = "high"

    # D35: count FormulaItem elements to detect math-heavy documents
    formula_count = 0
    if element_map is not None:
        for elems in element_map.values():
            formula_count += sum(1 for el in elems if el.label == "formula")

    table_frac   = type_distribution.get("table_heavy", 0.0)
    image_frac   = type_distribution.get("image_heavy", 0.0)
    mixed_frac   = type_distribution.get("mixed", 0.0)
    scanned_frac = type_distribution.get("scanned", 0.0)
    large_factor = min(1.0, n / 500)

    complexity_score = round(
        0.30 * table_frac +
        0.25 * image_frac +
        0.20 * mixed_frac +
        0.15 * scanned_frac +
        0.10 * large_factor,
        3,
    )

    if n < 50:
        size_tier = "small"
    elif n < 200:
        size_tier = "medium"
    elif n < 500:
        size_tier = "large"
    else:
        size_tier = "huge"

    return DocProfile(
        page_count        = n,
        type_distribution = type_distribution,
        vision_dependency = vision_dependency,
        complexity_score  = complexity_score,
        size_tier         = size_tier,
        formula_count     = formula_count,
    )


# ── ParsePlan builder ─────────────────────────────────────────────────────────

def build_parse_plan(
    profile: DocProfile,
    primary_viable: bool,
    use_docling: bool = False,
    exam_paper: bool = False,
    poster: bool = False,
) -> ParsePlan:
    """
    Derive adaptive ParsePlan from DocProfile.
    Complexity adjusts the base round budget by ±1.
    Model tier is capped at "fallback" when the primary model is not viable
    (total available memory — VRAM + RAM — is less than the primary model weight).
    exam_paper=True activates D39 exam_mode (JEE/GATE/ESE full-page math extraction).
    """
    base_rounds = _BASE_ROUNDS[profile.size_tier]
    if profile.complexity_score > 0.6:
        base_rounds += 1
    elif profile.complexity_score < 0.3:
        base_rounds -= 1
    max_rounds = max(1, base_rounds)

    judge_sample_rate = _BASE_SAMPLE[profile.size_tier]

    if profile.vision_dependency == "none":
        model_tier = "none"
    elif profile.vision_dependency == "low":
        model_tier = "fallback"
    else:
        # medium / high — prefer primary; cap at fallback when total memory can't cover it
        model_tier = "primary" if primary_viable else "fallback"

    from cloak.config import MATH_FORMULA_THRESHOLD, MATH_OCR_ENGINE
    from cloak.extraction.math_ocr import is_pix2tex_available
    use_math_ocr   = profile.formula_count >= MATH_FORMULA_THRESHOLD and is_pix2tex_available()
    math_ocr_engine = MATH_OCR_ENGINE if use_math_ocr else "none"

    # D38: slide deck detection — image_heavy + mixed > 70% AND multi-page AND text-sparse
    image_slide_frac = (
        profile.type_distribution.get("image_heavy", 0.0)
        + profile.type_distribution.get("mixed", 0.0)
    )
    slide_mode = (
        image_slide_frac >= 0.70
        and profile.page_count >= 5          # not a single poster
        and profile.size_tier in ("small", "medium")
    )

    # D39: exam_mode forces model_tier to at least "fallback" (vision required for math)
    if exam_paper and model_tier == "none":
        model_tier = "fallback"

    # D51: poster_mode forces primary vision — flowchart transcription needs the best VLM
    if poster and model_tier == "none":
        model_tier = "fallback"
    if poster and model_tier == "fallback" and primary_viable:
        model_tier = "primary"

    return ParsePlan(
        model_tier        = model_tier,
        max_rounds        = max_rounds,
        judge_sample_rate = judge_sample_rate,
        use_docling       = use_docling,
        use_math_ocr      = use_math_ocr,
        math_ocr_engine   = math_ocr_engine,
        slide_mode        = slide_mode,
        exam_mode         = exam_paper,
        poster_mode       = poster,
    )


# ── Docling layout pass (D29) ─────────────────────────────────────────────────

def run_docling_pass(pdf_path: Path) -> DoclingPageMap | None:
    """
    Run docling layout analysis on the PDF. Returns a DoclingPageMap keyed by
    0-indexed page number, or None if docling is not installed / conversion fails.

    Pipeline options:
      do_ocr=False  — scanned pages go through surya/tesseract separately (D30)
      device=cpu    — preserve VRAM for Ollama vision models loaded later

    On any failure a short warning is printed and None is returned — the pipeline
    falls back to the existing heuristic page profiler (D21).
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            AcceleratorDevice,
            AcceleratorOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError:
        return None   # docling not installed — silent fallback

    try:
        pipeline_opts = PdfPipelineOptions(
            do_ocr=False,
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts),
            },
        )
        result = converter.convert(str(pdf_path))
    except Exception as exc:
        import sys
        print(f"  [docling] conversion failed: {exc}", file=sys.stderr)
        return None

    doc = getattr(result, "document", None)
    if doc is None:
        return None

    element_map: DoclingPageMap = {}

    try:
        for item, _ in doc.iterate_items():
            _add_item(item, doc, element_map)
    except Exception as exc:
        import sys
        print(f"  [docling] item iteration failed: {exc}", file=sys.stderr)
        return None
    finally:
        gc.collect()

    # D36: visual reading order — sort each page top→bottom, left→right
    for elems in element_map.values():
        elems.sort(key=lambda e: (e.bbox_norm[1], e.bbox_norm[0]))

    return element_map if element_map else None


def _add_item(item: object, doc: object, element_map: DoclingPageMap) -> None:
    """Extract one docling item into element_map. Skips items without provenance."""
    prov_list = getattr(item, "prov", None)
    if not prov_list:
        return

    prov = prov_list[0]
    page_no  = getattr(prov, "page_no", None)
    if page_no is None:
        return
    page_num = page_no - 1   # 0-indexed

    label_raw = getattr(item, "label", None)
    label_val = (
        label_raw.value if hasattr(label_raw, "value") else str(label_raw or "text")
    )

    # PageHeader / PageFooter → discard entirely (D29)
    if label_val in ("page_header", "page_footer"):
        return

    text  = getattr(item, "text", "") or ""
    level = getattr(item, "level", 0) or 0

    # Normalise bbox to [0,1] top-left origin
    bbox_norm = (0.0, 0.0, 0.0, 0.0)
    raw_bbox  = getattr(prov, "bbox", None)
    if raw_bbox is not None:
        try:
            pages_dict = getattr(doc, "pages", {})
            page_item  = pages_dict.get(page_no)
            if page_item:
                size = getattr(page_item, "size", None)
                if size and getattr(size, "width", 0) and getattr(size, "height", 0):
                    bbox_tl = raw_bbox.to_top_left_origin(size.height)
                    bbox_norm = (
                        max(0.0, min(1.0, bbox_tl.l / size.width)),
                        max(0.0, min(1.0, bbox_tl.t / size.height)),
                        max(0.0, min(1.0, bbox_tl.r / size.width)),
                        max(0.0, min(1.0, bbox_tl.b / size.height)),
                    )
        except Exception:
            pass

    # Table markdown
    table_md = ""
    if label_val == "table":
        try:
            table_md = item.export_to_markdown()
        except Exception:
            table_md = ""

    el = DoclingElement(
        label     = label_val,
        text      = text,
        level     = level,
        bbox_norm = bbox_norm,
        table_md  = table_md,
        caption   = "",
    )

    if page_num not in element_map:
        element_map[page_num] = []
    element_map[page_num].append(el)
