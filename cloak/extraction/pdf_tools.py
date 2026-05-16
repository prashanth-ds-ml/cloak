"""
pdf_tools.py — PDF → Python data structures.
No LLM calls. Produces PageData list consumed by vision_tools and parser_agent.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # pymupdf
import pdfplumber
from PIL import Image

from cloak.config import MIN_IMAGE_BYTES, PAGE_DPI

_LIGATURE_MAP = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl",
    "'": "'", "'": "'", "“": '"', "”": '"',
    "–": "-", "—": "-",
})


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class Block:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1
    block_type: str                           # "text" | "image"


@dataclass
class RegionCrop:
    image: Image.Image
    bbox: tuple[float, float, float, float]
    label: str   # "ecg" | "diagram" | "figure"
    page_num: int


@dataclass
class TableData:
    rows: list[list[Optional[str]]]
    page_num: int

    def to_markdown(self) -> str:
        if not self.rows:
            return ""
        lines = []
        for i, row in enumerate(self.rows):
            cells = [str(c).replace("\n", " ").strip() if c else "" for c in row]
            lines.append("| " + " | ".join(cells) + " |")
            if i == 0:
                lines.append("| " + " | ".join(["---"] * len(row)) + " |")
        return "\n".join(lines)


@dataclass
class PageData:
    page_num: int          # 0-indexed
    image: Image.Image     # full page render at PAGE_DPI
    width: float           # page width in PDF points
    height: float
    blocks: list[Block]    # spatially sorted (spanning → left col → right col)
    regions: list[RegionCrop]
    tables: list[TableData]

    @property
    def text(self) -> str:
        """All text block content joined for quick access."""
        return "\n".join(b.text for b in self.blocks if b.block_type == "text")


# ── Page rendering ────────────────────────────────────────────────────────────

def render_page(page: fitz.Page, dpi: int = PAGE_DPI) -> Image.Image:
    """Render a fitz page to a PIL RGB Image at the given DPI."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


# ── Block extraction ──────────────────────────────────────────────────────────

def extract_blocks(page: fitz.Page) -> list[Block]:
    """
    Extract all blocks from a page via get_text('dict').
    Text blocks: join span text across lines.
    Image blocks: captured as empty-text Block with block_type='image'.
    """
    raw = page.get_text("dict")
    blocks: list[Block] = []

    for b in raw.get("blocks", []):
        x0, y0, x1, y1 = b["bbox"]
        btype = b.get("type", 0)

        if btype == 0:  # text block
            lines = []
            for line in b.get("lines", []):
                span_text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if span_text:
                    lines.append(span_text)
            text = "\n".join(lines).strip().translate(_LIGATURE_MAP)
            if text:
                blocks.append(Block(text=text, bbox=(x0, y0, x1, y1), block_type="text"))

        elif btype == 1:  # image block — bbox only, no text
            blocks.append(Block(text="", bbox=(x0, y0, x1, y1), block_type="image"))

    return blocks


# ── Spatial sort ──────────────────────────────────────────────────────────────

def spatial_sort(blocks: list[Block], page_width: float) -> list[Block]:
    """
    Column-aware reading-order sort for two-column PDF layouts.

    Strategy:
      - Spanning blocks (width > 55% of page) are treated as full-width;
        they're interleaved into the left-column stream by their y0.
      - Left-column blocks sorted by y0, then right-column blocks appended.
    """
    mid = page_width / 2
    spanning: list[Block] = []
    left: list[Block] = []
    right: list[Block] = []

    for b in blocks:
        x0, y0, x1, y1 = b.bbox
        block_w = x1 - x0
        if block_w > page_width * 0.55:
            spanning.append(b)
        elif (x0 + x1) / 2 < mid:
            left.append(b)
        else:
            right.append(b)

    spanning.sort(key=lambda b: b.bbox[1])
    left.sort(key=lambda b: b.bbox[1])
    right.sort(key=lambda b: b.bbox[1])

    result: list[Block] = []
    si = 0
    for b in left:
        while si < len(spanning) and spanning[si].bbox[1] <= b.bbox[1]:
            result.append(spanning[si])
            si += 1
        result.append(b)
    while si < len(spanning):
        result.append(spanning[si])
        si += 1

    result.extend(right)
    return result


# ── Region detection ──────────────────────────────────────────────────────────

def _classify_region(w_px: int, h_px: int) -> str:
    """Heuristic label from rendered pixel dimensions."""
    aspect = w_px / h_px if h_px > 0 else 1
    if aspect > 2.5 and w_px > 300:
        return "ecg"
    if w_px > 200 and h_px > 150:
        return "diagram"
    return "figure"


def detect_regions(page: fitz.Page, page_num: int, blocks: list[Block]) -> list[RegionCrop]:
    """
    Clip the rendered page at each image-block bbox to produce PIL crops.
    Skips blocks smaller than ~0.5 inch (36 pts) in either dimension.
    """
    regions: list[RegionCrop] = []
    mat = fitz.Matrix(PAGE_DPI / 72, PAGE_DPI / 72)

    for b in blocks:
        if b.block_type != "image":
            continue

        x0, y0, x1, y1 = b.bbox
        w_pts, h_pts = x1 - x0, y1 - y0

        if w_pts < 36 or h_pts < 36:
            continue

        clip = fitz.Rect(x0, y0, x1, y1)
        pix = page.get_pixmap(matrix=mat, clip=clip, colorspace=fitz.csRGB)

        if pix.width * pix.height * 3 < MIN_IMAGE_BYTES:
            continue

        pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        label = _classify_region(pix.width, pix.height)

        regions.append(RegionCrop(
            image=pil,
            bbox=(x0, y0, x1, y1),
            label=label,
            page_num=page_num,
        ))

    return regions


# ── Table extraction ──────────────────────────────────────────────────────────

def _is_useful_table(rows: list[list]) -> bool:
    flat = [c for row in rows for c in row if c and str(c).strip()]
    if len(flat) <= 2:
        return False
    return len(" ".join(str(c) for c in flat)) >= 20


def _extract_page_tables(plumber_pdf: pdfplumber.PDF, page_num: int) -> list[TableData]:
    if page_num >= len(plumber_pdf.pages):
        return []
    results = []
    for tbl in plumber_pdf.pages[page_num].extract_tables():
        if tbl and _is_useful_table(tbl):
            results.append(TableData(rows=tbl, page_num=page_num))
    return results


def extract_tables(pdf_path: Path | str, page_num: int) -> list[TableData]:
    """Public single-page table extraction (for testing individual pages)."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        return _extract_page_tables(pdf, page_num)


# ── Public API ────────────────────────────────────────────────────────────────

def load_pages(pdf_path: Path | str) -> list[PageData]:
    """
    Load all pages from a PDF.
    Returns a PageData list with rendered image, sorted text blocks,
    region crops, and pdfplumber tables — ready for vision_tools.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    plumber_pdf = pdfplumber.open(str(pdf_path))
    pages: list[PageData] = []

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            rect = page.rect

            img = render_page(page)
            raw_blocks = extract_blocks(page)
            sorted_blocks = spatial_sort(raw_blocks, rect.width)
            regions = detect_regions(page, page_num, raw_blocks)
            tables = _extract_page_tables(plumber_pdf, page_num)

            pages.append(PageData(
                page_num=page_num,
                image=img,
                width=rect.width,
                height=rect.height,
                blocks=sorted_blocks,
                regions=regions,
                tables=tables,
            ))
    finally:
        plumber_pdf.close()
        doc.close()

    return pages
