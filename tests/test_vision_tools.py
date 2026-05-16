"""Smoke test for vision_tools — requires Ollama running with qwen2.5vl:7b loaded."""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from pathlib import Path
from cloak.ingestion.pdf_tools import load_pages
from cloak.ingestion.vision_tools import full_page_extract, region_describe, judge_quality

PDF = Path("data/raw/cardiology/bradyarrhythmia.pdf")


def main():
    print(f"Loading {PDF.name} ...")
    pages = load_pages(PDF)
    pg = pages[0]
    print(f"  Page 0: {len(pg.regions)} regions, {len(pg.tables)} tables\n")

    # ── Test 1: region_describe on first ECG ──────────────────────────────
    if pg.regions:
        r = pg.regions[0]
        print(f"[TEST 1] region_describe — label={r.label}, size={r.image.size}")
        desc = region_describe(r.image, r.label)
        print(f"  Response ({len(desc)} chars):\n{desc[:600]}\n")
    else:
        print("[TEST 1] No regions — skipping\n")

    # ── Test 2: full_page_extract ─────────────────────────────────────────
    print("[TEST 2] full_page_extract on page 0 ...")
    md = full_page_extract(pg.image)
    print(f"  Extracted {len(md)} chars. First 600:\n{md[:600]}\n")

    # ── Test 3: judge_quality ─────────────────────────────────────────────
    print("[TEST 3] judge_quality ...")
    result = judge_quality(pg.image, md)
    print(f"  Score : {result['score']}")
    print(f"  Action: {result['action']}")
    print(f"  Gaps  : {result['gaps'][:3]}")


if __name__ == "__main__":
    main()
