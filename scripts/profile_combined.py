"""
profile_combined.py -- deep profile a PDF using pdfplumber + docling + GLM-OCR.

Outputs:
  data/batch_logs/profile_{stem}.txt  -- full per-document report
  data/batch_logs/profile_summary.csv -- one-line summary per doc

Usage:
  python scripts/profile_combined.py data/samples/icmr_stw/
  python scripts/profile_combined.py data/samples/icmr_stw/cardiology_af.pdf
"""
import sys, io, re, json, time
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pdfplumber
from cloak.profiling.doc_profiler import run_docling_pass
from cloak.extraction.ocr_tools import _ocr_page_glm, is_glm_ocr_available, OCRError
from cloak.extraction.pdf_tools import load_pages

LOG_DIR = Path("data/batch_logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── helpers ────────────────────────────────────────────────────────────────────

def word_set(text):
    return set(re.findall(r'\b[a-zA-Z]{3,}\b', text.lower()))

def detect_columns_from_words(words, page_width):
    """Use word X-positions to find column boundaries via gap detection."""
    if not words:
        return []
    bucket_count = 20
    bw = page_width / bucket_count
    buckets = [0] * bucket_count
    for w in words:
        b = min(int(w["x0"] / bw), bucket_count - 1)
        buckets[b] += 1
    # Find buckets with significantly fewer words (gaps between columns)
    max_count = max(buckets) if buckets else 1
    threshold = max_count * 0.15
    in_gap = False
    gap_starts = []
    for i, count in enumerate(buckets):
        if count <= threshold and not in_gap:
            in_gap = True
            gap_start = i
        elif count > threshold and in_gap:
            in_gap = False
            gap_mid = (gap_start + i) / 2 * bw
            # Only report gaps in the middle 80% of the page
            if page_width * 0.10 < gap_mid < page_width * 0.90:
                gap_starts.append(round(gap_mid / page_width * 100, 1))
    return gap_starts

def extract_sections_from_text(text):
    """Find ALL-CAPS lines in text -- these are section headings."""
    sections = []
    for line in text.splitlines():
        stripped = line.strip()
        if (stripped and len(stripped) > 3
                and stripped == stripped.upper()
                and not stripped.isdigit()
                and not stripped.startswith('|')
                and re.search(r'[A-Z]', stripped)):
            sections.append(stripped)
    return sections

def profile_pdf(pdf_path):
    """Profile a single PDF. Returns dict of all metrics."""
    pdf_path = Path(pdf_path)
    name = pdf_path.stem
    result = {"name": name, "path": str(pdf_path)}
    lines = []  # log lines

    def log(s=""):
        lines.append(s)

    log("=" * 72)
    log(f"PROFILE: {name}")
    log("=" * 72)

    # ── pdfplumber ──────────────────────────────────────────────────────────
    log("\n[1] PDFPLUMBER")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            W, H = page.width, page.height
            page_count = len(pdf.pages)
            raw_text = page.extract_text() or ""
            words = page.extract_words(x_tolerance=3, y_tolerance=3)
            tables = page.find_tables()
            images = page.images
            img_area = sum(i["width"] * i["height"] for i in images) / (W * H)

            result.update({
                "W": round(W), "H": round(H), "pages": page_count,
                "pdf_chars": len(raw_text), "pdf_words": len(words),
                "pdf_tables": len(tables), "pdf_images": len(images),
                "img_area_pct": round(img_area * 100, 1),
            })

            log(f"  Size:    {W:.0f} x {H:.0f} pts  ({W/72:.1f}\" x {H/72:.1f}\")")
            log(f"  Pages:   {page_count}")
            log(f"  Text:    {len(raw_text)} chars  {len(words)} words")
            log(f"  Tables:  {len(tables)}  Images: {len(images)} (area {img_area:.1%})")

            # Column detection from word positions
            col_gaps = detect_columns_from_words(words, W)
            result["col_gaps_pct"] = col_gaps
            if col_gaps:
                log(f"  Columns: gaps at x = {col_gaps}%  ({len(col_gaps)+1} columns)")
            else:
                log("  Columns: no clear column gap detected")

            # Tables detail
            log(f"\n  Tables detail:")
            table_data = []
            for i, t in enumerate(tables):
                x0, y0, x1, y1 = t.bbox
                rows = t.extract()
                nr = len(rows) if rows else 0
                nc = len(rows[0]) if rows and rows[0] else 0
                hdr = str(rows[0][0] or "")[:60].replace("\n", " ") if rows and rows[0] else ""
                log(f"    [{i+1}] x={x0/W*100:.0f}-{x1/W*100:.0f}%  y={y0/H*100:.0f}-{y1/H*100:.0f}%  {nr}r x {nc}c  '{hdr}'")
                table_data.append({"bbox_y0_pct": round(y0/H*100, 1), "bbox_y1_pct": round(y1/H*100, 1),
                                    "rows": nr, "cols": nc})
            result["table_data"] = table_data

    except Exception as exc:
        log(f"  ERROR: {exc}")
        result["pdf_error"] = str(exc)

    # ── docling ─────────────────────────────────────────────────────────────
    log("\n[2] DOCLING")
    try:
        element_map = run_docling_pass(pdf_path)
        if element_map:
            elems = element_map.get(0, [])
            label_counts = dict(Counter(e.label for e in elems))
            text_elems = [e for e in elems if e.label in ("text", "section_header", "list_item", "paragraph")]
            docling_chars = sum(len(e.text) for e in text_elems)
            pdf_chars = result.get("pdf_chars", 1)
            coverage = round(docling_chars / pdf_chars * 100, 1) if pdf_chars else 0

            result.update({
                "docling_elements": len(elems),
                "docling_labels": label_counts,
                "docling_chars": docling_chars,
                "docling_coverage_pct": coverage,
            })

            log(f"  Elements: {len(elems)}  coverage: {coverage}%  ({docling_chars}/{pdf_chars} chars)")
            log(f"  Types: {label_counts}")

            # Section headers
            headers = [(e.bbox_norm[0]*100, e.bbox_norm[1]*100, e.text[:55])
                       for e in elems if e.label in ("section_header", "title")]
            result["docling_headers"] = [{"x": round(x,1), "y": round(y,1), "text": t}
                                          for x, y, t in headers]
            log(f"\n  Section headers ({len(headers)}):")
            for x, y, t in sorted(headers, key=lambda h: h[1]):
                log(f"    x={x:5.1f}%  y={y:5.1f}%  {t}")

            # Large pictures (content gaps)
            pics = [e for e in elems if e.label == "picture"]
            large_pics = [e for e in pics
                          if (e.bbox_norm[2]-e.bbox_norm[0]) * (e.bbox_norm[3]-e.bbox_norm[1]) > 0.05]
            result["docling_large_pictures"] = [
                {"x": round(e.bbox_norm[0]*100,1), "y": round(e.bbox_norm[1]*100,1),
                 "w": round((e.bbox_norm[2]-e.bbox_norm[0])*100,1),
                 "h": round((e.bbox_norm[3]-e.bbox_norm[1])*100,1)}
                for e in large_pics
            ]
            log(f"\n  Large pictures (likely content, not logos): {len(large_pics)}")
            for e in sorted(large_pics, key=lambda x: x.bbox_norm[1]):
                x = e.bbox_norm[0]*100; y = e.bbox_norm[1]*100
                w = (e.bbox_norm[2]-e.bbox_norm[0])*100
                h = (e.bbox_norm[3]-e.bbox_norm[1])*100
                log(f"    y={y:.0f}%  x={x:.0f}%  size={w:.0f}%w x {h:.0f}%h  ({w*h/100:.0f}% of page)")

        else:
            log("  No elements returned.")
            result["docling_coverage_pct"] = 0

    except Exception as exc:
        log(f"  ERROR: {exc}")
        result["docling_error"] = str(exc)

    # ── GLM-OCR ─────────────────────────────────────────────────────────────
    log("\n[3] GLM-OCR")
    glm_text = ""
    glm_time = 0
    if not is_glm_ocr_available():
        log("  SKIP: GLM-OCR not available (Ollama not running or model not pulled)")
        result["glm_available"] = False
    else:
        result["glm_available"] = True
        try:
            pages = load_pages(pdf_path)
            pg = pages[0]
            if pg.image is None:
                log("  SKIP: page image not available")
            else:
                t0 = time.monotonic()
                glm_text = _ocr_page_glm(pg.image)
                glm_time = round(time.monotonic() - t0, 1)

                # Extract sections from GLM-OCR (ALL-CAPS lines)
                glm_sections = extract_sections_from_text(glm_text)
                glm_words_set = word_set(glm_text)
                pdf_words_set = word_set(result.get("pdf_chars") and (raw_text if 'raw_text' in dir() else ""))

                result.update({
                    "glm_chars": len(glm_text),
                    "glm_time_s": glm_time,
                    "glm_sections": glm_sections,
                    "glm_word_count": len(glm_words_set),
                })

                log(f"  Extracted: {len(glm_text)} chars in {glm_time}s")
                log(f"  Sections detected by GLM-OCR ({len(glm_sections)}):")
                for s in glm_sections:
                    log(f"    - {s}")

                # Comparison: docling sections vs GLM-OCR sections
                docling_section_texts = set()
                if "docling_headers" in result:
                    for h in result["docling_headers"]:
                        words_in_h = h["text"].strip().upper()
                        if words_in_h:
                            docling_section_texts.add(words_in_h[:30])

                log(f"\n  GLM-OCR text (first 1200 chars):")
                log("  " + "-" * 60)
                for line in glm_text[:1200].splitlines():
                    log(f"  {line}")
                log("  " + "-" * 60)

        except OCRError as exc:
            log(f"  GLM-OCR failed: {exc}")
            result["glm_error"] = str(exc)
        except Exception as exc:
            log(f"  ERROR: {exc}")
            result["glm_error"] = str(exc)

    # ── Combined analysis ───────────────────────────────────────────────────
    log("\n[4] COMBINED ANALYSIS")

    docling_cov = result.get("docling_coverage_pct", 0)
    glm_chars = result.get("glm_chars", 0)
    pdf_chars = result.get("pdf_chars", 0)

    # Determine extraction strategy
    if docling_cov < 30:
        strategy = "poster_mode: VLM full-page (coverage too low for docling path)"
    elif docling_cov < 60:
        strategy = "hybrid: GLM-OCR text + VLM crops for large picture sections"
    else:
        strategy = "text_mode: GLM-OCR ordered text + docling heading structure"

    large_pics = result.get("docling_large_pictures", [])
    col_gaps = result.get("col_gaps_pct", [])

    log(f"  docling coverage:  {docling_cov}%")
    log(f"  GLM-OCR chars:     {glm_chars}")
    log(f"  pdfplumber chars:  {pdf_chars}")
    log(f"  Column gaps at:    {col_gaps}%")
    log(f"  Large pictures:    {len(large_pics)}")
    log(f"  Recommended:       {strategy}")
    result["recommended_strategy"] = strategy

    log("\n" + "=" * 72)

    return result, "\n".join(lines)


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/samples/icmr_stw")

    if target.is_dir():
        pdfs = sorted(target.glob("*.pdf"))
    else:
        pdfs = [target]

    print(f"Profiling {len(pdfs)} PDF(s)...")
    print(f"Logs -> {LOG_DIR}/\n")

    summary_rows = []

    for pdf_path in pdfs:
        print(f"  [{pdf_path.stem}] ...", end=" ", flush=True)
        t0 = time.monotonic()
        result, log_text = profile_pdf(pdf_path)
        elapsed = round(time.monotonic() - t0, 1)
        print(f"{elapsed}s  docling={result.get('docling_coverage_pct',0)}%  glm={result.get('glm_chars',0)} chars")

        # Save per-doc log
        log_file = LOG_DIR / f"profile_{pdf_path.stem}.txt"
        log_file.write_text(log_text, encoding="utf-8")

        # Summary row
        summary_rows.append({
            "name": result["name"],
            "pdf_chars": result.get("pdf_chars", 0),
            "pdf_tables": result.get("pdf_tables", 0),
            "docling_elements": result.get("docling_elements", 0),
            "docling_coverage_pct": result.get("docling_coverage_pct", 0),
            "docling_large_pics": len(result.get("docling_large_pictures", [])),
            "glm_chars": result.get("glm_chars", 0),
            "glm_sections": len(result.get("glm_sections", [])),
            "col_gaps": result.get("col_gaps_pct", []),
            "strategy": result.get("recommended_strategy", "?"),
        })

    # Print summary table
    print("\n" + "=" * 110)
    print(f"{'DOCUMENT':<32} {'PDF':>5} {'PDF':>4} {'DOC':>5} {'COV':>5} {'PICS':>4} {'GLM':>5} {'SECS':>4}  STRATEGY")
    print(f"{'':32} {'chars':>5} {'tbls':>4} {'elms':>5} {'%':>5} {'':>4} {'chars':>5} {'':>4}")
    print("-" * 110)
    for r in summary_rows:
        strat_short = r["strategy"][:35]
        col_str = str(r["col_gaps"]) if r["col_gaps"] else "-"
        print(f"{r['name']:<32} {r['pdf_chars']:>5} {r['pdf_tables']:>4} {r['docling_elements']:>5} "
              f"{r['docling_coverage_pct']:>5} {r['docling_large_pics']:>4} {r['glm_chars']:>5}  "
              f"{col_str:<8}  {strat_short}")

    # Save summary JSON
    summary_file = LOG_DIR / "profile_summary.json"
    summary_file.write_text(json.dumps(summary_rows, indent=2), encoding="utf-8")
    print(f"\nSummary saved to {summary_file}")
    print(f"Full logs in {LOG_DIR}/profile_*.txt")


if __name__ == "__main__":
    main()
