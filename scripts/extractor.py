"""
extractor.py -- Landing.ai-style extraction pipeline.

Flow per document:
  1. Profile   → docling + GLM-OCR + pdfplumber
  2. Extract   → GLM-OCR text + camelot tables + heading promotion
  3. Post-proc → clean artifacts, normalize
  4. Judge     → qwen2.5vl:7b evaluates output quality
  5. Compare   → vs Landing.ai JSON where available

Usage:
  python scripts/extractor.py data/samples/icmr_stw_full/cardiology/cardiology_af.pdf
  python scripts/extractor.py data/samples/icmr_stw_full/cardiology/cardiology_af.pdf --landing-ai "C:/..."
  python scripts/extractor.py --batch   (runs all 5 test docs)
"""
from __future__ import annotations
import sys, io, re, json, time, queue, threading
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pdfplumber
from PIL import Image
import ollama

from cloak.extraction.pdf_tools import load_pages
from cloak.config import MODEL_KEEP_ALIVE, VISION_TIMEOUT
from scripts.profiler_v2 import pass1_profile, pass2_vlm, DocumentProfile

OUT_DIR = Path("data/extraction")
OUT_DIR.mkdir(parents=True, exist_ok=True)

JUDGE_MODEL = "qwen2.5vl:7b"

# ── Test documents — from icmr/ folder with Landing.ai ground truth ──────────
# Each pair: (PDF path, Landing.ai JSON path)
# Covers different strategies: hybrid, text_mode, poster_mode
TEST_DOCS = [
    # Cardiology AF — hybrid (34% docling coverage, complex 3-column layout)
    ("icmr/cardiology/Cardiology_STWs/Atrial_Fibrillation.pdf",
     "icmr/Data_Files Cardiology/Atrial_Fibrillation.parse.json"),

    # Neurology Stroke — text_mode (84% coverage, mostly structured text)
    ("icmr/Neurology/Stroke.pdf",
     "icmr/Neurology_Data_Files/Stroke.parse.json"),

    # Paediatrics Dengue — poster_mode (2.9% coverage, full flowchart)
    ("icmr/Paediatrics/Dengue_Fever.pdf",
     "icmr/Paediatrics_Data_Files/Dengue_Fever.parse.json"),

    # Nephrology AKI — text_mode (82% coverage, clean structured doc)
    ("icmr/Nephrology/Acute_Kidney_Injury.pdf",
     "icmr/Nephrology_Data_Files/Acute_Kidney_Injury.parse.json"),

    # ENT Epistaxis — text_mode (different specialty, validate generalisation)
    ("icmr/ENT/Epistaxis.pdf",
     "icmr/ENT_Data_Files/Epistaxis.parse.json"),
]


# ── Step 2a: Parse GLM-OCR HTML to clean text ─────────────────────────────────

def glm_html_to_markdown(html: str) -> str:
    """Convert GLM-OCR HTML table output to clean markdown."""
    if not html:
        return ""

    if "<table" not in html and "<td" not in html:
        return html  # already plain text

    # Process each table tag
    result_parts = []
    remaining = html

    while "<table" in remaining:
        pre_table = remaining[:remaining.index("<table")]
        if pre_table.strip():
            result_parts.append(_html_to_text(pre_table))

        table_start = remaining.index("<table")
        table_end = remaining.index("</table>") + len("</table>")
        table_html = remaining[table_start:table_end]
        result_parts.append(_html_table_to_md(table_html))
        remaining = remaining[table_end:]

    if remaining.strip():
        result_parts.append(_html_to_text(remaining))

    return "\n\n".join(p for p in result_parts if p.strip())


def _html_to_text(html: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&gt;', '>').replace('&lt;', '<')
    text = text.replace('&nbsp;', ' ').replace('&#39;', "'").replace('&ge;', '≥')
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return '\n'.join(lines)


def _html_table_to_md(table_html: str) -> str:
    """Convert HTML table to markdown table."""
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.DOTALL | re.IGNORECASE)
        row = []
        for cell in cells:
            text = re.sub(r'<br\s*/?>', ' ', cell, flags=re.IGNORECASE)
            text = re.sub(r'<[^>]+>', '', text)
            text = text.replace('&amp;', '&').replace('&gt;', '>').replace('&lt;', '<')
            text = text.replace('&nbsp;', ' ').replace('&#39;', "'")
            row.append(text.strip())
        if row:
            rows.append(row)

    if not rows:
        return ""

    md_rows = []
    for i, row in enumerate(rows):
        md_rows.append("| " + " | ".join(row) + " |")
        if i == 0:
            md_rows.append("| " + " | ".join(["---"] * len(row)) + " |")
    return "\n".join(md_rows)


