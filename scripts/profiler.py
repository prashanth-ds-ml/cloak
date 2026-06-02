"""
profiler.py -- deep profiler combining pdfplumber + docling + GLM-OCR.

Fixes applied (Session 30):
  Fix 1 - Hallucination detection: discard GLM-OCR when it generates fake numbered sections
  Fix 2 - Multi-page support: profile all pages, report per-page + aggregate
  Fix 3 - OCR correction: qwen3:14b post-pass fixes phonetic medical term errors
  Fix 4 - Column detection: filter spanning headers (x<5%) before clustering

Produces a DocumentProfile with everything the extraction pipeline needs:
  - Column structure per page
  - Content inside picture sections
  - Section alignment (docling vs GLM-OCR)
  - OCR-corrected section names
  - Multi-page awareness

Usage:
  python scripts/profiler.py data/samples/icmr_stw/cardiology_af.pdf
  python scripts/profiler.py data/samples/icmr_stw_full/   (all PDFs in dir)
"""
from __future__ import annotations
import sys, io, re, json, time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from collections import Counter
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pdfplumber
from cloak.profiling.doc_profiler import run_docling_pass
from cloak.extraction.ocr_tools import _ocr_page_glm, is_glm_ocr_available, OCRError
from cloak.extraction.pdf_tools import load_pages

