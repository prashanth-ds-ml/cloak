"""
profile_icmr.py — profile a single ICMR STW PDF and print a structured report.
Usage: python scripts/profile_icmr.py <pdf_path>
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/samples/icmr_stw/cardiology_af.pdf")

import pdfplumber
from collections import Counter

# ── helpers ──────────────────────────────────────────────────────────────────

def detect_columns(section_headers, page_width):
    """
    Detect column boundaries from section header X positions.
    Filters spanning/centered headers (x > 25% = centered, not column-anchored).
    Returns list of boundary X percentages.
    """
    # Only use left-anchored headers (x < 60%) as column anchors
    col_xs = [h["x_pct"] for h in section_headers if h["x_pct"] < 60 and h["x_pct"] > 2]
    if not col_xs:
        return []
    col_xs_sorted = sorted(set(round(x / 5) * 5 for x in col_xs))  # snap to 5% grid

    # Find gaps > 12% in the X distribution
    boundaries = []
    for i in range(len(col_xs_sorted) - 1):
        gap = col_xs_sorted[i+1] - col_xs_sorted[i]
        if gap > 12:
            boundaries.append((col_xs_sorted[i] + col_xs_sorted[i+1]) / 2)

    return sorted(set(round(b) for b in boundaries))


def get_column(x_pct, boundaries):
    for i, b in enumerate(boundaries):
        if x_pct < b:
            return i + 1
    return len(boundaries) + 1


# ── pdfplumber pass ──────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"PROFILING: {pdf_path.name}")
print(f"{'='*70}")

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    W, H = page.width, page.height
    page_count = len(pdf.pages)
    raw_text = page.extract_text() or ""
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    tables = page.find_tables()
    images = page.images
    total_img_area = sum(i["width"] * i["height"] for i in images)
    img_ratio = total_img_area / (W * H)

print(f"\n[PAGE GEOMETRY]")
print(f"  Size:        {W:.0f} x {H:.0f} pts  ({W/72:.1f}\" x {H/72:.1f}\")")
print(f"  Pages:       {page_count}")
print(f"  pdfplumber:  {len(raw_text)} chars  {len(words)} words")
print(f"  Tables:      {len(tables)}")
print(f"  Images:      {len(images)}  (area ratio: {img_ratio:.1%})")

print(f"\n[PDFPLUMBER TABLES]")
with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    for i, t in enumerate(page.find_tables()):
        x0, y0, x1, y1 = t.bbox
        rows = t.extract()
        n_rows = len(rows) if rows else 0
        n_cols = len(rows[0]) if rows and rows[0] else 0
        header = str(rows[0])[:80] if rows else ""
        print(f"  Table {i+1}: x={x0/W*100:.0f}%-{x1/W*100:.0f}%  y={y0/H*100:.0f}%-{y1/H*100:.0f}%  "
              f"{n_rows}r x {n_cols}c")
        print(f"    header: {header}")

# ── docling pass ─────────────────────────────────────────────────────────────
print(f"\n[DOCLING ANALYSIS]")
try:
    from cloak.profiling.doc_profiler import run_docling_pass
    element_map = run_docling_pass(pdf_path)
    if not element_map:
        print("  Docling returned nothing.")
        sys.exit(0)

    elems = element_map.get(0, [])
    label_counts = Counter(e.label for e in elems)
    print(f"  Total elements: {len(elems)}")
    print(f"  By type:  {dict(label_counts)}")

    text_elems = [e for e in elems if e.label in ("text", "section_header", "list_item", "paragraph")]
    docling_chars = sum(len(e.text) for e in text_elems)
    coverage = docling_chars / len(raw_text) * 100 if raw_text else 0
    print(f"  Text chars:  docling={docling_chars}  pdfplumber={len(raw_text)}  coverage={coverage:.1f}%")

    # Section headers with positions
    headers = [e for e in elems if e.label in ("section_header", "title")]
    header_data = [{"text": h.text[:50], "x_pct": h.bbox_norm[0]*100, "y_pct": h.bbox_norm[1]*100}
                   for h in sorted(headers, key=lambda e: e.bbox_norm[1])]

    print(f"\n[SECTION HEADERS ({len(headers)} found)]")
    for h in header_data:
        print(f"  x={h['x_pct']:5.1f}%  y={h['y_pct']:5.1f}%  {h['text']}")

    # Column detection
    boundaries = detect_columns(header_data, W)
    print(f"\n[COLUMN DETECTION]")
    if boundaries:
        cols = [0] + boundaries + [100]
        print(f"  Boundaries detected at x = {boundaries}%")
        for i in range(len(cols)-1):
            print(f"  Column {i+1}: {cols[i]:.0f}% -> {cols[i+1]:.0f}%")
    else:
        print("  Single column (no gap detected)")

    # Pictures — what's missing from docling text
    pics = [e for e in elems if e.label == "picture"]
    print(f"\n[PICTURES (docling treats these as images, not text)]")
    for p in sorted(pics, key=lambda e: e.bbox_norm[1]):
        x, y = p.bbox_norm[0]*100, p.bbox_norm[1]*100
        w = (p.bbox_norm[2] - p.bbox_norm[0]) * 100
        h_size = (p.bbox_norm[3] - p.bbox_norm[1]) * 100
        print(f"  x={x:.0f}%  y={y:.0f}%  size={w:.0f}%w x {h_size:.0f}%h  {'<-- LARGE, likely content' if w*h_size > 500 else ''}")

    # Column-sorted reading order
    if boundaries:
        print(f"\n[COLUMN-SORTED READING ORDER (all text elements)]")
        def sort_key(e):
            x = e.bbox_norm[0] * 100
            y = e.bbox_norm[1] * 100
            col = get_column(x, boundaries)
            return (col, y)
        for e in sorted(text_elems, key=sort_key):
            col = get_column(e.bbox_norm[0]*100, boundaries)
            y = e.bbox_norm[1]*100
            text = (e.text or "")[:70].replace("\n", " ")
            print(f"  [C{col} y={y:5.1f}%]  {e.label:<16}  {text}")

    # Summary
    print(f"\n[SUMMARY]")
    print(f"  Page size:        {W:.0f}x{H:.0f}pts  ({page_count} page)")
    print(f"  Columns:          {len(boundaries)+1 if boundaries else 1}")
    print(f"  Docling coverage: {coverage:.1f}%  ({docling_chars}/{len(raw_text)} chars)")
    print(f"  Missing content:  {len(raw_text)-docling_chars} chars not in docling elements")
    print(f"  Large pictures:   {sum(1 for p in pics if (p.bbox_norm[2]-p.bbox_norm[0])*(p.bbox_norm[3]-p.bbox_norm[1])>0.05)} (likely contain text/flowchart content)")
    if boundaries:
        print(f"  Column boundaries: {boundaries}%")
    print(f"  pdfplumber tables: {len(tables)}")

except Exception as exc:
    import traceback
    print(f"  ERROR: {exc}")
    traceback.print_exc()
