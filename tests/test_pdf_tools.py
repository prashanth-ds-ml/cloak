"""Quick smoke test for pdf_tools — run with: python tests/test_pdf_tools.py"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pathlib import Path
from cloak.ingestion.pdf_tools import load_pages

PDF = Path("data/raw/cardiology/bradyarrhythmia.pdf")

def main():
    print(f"Loading: {PDF.name}")
    pages = load_pages(PDF)
    print(f"Pages: {len(pages)}")

    for pg in pages:
        n_text = sum(1 for b in pg.blocks if b.block_type == "text")
        n_img  = sum(1 for b in pg.blocks if b.block_type == "image")
        labels = [r.label for r in pg.regions]
        print(
            f"\n--- Page {pg.page_num} "
            f"({pg.width:.0f}x{pg.height:.0f} pts, rendered {pg.image.size}) ---"
        )
        print(f"  Blocks : {len(pg.blocks)}  ({n_text} text, {n_img} image)")
        print(f"  Regions: {len(pg.regions)}  labels={labels}")
        print(f"  Tables : {len(pg.tables)}")

        for b in [b for b in pg.blocks if b.block_type == "text"][:4]:
            preview = b.text[:90].replace("\n", " ")
            print(f"  [{b.bbox[0]:.0f},{b.bbox[1]:.0f}]  {preview!r}")

        for i, r in enumerate(pg.regions):
            print(f"  Region {i}: {r.label}  {r.image.size}px  bbox={tuple(round(v) for v in r.bbox)}")

        for t in pg.tables:
            print(f"  Table: {len(t.rows)} rows x {len(t.rows[0]) if t.rows else 0} cols")

if __name__ == "__main__":
    main()