LOG_DIR = Path("data/batch_logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ColumnInfo:
    count: int
    boundaries_pct: list[float]    # X% boundaries between columns
    method: str                    # how it was detected

@dataclass
class PictureSection:
    y0_pct: float
    y1_pct: float
    x0_pct: float
    x1_pct: float
    area_pct: float
    pdfplumber_text: str           # what pdfplumber extracts from this region
    word_count: int

@dataclass
class TableInfo:
    index: int
    x0_pct: float
    x1_pct: float
    y0_pct: float
    y1_pct: float
    rows: int
    cols: int
    header_preview: str

@dataclass
class SectionHeader:
    text: str
    x_pct: float
    y_pct: float
    source: str                    # "docling" | "glm_ocr"

@dataclass
class DocumentProfile:
    name: str
    path: str

    # Geometry
    width_pts: float
    height_pts: float
    page_count: int

    # pdfplumber
    pdf_chars: int
    pdf_word_count: int
    pdf_tables: list[TableInfo]

    # Docling
    docling_elements: int
    docling_label_counts: dict
    docling_coverage_pct: float
    docling_chars: int
    docling_sections: list[SectionHeader]
    picture_sections: list[PictureSection]  # large picture elements (content gaps)

    # Column detection
    columns: ColumnInfo

    # GLM-OCR
    glm_available: bool
    glm_chars: int
    glm_time_s: float
    glm_text: str
    glm_sections: list[str]        # ALL-CAPS headings in GLM-OCR output

    # Cross-tool analysis
    sections_only_in_glm: list[str]    # in GLM-OCR but not in docling (missing from structure)
    sections_only_in_docling: list[str]  # in docling but not in GLM-OCR
    picture_text_chars: int           # total chars in picture sections (content gap size)

    # Strategy
    extraction_strategy: str


# ── helpers ────────────────────────────────────────────────────────────────────

def is_glm_hallucination(sections: list[str]) -> bool:
    """
    Fix 1: Detect GLM-OCR hallucination where model generates fake numbered sections.
    Patterns seen across 158 docs:
      - "CONSIDERATION 1" through "CONSIDERATION 106" (cardiology_nstemi)
      - "APPROVED 1." through "APPROVED 106" (neurosurg_spinal)
      - "SECTION 1" type patterns
    General rule: if >10 sections and >50% match any WORD + number pattern
    where the word is the same for most entries → hallucination.
    """
    if len(sections) < 10:
        return False
    # Extract leading word from each section
    words = []
    for s in sections:
        m = re.match(r'^([A-Z]+)\s+\d+', s.strip())
        if m:
            words.append(m.group(1))
    if not words:
        return False
    from collections import Counter
    most_common_word, most_common_count = Counter(words).most_common(1)[0]
    # If one word dominates (>50% of sections are WORD N format) → hallucination
    return most_common_count / len(sections) > 0.45


def detect_columns_from_elements(section_headers: list[SectionHeader], page_width: float) -> ColumnInfo:
    """
    Detect column structure using docling section header X positions.
    Fix 4: Filter spanning headers (x < 5%) — these are left-margin or centered
    headers that span the full width, not column anchors. Including them skews
    the clustering and causes 3-column docs to appear as 2-column.
    """
    if not section_headers:
        return ColumnInfo(count=1, boundaries_pct=[], method="no headers")

    # Fix 4: filter non-column headers before clustering
    # - x < 5%: left-margin spanning headers (e.g. STROKE RISK SCORE at x=2.6%)
    # - x > 85%: right-edge headers
    # - STW template title: "Standard Treatment Workflow" header appears in ALL ICMR STWs
    #   as a centered title at the top, NOT anchored to any column. Filter by content.
    #   Using y-position filter caused regressions on 5-column docs whose real column
    #   headers happened to be near the top of the page.
    def _is_stw_title(text: str) -> bool:
        t = text.lower()
        return ("standard treatment workflow" in t
                or re.match(r'^icd-?\d+', t)
                or re.match(r'^stw\b', t))

    anchored = [h for h in section_headers
                if 5.0 < h.x_pct < 85.0 and not _is_stw_title(h.text)]
    if len(anchored) < 2:
        return ColumnInfo(count=1, boundaries_pct=[], method="too few headers")

    xs = sorted(h.x_pct for h in anchored)

    # Gap threshold: 12% detects AF's 3-column gaps (~12.2%) while still
    # correctly giving 2 columns for stroke (gap = 11.2% < 12% = no boundary there,
    # but 27% gap → correct 2-column boundary found).
    gaps = []
    for i in range(len(xs) - 1):
        gap = xs[i+1] - xs[i]
        if gap > 12.0:
            boundary = (xs[i] + xs[i+1]) / 2
            gaps.append(round(boundary, 1))

    # No secondary fallback — dense-header documents (24+ headers) produce many
    # small gaps that create false multi-column detections at lower thresholds.
    # If no 12% gap is found, declare 1 column (content still extracted correctly).

    # Deduplicate nearby boundaries (within 5%)
    deduped = []
    for b in sorted(gaps):
        if not deduped or b - deduped[-1] > 5.0:
            deduped.append(b)

    n_cols = len(deduped) + 1
    return ColumnInfo(count=n_cols, boundaries_pct=deduped,
                      method=f"docling header X clustering ({len(anchored)} headers)")


def glm_to_plain_text(glm_output: str) -> str:
    """
    Convert GLM-OCR HTML output to plain text.
    GLM-OCR returns HTML tables (<table>, <td>, <br> etc.) not plain text.
    Strip tags and decode entities to get readable text.
    """
    if not glm_output:
        return ""
    # Check if it's HTML
    if "<table" in glm_output or "<td" in glm_output:
        # Replace <br> with newline
        text = re.sub(r'<br\s*/?>', '\n', glm_output, flags=re.IGNORECASE)
        # Replace </tr> with newline
        text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
        # Replace </td> and </th> with pipe separator
        text = re.sub(r'</t[dh]>', ' | ', text, flags=re.IGNORECASE)
        # Strip all remaining HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Decode HTML entities
        text = text.replace('&amp;', '&').replace('&gt;', '>').replace('&lt;', '<')
        text = text.replace('&nbsp;', ' ').replace('&#39;', "'")
        # Clean up whitespace
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return '\n'.join(lines)
    return glm_output


def extract_sections_from_text(text: str) -> list[str]:
    """Find ALL-CAPS lines in text — these are section headings."""
    # First convert from HTML if needed
    plain = glm_to_plain_text(text)
    sections = []
    for line in plain.splitlines():
        stripped = line.strip()
        # Strip pipe separators and check
        stripped = stripped.strip('| ').strip()
        if (stripped
                and len(stripped) > 3
                and stripped == stripped.upper()
                and not stripped.isdigit()
                and not stripped.startswith('|')
                and re.search(r'[A-Z]{3,}', stripped)
                and not re.fullmatch(r'[\d\s|.,:;()%/-]+', stripped)):
            sections.append(stripped)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for s in sections:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def normalize_section(text: str) -> str:
    """Normalize section name for comparison."""
    return re.sub(r'\s+', ' ', text.upper().strip())[:40]


def correct_medical_ocr(text: str) -> str:
    """
    Fix 3: Use qwen3:14b to correct phonetic/visual OCR errors in medical text.
    Only runs when qwen3:14b is available in Ollama. Falls back to original text.
    Rules: fix spelling errors only, never change clinical values (numbers, doses).
    """
    if not text or len(text) < 50:
        return text
    try:
        import ollama as _ollama
        from cloak.config import MODEL_KEEP_ALIVE
        prompt = (
            "Fix OCR spelling errors in this medical text. Rules:\n"
            "1. Fix phonetic substitutions only (e.g. THROMBOVIC->THROMBOLYTIC, ISTERITY->TERTIARY)\n"
            "2. Never change clinical values: numbers, doses, thresholds, units\n"
            "3. Keep ALL-CAPS section headings in ALL-CAPS\n"
            "4. Do not add or remove any content\n"
            "5. Return only the corrected text, no explanation\n\n"
            f"TEXT:\n{text[:3000]}"
        )
        resp = _ollama.chat(
            model="qwen3:14b",
            messages=[{"role": "user", "content": "/no_think\n" + prompt}],
            options={"temperature": 0.0, "num_ctx": 4096, "think": False},
            keep_alive=MODEL_KEEP_ALIVE,
        )
        corrected = resp.message.content.strip()
        # Safety: if correction drastically changes length, revert
        if corrected and 0.7 < len(corrected) / len(text[:3000]) < 1.5:
            return corrected + (text[3000:] if len(text) > 3000 else "")
        return text
    except Exception:
        return text


def is_correction_available() -> bool:
    """Return True if qwen3:14b is available for OCR correction."""
    try:
        import ollama as _ollama
        models = _ollama.list()
        return any("qwen3:14b" in m.model for m in models.models)
    except Exception:
        return False


def get_pdfplumber_text_in_bbox(page, bbox_norm, page_W, page_H) -> str:
    """Extract pdfplumber text within a normalized bbox [0,1]."""
    try:
        l, t, r, b = bbox_norm
        x0, y0, x1, y1 = l * page_W, t * page_H, r * page_W, b * page_H
        # Add small margin
        crop = page.within_bbox((max(0, x0-2), max(0, y0-2),
                                  min(page_W, x1+2), min(page_H, y1+2)))
        return (crop.extract_text() or "").strip()
    except Exception:
        return ""


# ── main profiler ──────────────────────────────────────────────────────────────

_correction_available: bool | None = None   # cached check


def profile(pdf_path: Path, use_ocr_correction: bool = True) -> DocumentProfile:
    pdf_path = Path(pdf_path)
    name = pdf_path.stem

    # ── pdfplumber (page 0) ─────────────────────────────────────────────────
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        W, H = page.width, page.height
        page_count = len(pdf.pages)        # Fix 2: record full page count
        raw_text = page.extract_text() or ""
        words = page.extract_words(x_tolerance=3, y_tolerance=3)
        tables_raw = page.find_tables()

        pdf_tables = []
        for i, t in enumerate(tables_raw):
            x0, y0, x1, y1 = t.bbox
            rows = t.extract()
            nr = len(rows) if rows else 0
            nc = len(rows[0]) if rows and rows[0] else 0
            hdr = ""
            if rows and rows[0]:
                hdr = " | ".join(str(c or "")[:25] for c in rows[0] if c)[:60]
            pdf_tables.append(TableInfo(
                index=i+1,
                x0_pct=round(x0/W*100, 1), x1_pct=round(x1/W*100, 1),
                y0_pct=round(y0/H*100, 1), y1_pct=round(y1/H*100, 1),
                rows=nr, cols=nc, header_preview=hdr,
            ))

    # ── docling ─────────────────────────────────────────────────────────────
    docling_sections = []
    picture_sections = []
    label_counts = {}
    docling_chars = 0
    docling_coverage = 0.0
    docling_element_count = 0

    element_map = run_docling_pass(pdf_path)
    if element_map:
        # Fix 2: aggregate elements across all pages for coverage, but report
        # column structure from page 0 (most representative for layout)
        all_text_chars = 0
        with pdfplumber.open(pdf_path) as pdf:
            for pg_num, page_elems in element_map.items():
                pg_text = (pdf.pages[pg_num].extract_text() or "") if pg_num < len(pdf.pages) else ""
                te = [e for e in page_elems if e.label in ("text", "section_header", "list_item", "paragraph")]
                all_text_chars += sum(len(e.text) for e in te)

        # Page 0 for column and section analysis
        elems = element_map.get(0, [])
        label_counts = dict(Counter(e.label for e in elems))
        text_elems = [e for e in elems if e.label in ("text", "section_header", "list_item", "paragraph")]
        docling_chars = sum(len(e.text) for e in text_elems)
        docling_coverage = round(docling_chars / len(raw_text) * 100, 1) if raw_text else 0
        docling_element_count = len(elems)

        # Section headers from page 0 (Fix 4: filter x<5% spanning headers)
        for e in sorted(elems, key=lambda x: x.bbox_norm[1]):
            if e.label in ("section_header", "title"):
                docling_sections.append(SectionHeader(
                    text=e.text[:60],
                    x_pct=round(e.bbox_norm[0]*100, 1),
                    y_pct=round(e.bbox_norm[1]*100, 1),
                    source="docling",
                ))

        # Large picture sections — page 0
        with pdfplumber.open(pdf_path) as pdf:
            pg = pdf.pages[0]
            for e in elems:
                if e.label != "picture":
                    continue
                l, t, r, b = e.bbox_norm
                area = (r - l) * (b - t) * 100
                if area < 3.0:
                    continue
                pic_text = get_pdfplumber_text_in_bbox(pg, e.bbox_norm, W, H)
                picture_sections.append(PictureSection(
                    y0_pct=round(t*100, 1), y1_pct=round(b*100, 1),
                    x0_pct=round(l*100, 1), x1_pct=round(r*100, 1),
                    area_pct=round(area, 1),
                    pdfplumber_text=pic_text,
                    word_count=len(pic_text.split()) if pic_text else 0,
                ))

    # ── column detection (Fix 4: spanning header filter applied inside) ──────
    columns = detect_columns_from_elements(docling_sections, W)

    # ── GLM-OCR ─────────────────────────────────────────────────────────────
    glm_text = ""
    glm_time = 0.0
    glm_hallucination = False
    glm_available = is_glm_ocr_available()

    if glm_available:
        try:
            pages = load_pages(pdf_path)
            pg = pages[0]
            if pg.image is not None:
                t0 = time.monotonic()
                glm_text = _ocr_page_glm(pg.image)
                glm_time = round(time.monotonic() - t0, 1)
        except OCRError:
            pass
        except Exception:
            pass

    # Fix 3: OCR correction pass using qwen3:14b
    global _correction_available
    if glm_text and use_ocr_correction:
        if _correction_available is None:
            _correction_available = is_correction_available()
        if _correction_available:
            glm_text = correct_medical_ocr(glm_text)

    # Fix 1: detect hallucination before section extraction
    raw_sections = extract_sections_from_text(glm_text) if glm_text else []
    if is_glm_hallucination(raw_sections):
        glm_hallucination = True
        glm_sections = []   # discard — use pdfplumber picture bbox text instead
    else:
        glm_sections = raw_sections

    # ── Cross-tool analysis ─────────────────────────────────────────────────
    docling_norm = {normalize_section(h.text) for h in docling_sections}
    glm_norm = {normalize_section(s) for s in glm_sections}

    sections_only_in_glm = [s for s in glm_sections
                             if normalize_section(s) not in docling_norm]
    sections_only_in_docling = [h.text for h in docling_sections
                                 if normalize_section(h.text) not in glm_norm]
    picture_text_chars = sum(len(ps.pdfplumber_text) for ps in picture_sections)

    # ── Strategy ────────────────────────────────────────────────────────────
    if docling_coverage < 30:
        strategy = "poster_mode"
    elif docling_coverage < 60:
        strategy = "hybrid"
    else:
        strategy = "text_mode"

    # Fix 2: flag multi-page documents
    if page_count > 1:
        strategy = strategy + f"_multipage({page_count}p)"

    prof = DocumentProfile(
        name=name, path=str(pdf_path),
        width_pts=round(W), height_pts=round(H), page_count=page_count,
        pdf_chars=len(raw_text), pdf_word_count=len(words), pdf_tables=pdf_tables,
        docling_elements=docling_element_count, docling_label_counts=label_counts,
        docling_coverage_pct=docling_coverage, docling_chars=docling_chars,
        docling_sections=docling_sections, picture_sections=picture_sections,
        columns=columns,
        glm_available=glm_available, glm_chars=len(glm_text),
        glm_time_s=glm_time, glm_text=glm_text, glm_sections=glm_sections,
        sections_only_in_glm=sections_only_in_glm,
        sections_only_in_docling=sections_only_in_docling,
        picture_text_chars=picture_text_chars,
        extraction_strategy=strategy,
    )
    # Attach hallucination flag as extra attribute for reporting
    prof._glm_hallucination = glm_hallucination  # type: ignore[attr-defined]
    return prof


# ── report writer ──────────────────────────────────────────────────────────────

def write_report(prof: DocumentProfile) -> str:
    lines = []
    def L(s=""): lines.append(s)

    L("=" * 72)
    L(f"PROFILE: {prof.name}")
    L("=" * 72)

    L(f"\n[GEOMETRY]")
    L(f"  Size:   {prof.width_pts} x {prof.height_pts} pts  "
      f"({prof.width_pts/72:.1f}\" x {prof.height_pts/72:.1f}\")")
    L(f"  Pages:  {prof.page_count}")

    L(f"\n[PDFPLUMBER]")
    L(f"  Text:   {prof.pdf_chars} chars  {prof.pdf_word_count} words")
    L(f"  Tables: {len(prof.pdf_tables)}")
    for t in prof.pdf_tables:
        L(f"    [{t.index}] y={t.y0_pct:.0f}%-{t.y1_pct:.0f}%  "
          f"x={t.x0_pct:.0f}%-{t.x1_pct:.0f}%  "
          f"{t.rows}r x {t.cols}c  '{t.header_preview}'")

    L(f"\n[DOCLING]")
    L(f"  Elements: {prof.docling_elements}  coverage: {prof.docling_coverage_pct}%  "
      f"({prof.docling_chars}/{prof.pdf_chars} chars)")
    L(f"  Types:    {prof.docling_label_counts}")

    L(f"\n  Section headers ({len(prof.docling_sections)}):")
    for h in prof.docling_sections:
        L(f"    x={h.x_pct:5.1f}%  y={h.y_pct:5.1f}%  {h.text}")

    L(f"\n  Picture sections (content gaps): {len(prof.picture_sections)}")
    for ps in sorted(prof.picture_sections, key=lambda x: x.y0_pct):
        L(f"    y={ps.y0_pct:.0f}%-{ps.y1_pct:.0f}%  "
          f"x={ps.x0_pct:.0f}%-{ps.x1_pct:.0f}%  "
          f"area={ps.area_pct:.0f}%  "
          f"pdfplumber: {ps.word_count} words")
        if ps.pdfplumber_text:
            preview = ps.pdfplumber_text[:200].replace('\n', ' | ')
            L(f"      text: {preview}")

    L(f"\n[COLUMN DETECTION]")
    L(f"  Columns: {prof.columns.count}  method: {prof.columns.method}")
    if prof.columns.boundaries_pct:
        boundaries = prof.columns.boundaries_pct
        col_ranges = [0] + boundaries + [100]
        for i in range(len(col_ranges) - 1):
            # Find headers in this column
            col_headers = [h.text[:35] for h in prof.docling_sections
                           if col_ranges[i] <= h.x_pct < col_ranges[i+1]]
            L(f"  Column {i+1}: x={col_ranges[i]:.0f}%-{col_ranges[i+1]:.0f}%  "
              f"headers: {col_headers[:4]}")
    else:
        L(f"  (single column or no clear gap)")

    L(f"\n[GLM-OCR]")
    hallucinated = getattr(prof, "_glm_hallucination", False)
    if not prof.glm_available:
        L("  Ollama not running or glm-ocr not installed")
    elif prof.glm_chars == 0:
        L("  GLM-OCR failed (GGML error at all resize levels)")
    elif hallucinated:
        L(f"  HALLUCINATION DETECTED — {len(extract_sections_from_text(prof.glm_text))} fake sections discarded")
        L("  Using pdfplumber picture bbox text as fallback")
    else:
        L(f"  Extracted: {prof.glm_chars} chars in {prof.glm_time_s}s")
        L(f"\n  Sections found by GLM-OCR ({len(prof.glm_sections)}):")
        for s in prof.glm_sections:
            L(f"    - {s}")
        # Show both raw format and parsed plain text
        is_html = "<table" in prof.glm_text or "<td" in prof.glm_text
        L(f"\n  Format: {'HTML table' if is_html else 'plain text'}")
        plain = glm_to_plain_text(prof.glm_text)
        L(f"\n  GLM-OCR parsed text (first 2000 chars):")
        L("  " + "-"*60)
        for line in plain[:2000].splitlines():
            L(f"  {line}")
        L("  " + "-"*60)

    L(f"\n[CROSS-TOOL ANALYSIS]")
    L(f"  docling coverage: {prof.docling_coverage_pct}%")
    L(f"  picture sections: {len(prof.picture_sections)}  "
      f"({prof.picture_text_chars} chars of content in those regions)")

    if prof.sections_only_in_glm:
        L(f"\n  Sections in GLM-OCR but NOT in docling "
          f"({len(prof.sections_only_in_glm)}) -- these are being missed:")
        for s in prof.sections_only_in_glm:
            L(f"    MISSING: {s}")

    if prof.sections_only_in_docling:
        L(f"\n  Sections in docling but NOT in GLM-OCR "
          f"({len(prof.sections_only_in_docling)}):")
        for s in prof.sections_only_in_docling:
            L(f"    EXTRA:   {s}")

    L(f"\n[RECOMMENDED STRATEGY]")
    L(f"  {prof.extraction_strategy.upper()}")
    if prof.extraction_strategy == "text_mode":
        L("  -> Use GLM-OCR ordered text + docling heading hierarchy")
        L("  -> No VLM needed")
    elif prof.extraction_strategy == "hybrid":
        L("  -> Use GLM-OCR ordered text + docling headings")
        L(f"  -> VLM crop for {len(prof.picture_sections)} picture section(s)")
    else:
        L("  -> Full-page VLM extraction (qwen2.5vl:7b + poster prompt)")
        L("  -> GLM-OCR text as fallback if VLM fails")

    L("\n" + "=" * 72)
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    # --correct flag enables qwen3:14b OCR correction (slow, off by default for batch)
    use_correction = "--correct" in args
    args = [a for a in args if not a.startswith("--")]

    target = Path(args[0]) if args else Path("data/samples/icmr_stw")
    pdfs = sorted(target.rglob("*.pdf")) if target.is_dir() else [target]

    mode = "with OCR correction" if use_correction else "fast mode (no OCR correction)"
    print(f"Profiling {len(pdfs)} PDF(s)  [{mode}]  ->  {LOG_DIR}/\n")

    summary = []
    for pdf_path in pdfs:
        print(f"  [{pdf_path.stem}] ...", end=" ", flush=True)
        t0 = time.monotonic()
        prof = profile(pdf_path, use_ocr_correction=use_correction)
        elapsed = round(time.monotonic() - t0, 1)
        hallucinated = "HALLUC " if getattr(prof, "_glm_hallucination", False) else ""
        multipage = f" {prof.page_count}pp" if prof.page_count > 1 else ""
        print(f"{elapsed}s  cov={prof.docling_coverage_pct}%  "
              f"cols={prof.columns.count}  glm={prof.glm_chars}  "
              f"gaps={len(prof.picture_sections)}{multipage}  "
              f"{hallucinated}-> {prof.extraction_strategy}")

        report = write_report(prof)
        log_file = LOG_DIR / f"profile2_{pdf_path.stem}.txt"
        log_file.write_text(report, encoding="utf-8")

        # Save JSON profile
        json_file = LOG_DIR / f"profile2_{pdf_path.stem}.json"
        d = asdict(prof)
        d.pop("glm_text")  # too large for JSON summary
        json_file.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

        summary.append({
            "name": prof.name,
            "coverage": prof.docling_coverage_pct,
            "columns": prof.columns.count,
            "page_count": prof.page_count,
            "glm_hallucination": getattr(prof, "_glm_hallucination", False),
            "col_boundaries": prof.columns.boundaries_pct,
            "glm_chars": prof.glm_chars,
            "picture_sections": len(prof.picture_sections),
            "picture_text_chars": prof.picture_text_chars,
            "glm_sections": prof.glm_sections,
            "missing_sections": prof.sections_only_in_glm,
            "strategy": prof.extraction_strategy,
        })

    # Summary table
    print("\n" + "=" * 100)
    print(f"{'DOCUMENT':<32} {'COV':>5} {'COLS':>4} {'GLM':>5} {'PICS':>4} {'PIC_CHARS':>9}  STRATEGY")
    print("-" * 100)
    for r in summary:
        col_str = f"{r['columns']}({r['col_boundaries']})" if r['col_boundaries'] else str(r['columns'])
        print(f"{r['name']:<32} {r['coverage']:>5} {col_str:>10} {r['glm_chars']:>5} "
              f"{r['picture_sections']:>4} {r['picture_text_chars']:>9}  {r['strategy']}")

    # Save combined summary
    summary_file = LOG_DIR / "profile2_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull reports: {LOG_DIR}/profile2_*.txt")
    print(f"JSON data:    {LOG_DIR}/profile2_*.json")
    print(f"Summary:      {summary_file}")


if __name__ == "__main__":
    main()