# ── Step 2b: Promote ALL-CAPS lines to headings ───────────────────────────────

def promote_headings(text: str) -> str:
    """Promote ALL-CAPS lines to ## markdown headings."""
    lines = text.splitlines()
    out = []
    for line in lines:
        s = line.strip()
        if (s and len(s) > 3 and s == s.upper()
                and not s.startswith('|') and not s.isdigit()
                and re.search(r'[A-Z]{3,}', s)
                and not re.fullmatch(r'[\d\s|.,:;()/%-]+', s)):
            out.append(f"\n## {s}")
        else:
            out.append(line)
    return "\n".join(out)


# ── Step 2c: Extract tables via camelot ──────────────────────────────────────

def extract_tables_camelot(pdf_path: Path, prof: DocumentProfile) -> dict[tuple, str]:
    """Extract all pdfplumber-detected tables via camelot stream. Returns {(y0,y1): markdown}."""
    try:
        import camelot
    except ImportError:
        return {}

    result = {}
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        W, H = page.width, page.height
        tables = page.find_tables()

    for t in tables:
        x0, y0, x1, y1 = t.bbox
        # Skip tiny footer tables
        if (y1 - y0) < 20 or (x1 - x0) < 100:
            continue
        # camelot uses bottom-left origin
        c_area = f"{x0:.0f},{H-y1:.0f},{x1:.0f},{H-y0:.0f}"
        try:
            tables_found = camelot.read_pdf(str(pdf_path), pages="1",
                                            flavor="stream", table_areas=[c_area])
            if not tables_found:
                tables_found = camelot.read_pdf(str(pdf_path), pages="1",
                                                flavor="lattice", table_areas=[c_area])
            if tables_found:
                df = tables_found[0].df
                md_rows = []
                for i, row in df.iterrows():
                    cells = [str(v).replace("\n", " ").strip() for v in row]
                    md_rows.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
                md = "\n".join(md_rows)
                if md.strip():
                    y0_pct = round(y0 / H * 100, 1)
                    y1_pct = round(y1 / H * 100, 1)
                    result[(y0_pct, y1_pct)] = md
        except Exception:
            pass
    return result


# ── Step 2d: Insert tables into text at correct position ─────────────────────

def inject_tables(text: str, tables: dict[tuple, str], prof: DocumentProfile) -> str:
    """Insert camelot-extracted tables at their Y position in the text."""
    if not tables:
        return text

    # For each table, find matching content in text and replace with clean version
    for (y0, y1), table_md in sorted(tables.items()):
        # Try to find the table header line in the text
        first_row = table_md.split('\n')[0] if table_md else ""
        cells = [c.strip() for c in first_row.split('|') if c.strip()]
        if cells:
            header_word = cells[0][:15].upper()
            # If the header appears in text, it's likely already there — enhance it
            # Just append tables that don't appear to be in the GLM-OCR output
            if header_word not in text.upper():
                text += f"\n\n{table_md}"
    return text


# ── Step 3: Post-processing ───────────────────────────────────────────────────

def postprocess(text: str) -> str:
    """Clean up extraction artifacts."""
    from cloak.quality import postprocess as pp
    return pp.run(text)


# ── Step 4: Judge output with qwen2.5vl:7b ───────────────────────────────────

