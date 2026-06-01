"""
profile_page.py — deep profiling of a single PDF page using all available tools.
Usage: python scripts/profile_page.py data/samples/icmr_stw/cardiology_af.pdf
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/samples/icmr_stw/cardiology_af.pdf")

import pdfplumber
import statistics

# ── pdfplumber ────────────────────────────────────────────────────────────────
print("=" * 70)
print("1. PAGE GEOMETRY (pdfplumber)")
print("=" * 70)

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[0]
    W, H = page.width, page.height
    print(f"  Size:   {W:.0f} x {H:.0f} pts  ({W/72:.2f}\" x {H/72:.2f}\")")
    print(f"  Pages:  {len(pdf.pages)}")

    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    raw_text = page.extract_text() or ""
    tables = page.find_tables()
    images = page.images

    print(f"\n  Text:   {len(raw_text)} chars, {len(words)} words")
    print(f"  Tables: {len(tables)}")
    print(f"  Images: {len(images)}")

    # X distribution of words -> column detection
    print("\n" + "=" * 70)
    print("2. COLUMN DETECTION (word X-positions)")
    print("=" * 70)
    x_mids = [w["x0"] + (w["x1"] - w["x0"]) / 2 for w in words]
    if x_mids:
        # Bucket into 10 bins
        bucket_w = W / 10
        buckets = [0] * 10
        for x in x_mids:
            b = min(int(x / bucket_w), 9)
            buckets[b] += 1
        print("  X distribution (10 buckets across page width):")
        for i, count in enumerate(buckets):
            bar = "#" * (count // 2)
            pct = i * 10
            print(f"  {pct:3d}-{pct+10}%  {bar} ({count})")
        # Simple column boundary: find the gap
        mid_buckets = buckets[2:8]  # middle 60% of page
        if min(mid_buckets) < max(mid_buckets) * 0.3:
            gap_idx = mid_buckets.index(min(mid_buckets)) + 2
            gap_x = (gap_idx + 0.5) * bucket_w
            print(f"\n  -> Column gap detected around x={gap_x:.0f} ({gap_x/W*100:.0f}% of page width)")
        else:
            print("\n  -> No clear column gap (single-column or uniform)")

    # Image coverage
    print("\n" + "=" * 70)
    print("3. IMAGE COVERAGE")
    print("=" * 70)
    total_img_area = sum(i["width"] * i["height"] for i in images)
    page_area = W * H
    print(f"  Image area ratio: {total_img_area/page_area:.1%}")
    for i, img in enumerate(images):
        x_pct = img["x0"] / W * 100
        y_pct = img["top"] / H * 100
        w_pct = img["width"] / W * 100
        h_pct = img["height"] / H * 100
        area_pct = (img["width"] * img["height"]) / page_area * 100
        print(f"  Image {i+1}: x={x_pct:.0f}% y={y_pct:.0f}% size={w_pct:.0f}%x{h_pct:.0f}%  area={area_pct:.1f}%")

    # Table bboxes
    if tables:
        print("\n" + "=" * 70)
        print("4. TABLE LOCATIONS")
        print("=" * 70)
        for i, t in enumerate(tables):
            x0, y0, x1, y1 = t.bbox
            print(f"  Table {i+1}: x={x0/W*100:.0f}%-{x1/W*100:.0f}%  y={y0/H*100:.0f}%-{y1/H*100:.0f}%")
            rows = t.extract()
            if rows:
                print(f"    {len(rows)} rows x {len(rows[0]) if rows[0] else 0} cols")
                print(f"    Header: {rows[0]}")

    # Word spatial analysis — detect rows
    print("\n" + "=" * 70)
    print("5. TEXT SPATIAL LAYOUT (first 40 words with position)")
    print("=" * 70)
    print(f"  {'X%':>5}  {'Y%':>5}  Text")
    print(f"  {'---':>5}  {'---':>5}  ----")
    for w in sorted(words, key=lambda x: (x["top"], x["x0"]))[:40]:
        x_pct = w["x0"] / W * 100
        y_pct = w["top"] / H * 100
        print(f"  {x_pct:5.1f}  {y_pct:5.1f}  {w['text']}")

# ── docling ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("6. DOCLING LAYOUT ANALYSIS")
print("=" * 70)

try:
    from cloak.profiling.doc_profiler import run_docling_pass
    element_map = run_docling_pass(pdf_path)
    if element_map:
        elems = element_map.get(0, [])
        from collections import Counter
        label_counts = Counter(e.label for e in elems)
        print(f"  Total elements: {len(elems)}")
        print(f"  By type: {dict(label_counts)}")

        # Coverage
        text_elems = [e for e in elems if e.label in ("text", "section_header", "list_item")]
        total_docling_chars = sum(len(e.text) for e in text_elems)
        print(f"\n  Docling text chars: {total_docling_chars}")
        print(f"  pdfplumber text chars: {len(raw_text)}")
        print(f"  Docling coverage: {total_docling_chars/len(raw_text)*100:.1f}%")

        print("\n  All elements (label | x% | y% | text[:60]):")
        print(f"  {'LABEL':<16} {'X':>5} {'Y':>5}  TEXT")
        print(f"  {'-----':<16} {'--':>5} {'--':>5}  ----")
        for e in elems:
            l, t, r, b = e.bbox_norm
            x_pct = l * 100
            y_pct = t * 100
            text_preview = (e.text or "")[:60].replace("\n", " ")
            print(f"  {e.label:<16} {x_pct:5.1f} {y_pct:5.1f}  {text_preview}")
    else:
        print("  Docling returned no elements.")
except Exception as exc:
    print(f"  Docling failed: {exc}")

print("\n" + "=" * 70)
print("PROFILING COMPLETE")
print("=" * 70)
