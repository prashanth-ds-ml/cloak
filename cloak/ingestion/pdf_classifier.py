"""
Classify PDFs by content type to determine parsing strategy.

Type A: Pure vector — all content is text/vector, no meaningful images.
         → pdfplumber text + table extraction only.

Type B: Vector + clinical images (ECGs, X-rays, small diagrams, icons).
         → pdfplumber text + tables + gemma4 vision for images.

Type C: Raster-heavy — main workflow/flowchart rendered as a large bitmap.
         → pdfplumber for surrounding text + gemma4 vision for the main image.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import fitz  # pymupdf


IMAGE_MIN_SIZE_KB = 5      # anything smaller is decorative (borders, rules, tiny icons)
IMAGE_MIN_WIDTH   = 100    # pixels
IMAGE_MIN_HEIGHT  = 80     # pixels
LARGE_IMAGE_WIDTH = 400    # threshold to call it a "large raster block"


@dataclass
class EmbeddedImage:
    xref: int
    width: int
    height: int
    size_kb: int
    ext: str
    image_bytes: bytes
    is_large: bool = False   # True → likely a flowchart / full-page diagram


@dataclass
class PDFProfile:
    path: Path
    pages: int
    page_width: float
    page_height: float
    text_chars: int
    text_blocks: int
    vector_drawings: int
    tables_count: int
    meaningful_images: List[EmbeddedImage] = field(default_factory=list)
    pdf_type: str = "A"      # "A", "B", or "C"


def classify_pdf(pdf_path: Path) -> PDFProfile:
    doc = fitz.open(str(pdf_path))
    page = doc[0]

    text      = page.get_text("text")
    blocks    = page.get_text("blocks")
    drawings  = page.get_drawings()

    import pdfplumber
    with pdfplumber.open(str(pdf_path)) as plumb:
        tables_count = len(plumb.pages[0].extract_tables())

    meaningful: List[EmbeddedImage] = []
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        base = doc.extract_image(xref)
        w, h   = base["width"], base["height"]
        kb     = len(base["image"]) // 1024
        ext    = base["ext"]

        if kb < IMAGE_MIN_SIZE_KB or w < IMAGE_MIN_WIDTH or h < IMAGE_MIN_HEIGHT:
            continue

        is_large = (w >= LARGE_IMAGE_WIDTH)
        meaningful.append(EmbeddedImage(
            xref=xref, width=w, height=h, size_kb=kb,
            ext=ext, image_bytes=base["image"], is_large=is_large
        ))

    if not meaningful:
        pdf_type = "A"
    elif any(img.is_large for img in meaningful):
        pdf_type = "C"
    else:
        pdf_type = "B"

    profile = PDFProfile(
        path          = pdf_path,
        pages         = len(doc),
        page_width    = page.rect.width,
        page_height   = page.rect.height,
        text_chars    = len(text),
        text_blocks   = len([b for b in blocks if b[6] == 0]),
        vector_drawings = len(drawings),
        tables_count  = tables_count,
        meaningful_images = meaningful,
        pdf_type      = pdf_type,
    )
    doc.close()
    return profile
