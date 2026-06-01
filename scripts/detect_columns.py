"""detect_columns.py — detect column structure from docling section headers"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cloak.profiling.doc_profiler import run_docling_pass

pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/samples/icmr_stw/cardiology_af.pdf")
element_map = run_docling_pass(pdf_path)
elems = element_map.get(0, [])

# ── 1. Section headers reveal column structure ─────────────────────────────
headers = [e for e in elems if e.label in ("section_header", "title")]
print("Section headers (sorted by Y):")
for h in sorted(headers, key=lambda e: e.bbox_norm[1]):
    x = h.bbox_norm[0] * 100
    y = h.bbox_norm[1] * 100
    print(f"  x={x:5.1f}%  y={y:5.1f}%  {h.text[:55]}")

# ── 2. Detect column boundaries from header X clustering ──────────────────
xs = sorted([h.bbox_norm[0] * 100 for h in headers if h.bbox_norm[0] > 0.02])
print(f"\nHeader X positions (sorted): {[round(x, 1) for x in xs]}")

# Find significant gaps in X distribution
gaps = []
for i in range(len(xs) - 1):
    gap = xs[i+1] - xs[i]
    if gap > 10:
        boundary = (xs[i] + xs[i+1]) / 2
        gaps.append(boundary)

if gaps:
    boundaries = sorted(gaps)
    print(f"Column boundaries detected at x = {[round(b, 1) for b in boundaries]}%")
    # Label columns
    cols = [0] + boundaries + [100]
    for i in range(len(cols) - 1):
        print(f"  Column {i+1}: {cols[i]:.0f}% -> {cols[i+1]:.0f}%")
else:
    print("No multi-column structure detected")
    boundaries = []

# ── 3. Re-sort elements by column then Y ──────────────────────────────────
def get_column(x_pct, boundaries):
    for i, b in enumerate(boundaries):
        if x_pct < b:
            return i
    return len(boundaries)

if boundaries:
    print("\n--- CORRECT READING ORDER (column-first sort) ---")
    def sort_key(e):
        x = e.bbox_norm[0] * 100
        y = e.bbox_norm[1] * 100
        col = get_column(x, boundaries)
        return (col, y)

    for e in sorted(elems, key=sort_key):
        if e.label in ("picture",):
            continue
        col = get_column(e.bbox_norm[0] * 100, boundaries) + 1
        y = e.bbox_norm[1] * 100
        text = (e.text or "")[:60].replace("\n", " ")
        print(f"  [COL{col}  y={y:5.1f}%]  {e.label:<16}  {text}")
