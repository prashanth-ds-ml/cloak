"""
page_profiler.py — heuristic page classification. Zero model calls.

Classifies each PageData into one of five types and builds a RouteMap
that drives extraction strategy selection in Phase 3 (parser_agent).
See DECISIONS.md §D21.

Page types:
  text_rich   — digital text dominates; PyMuPDF + pdfplumber sufficient
  table_heavy — pdfplumber tables are the primary content
  image_heavy — large image(s) dominate; needs vision model
  scanned     — no extractable text + image present; needs OCR
  mixed       — text + moderate image regions; text path + vision for regions
"""
from __future__ import annotations

from dataclasses import dataclass

from cloak.config import IMAGE_AREA_THRESHOLD, SCANNED_TEXT_THRESHOLD
from cloak.extraction.pdf_tools import PageData

RouteMap = dict[int, str]   # {page_num: page_type}


@dataclass
class PageProfile:
    page_num: int
    text_length: int           # chars extracted by PyMuPDF
    image_area_ratio: float    # total image-block area / page area  (0.0 – 1.0)
    table_count: int           # pdfplumber tables found on this page
    page_type: str             # "text_rich" | "table_heavy" | "image_heavy" | "scanned" | "mixed"
    needs_ocr: bool
    needs_vision: bool         # Phase 3 extraction — judge always uses vision regardless


# ── Helpers ───────────────────────────────────────────────────────────────────

def _image_area_ratio(page: PageData) -> float:
    """Sum of image-block areas divided by total page area. Clamped to [0, 1]."""
    page_area = page.width * page.height
    if page_area <= 0:
        return 0.0
    total = sum(
        max(0.0, (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]))
        for b in page.blocks
        if b.block_type == "image"
    )
    return min(1.0, total / page_area)


def _classify(text_length: int, image_area_ratio: float, table_count: int) -> str:
    """
    Classify a page into one of five types.

    Priority order matters — earlier rules take precedence:
      1. scanned     — no extractable text + significant image → OCR needed
      2. image_heavy — image dominates AND text is sparse (truly visual page)
      3. table_heavy — pdfplumber found multiple tables
      4. mixed       — image present alongside real text → text + region vision
      5. text_rich   — default

    image_heavy requires sparse text (< 5× SCANNED_TEXT_THRESHOLD) so that PDFs
    with large background images but real digital text are classified as mixed, not
    image_heavy — those pages extract fine via PyMuPDF and only need region vision.
    """
    if text_length < SCANNED_TEXT_THRESHOLD and image_area_ratio > IMAGE_AREA_THRESHOLD:
        return "scanned"
    if image_area_ratio > 0.5 and text_length < SCANNED_TEXT_THRESHOLD * 5:
        return "image_heavy"
    if table_count >= 2:
        return "table_heavy"
    if image_area_ratio > 0.2 and text_length >= SCANNED_TEXT_THRESHOLD:
        return "mixed"
    return "text_rich"


# ── Public API ────────────────────────────────────────────────────────────────

def profile_page(page: PageData) -> PageProfile:
    """Classify a single page. No model calls — pure heuristics on PageData."""
    text_length      = len(page.text)
    img_ratio        = _image_area_ratio(page)
    table_count      = len(page.tables)
    page_type        = _classify(text_length, img_ratio, table_count)
    needs_ocr        = page_type == "scanned"
    needs_vision     = page_type in ("image_heavy", "mixed")

    return PageProfile(
        page_num=page.page_num,
        text_length=text_length,
        image_area_ratio=round(img_ratio, 3),
        table_count=table_count,
        page_type=page_type,
        needs_ocr=needs_ocr,
        needs_vision=needs_vision,
    )


def profile_all(pages: list[PageData]) -> list[PageProfile]:
    """Classify every page in the document."""
    return [profile_page(p) for p in pages]


def build_route_map(profiles: list[PageProfile]) -> RouteMap:
    """Return {page_num: page_type} for use by parser_agent Phase 3."""
    return {p.page_num: p.page_type for p in profiles}


def summarise(profiles: list[PageProfile]) -> dict[str, int]:
    """Count pages by type — used in startup display and logging."""
    counts: dict[str, int] = {}
    for p in profiles:
        counts[p.page_type] = counts.get(p.page_type, 0) + 1
    return counts


def update_vision_from_docling(
    profiles: list[PageProfile],
    element_map: dict[int, list],   # DoclingPageMap — typed as dict to avoid circular import
) -> None:
    """
    Refine needs_vision for each page based on the docling element map (D29).
    After this call, needs_vision=True only for pages that have actual PictureItems.
    Vision is no longer called for heading extraction or text layout on text_rich pages.
    """
    for p in profiles:
        elements = element_map.get(p.page_num, [])
        p.needs_vision = any(getattr(el, "label", None) == "picture" for el in elements)