def judge_output(page_img: Image.Image, markdown: str) -> dict:
    """qwen2.5vl:7b evaluates extraction quality vs the original page image."""
    w, h = page_img.size
    if max(w, h) > 768:
        scale = 768 / max(w, h)
        page_img = page_img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)

    buf = io.BytesIO()
    page_img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    prompt = f"""You are judging the quality of a document extraction.
Compare the extracted markdown below against the original document image.

Evaluate on these 4 criteria (score each 0-10):
1. COMPLETENESS: Is all clinical content present? (sections, drug names, criteria, thresholds)
2. STRUCTURE: Are sections properly headed? Tables formatted? Lists correct?
3. ACCURACY: Are values, numbers, and medical terms correct? No hallucinations?
4. ORDER: Is content in the correct reading sequence?

Respond in this exact JSON format:
{{"completeness": N, "structure": N, "accuracy": N, "order": N, "overall": N,
  "missing": ["item1", "item2"],
  "issues": ["issue1", "issue2"],
  "summary": "one sentence overall assessment"}}

EXTRACTED MARKDOWN:
{markdown[:3000]}"""

    result_q: queue.Queue = queue.Queue()
    def _worker():
        try:
            resp = ollama.chat(
                model=JUDGE_MODEL,
                messages=[{"role": "user", "content": prompt, "images": [img_bytes]}],
                options={"temperature": 0.0, "num_ctx": 4096},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", str(exc)))

    threading.Thread(target=_worker, daemon=True).start()
    try:
        kind, val = result_q.get(timeout=120)
        if kind == "err":
            return {"error": val}
        # Parse JSON from response
        m = re.search(r'\{.*\}', val, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"raw": val[:500]}
    except queue.Empty:
        return {"error": "judge timeout"}


# ── Step 5: Compare vs Landing.ai ────────────────────────────────────────────

def compare_landing_ai(our_md: str, la_json_path: Path) -> dict:
    """Compare our markdown against Landing.ai's output."""
    la = json.loads(la_json_path.read_text(encoding="utf-8"))
    la_md = la.get("markdown", "")

    def words(t):
        return set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', t.lower()))

    o, r = words(our_md), words(la_md)
    recall    = round(len(o & r) / len(r) if r else 0, 2)
    precision = round(len(o & r) / len(o) if o else 0, 2)
    f1        = round(2 * recall * precision / (recall + precision + 1e-9), 2)

    # Count element types in Landing.ai
    la_types = {}
    for chunk in la.get("chunks", []):
        t = chunk.get("type", "?")
        la_types[t] = la_types.get(t, 0) + 1

    # Count our tables and headings
    our_tables  = len(re.findall(r'^\|.*\|.*\|', our_md, re.MULTILINE))
    our_heads   = len(re.findall(r'^#{1,3} \S', our_md, re.MULTILINE))
    la_tables   = la_types.get("table", 0)
    la_headings = sum(1 for c in la.get("chunks", [])
                      if c.get("type") == "text" and "##" in c.get("markdown", ""))

    return {
        "word_recall": recall, "word_precision": precision, "f1": f1,
        "our_tables": our_tables, "la_tables": la_tables,
        "our_headings": our_heads,
        "la_element_types": la_types,
    }


# ── Main extraction function ──────────────────────────────────────────────────

def extract(pdf_path: Path, landing_ai_json: Path | None = None,
            run_judge: bool = True) -> dict:
    """Full extraction pipeline. Returns result dict."""
    pdf_path = Path(pdf_path)
    name = pdf_path.stem
    out_path = OUT_DIR / f"{name}.md"
    result_path = OUT_DIR / f"{name}_result.json"
    t_start = time.monotonic()

    print(f"\n{'='*65}")
    print(f"Extracting: {name}")
    print(f"{'='*65}")

    # ── Step 1: Profile ───────────────────────────────────────────────────────
    print("  Step 1: Profiling...", end=" ", flush=True)
    t0 = time.monotonic()
    prof = pass1_profile(pdf_path)
    # Run Pass 2 VLM if tags present (for picture type classification)
    if prof.tags:
        prof = pass2_vlm(prof)
    print(f"{time.monotonic()-t0:.1f}s  strategy={prof.strategy}  "
          f"glm={prof.glm_chars}  tables={prof.pdf_tables}")

    # ── Step 2: Extract ───────────────────────────────────────────────────────
    print("  Step 2: Extracting...", end=" ", flush=True)
    t0 = time.monotonic()

    # 2a. GLM-OCR → HTML → clean markdown
    glm_plain = glm_html_to_markdown(prof.glm_text) if prof.glm_text else ""

    # 2b. If GLM-OCR failed or sparse, use pdfplumber text as fallback
    if len(glm_plain.strip()) < 200:
        with pdfplumber.open(pdf_path) as pdf:
            glm_plain = pdf.pages[0].extract_text() or ""

    # 2c. Add heading structure
    markdown = promote_headings(glm_plain)

    # 2d. Extract tables via camelot and inject
    tables = extract_tables_camelot(pdf_path, prof)
    if tables:
        markdown = inject_tables(markdown, tables, prof)
        print(f"\n     Tables extracted: {len(tables)}", end=" ")

    print(f"{time.monotonic()-t0:.1f}s  {len(markdown)} chars")

    # ── Step 3: Post-process ──────────────────────────────────────────────────
    print("  Step 3: Post-processing...", end=" ", flush=True)
    t0 = time.monotonic()
    markdown = postprocess(markdown)
    print(f"{time.monotonic()-t0:.1f}s  {len(markdown)} chars final")

    # Save output
    out_path.write_text(markdown, encoding="utf-8")
    print(f"  Output: {out_path}")

    result = {
        "name": name, "strategy": prof.strategy,
        "glm_chars": prof.glm_chars, "pdf_tables": prof.pdf_tables,
        "output_chars": len(markdown), "output_path": str(out_path),
        "total_s": 0,
    }

    # ── Step 4: Judge ─────────────────────────────────────────────────────────
    if run_judge:
        print(f"  Step 4: Judging with {JUDGE_MODEL}...", end=" ", flush=True)
        t0 = time.monotonic()
        pages = load_pages(pdf_path)
        judgment = judge_output(pages[0].image, markdown)
        result["judgment"] = judgment
        elapsed = round(time.monotonic()-t0, 1)
        if "overall" in judgment:
            print(f"{elapsed}s  overall={judgment['overall']}/10  "
                  f"completeness={judgment.get('completeness','?')}  "
                  f"accuracy={judgment.get('accuracy','?')}")
        else:
            print(f"{elapsed}s  {str(judgment)[:80]}")

    # ── Step 5: Compare vs Landing.ai ─────────────────────────────────────────
    if landing_ai_json and Path(landing_ai_json).exists():
        print("  Step 5: Comparing vs Landing.ai...", end=" ", flush=True)
        comp = compare_landing_ai(markdown, Path(landing_ai_json))
        result["landing_ai_comparison"] = comp
        print(f"recall={comp['word_recall']}  f1={comp['f1']}  "
              f"our_tables={comp['our_tables']}/la_tables={comp['la_tables']}")

    result["total_s"] = round(time.monotonic()-t_start, 1)
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print the markdown
    print(f"\n{'─'*65}")
    print("EXTRACTED MARKDOWN:")
    print('─'*65)
    for line in markdown.splitlines()[:60]:
        print(f"  {line}")
    if len(markdown.splitlines()) > 60:
        print(f"  ... ({len(markdown.splitlines())-60} more lines)")

    # Print judgment
    if "judgment" in result:
        j = result["judgment"]
        print(f"\n{'─'*65}")
        print("JUDGE SCORES (qwen2.5vl:7b):")
        print(f"  Overall:      {j.get('overall', '?')}/10")
        print(f"  Completeness: {j.get('completeness', '?')}/10")
        print(f"  Structure:    {j.get('structure', '?')}/10")
        print(f"  Accuracy:     {j.get('accuracy', '?')}/10")
        print(f"  Order:        {j.get('order', '?')}/10")
        if j.get("missing"):
            print(f"  Missing: {j['missing'][:3]}")
        if j.get("issues"):
            print(f"  Issues:  {j['issues'][:3]}")
        if j.get("summary"):
            print(f"  Summary: {j['summary']}")

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    no_judge = "--no-judge" in args
    run_batch = "--batch" in args
    args = [a for a in args if not a.startswith("--")]

    landing_ai = None
    la_idx = next((i for i, a in enumerate(args) if a == "--landing-ai"), -1)
    if la_idx >= 0 and la_idx + 1 < len(args):
        landing_ai = Path(args[la_idx + 1])
        args = args[:la_idx] + args[la_idx+2:]

    if run_batch:
        print(f"Running extraction on {len(TEST_DOCS)} test documents\n")
        all_results = []
        for pdf_str, la_str in TEST_DOCS:
            pdf_path = Path(pdf_str)
            if not pdf_path.exists():
                print(f"  SKIP (not found): {pdf_path.name}")
                continue
            la_path = Path(la_str) if la_str else None
            r = extract(pdf_path, la_path, run_judge=not no_judge)
            all_results.append(r)

        print(f"\n{'='*65}")
        print("BATCH SUMMARY")
        print(f"{'='*65}")
        print(f"{'Document':<35} {'Strategy':<14} {'Overall':>7} {'Recall':>7} {'Time':>6}")
        print("-" * 75)
        for r in all_results:
            j = r.get("judgment", {})
            comp = r.get("landing_ai_comparison", {})
            overall = j.get("overall", "—")
            recall = comp.get("word_recall", "—")
            print(f"  {r['name']:<33} {r['strategy']:<14} {str(overall):>7} {str(recall):>7} {r['total_s']:>5}s")

        summary_path = OUT_DIR / "batch_summary.json"
        summary_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nAll outputs: {OUT_DIR}/")
        print(f"Summary:     {summary_path}")

    elif args:
        pdf_path = Path(args[0])
        extract(pdf_path, landing_ai, run_judge=not no_judge)
    else:
        print("Usage:")
        print("  python scripts/extractor.py <pdf_path>")
        print("  python scripts/extractor.py <pdf> --landing-ai <json>")
        print("  python scripts/extractor.py --batch")
        print("  python scripts/extractor.py --batch --no-judge  (skip judge for speed)")


if __name__ == "__main__":
    main()
