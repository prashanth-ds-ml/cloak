"""
profiler_v2.py -- Two-pass profiler with VLM gap-filling and judge evaluation.

Design (docs/TWO_PASS_PROFILER.md):
  Pass 1:  current profiler (pdfplumber + docling + GLM-OCR) → tags uncertain cases
  Pass 2:  qwen3-vl:4b fills tagged profiling gaps (column count, picture type)
  Judge:   qwen2.5vl:7b evaluates the complete profile
  Output:  annotated PNG + structured report per document

Landing.ai comparison:
  When a Landing.ai JSON is provided alongside a PDF, the report includes
  a side-by-side comparison of element types, bboxes, and column structure.

Usage:
  python scripts/profiler_v2.py data/samples/icmr_stw_full/cardiology/cardiology_af.pdf
  python scripts/profiler_v2.py data/samples/icmr_stw_full/cardiology/cardiology_af.pdf --landing-ai "C:/Users/prash/Downloads/1768823343_cardiology_1-1.parse.json"
  python scripts/profiler_v2.py data/samples/icmr_stw_full/ --docs cardiology_af,paediatrics_dengue,neurology_stroke
"""
from __future__ import annotations
import sys, io, re, json, time, queue, threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from collections import Counter
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pdfplumber
from PIL import Image, ImageDraw, ImageFont
import ollama

from cloak.profiling.doc_profiler import run_docling_pass
from cloak.extraction.ocr_tools import _ocr_page_glm, is_glm_ocr_available, OCRError
from cloak.extraction.pdf_tools import load_pages
from cloak.config import MODEL_KEEP_ALIVE, VISION_TIMEOUT

# ── Output directory ──────────────────────────────────────────────────────────
VALIDATE_DIR = Path("data/validation")
VALIDATE_DIR.mkdir(parents=True, exist_ok=True)

# ── Models ────────────────────────────────────────────────────────────────────
PASS2_MODEL  = "qwen3-vl:4b"     # fast, simple classification questions
JUDGE_MODEL  = "qwen2.5vl:7b"    # higher quality, document-trained evaluator


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class PicSection:
    index: int
    y0_pct: float
    y1_pct: float
    x0_pct: float
    x1_pct: float
    area_pct: float
    pdf_text: str        # pdfplumber text inside bbox
    pdf_words: int
    vlm_type: str = ""   # filled by Pass 2: flowchart/table/diagram/logo
    judge_type: str = "" # filled by Judge

@dataclass
class ColumnInfo:
    count: int
    boundaries_pct: list[float]
    method: str
    vlm_count: int = 0   # Pass 2 visual confirmation
    judge_count: int = 0 # Judge visual confirmation

@dataclass
class DocumentProfile:
    name: str
    path: str
    page_count: int
    width_pts: float
    height_pts: float
    # pdfplumber
    pdf_chars: int
    pdf_tables: int
    # docling
    docling_coverage_pct: float
    docling_sections: list[dict]     # [{x_pct, y_pct, text}]
    docling_label_counts: dict
    picture_sections: list[PicSection]
    # columns
    columns: ColumnInfo
    # GLM-OCR
    glm_chars: int
    glm_time_s: float
    glm_text: str
    glm_sections: list[str]
    glm_hallucination: bool
    # Strategy
    strategy: str
    tags: list[str]       # which VLM passes are needed
    # Judge (filled later)
    judge_strategy_correct: str = ""
    judge_notes: str = ""


# ── helpers shared with profiler.py ──────────────────────────────────────────

def glm_to_plain_text(glm_output: str) -> str:
    if not glm_output:
        return ""
    if "<table" in glm_output or "<td" in glm_output:
        text = re.sub(r'<br\s*/?>', '\n', glm_output, flags=re.IGNORECASE)
        text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</t[dh]>', ' | ', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&amp;', '&').replace('&gt;', '>').replace('&lt;', '<')
        text = text.replace('&nbsp;', ' ').replace('&#39;', "'")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return '\n'.join(lines)
    return glm_output


def extract_sections(text: str) -> list[str]:
    plain = glm_to_plain_text(text)
    sections = []
    for line in plain.splitlines():
        s = line.strip().strip('| ').strip()
        if (s and len(s) > 3 and s == s.upper()
                and not s.isdigit() and not s.startswith('|')
                and re.search(r'[A-Z]{3,}', s)
                and not re.fullmatch(r'[\d\s|.,:;()%/-]+', s)):
            sections.append(s)
    seen, unique = set(), []
    for s in sections:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def is_hallucination(sections: list[str]) -> bool:
    if len(sections) < 10:
        return False
    words = []
    for s in sections:
        m = re.match(r'^([A-Z]+)\s+\d+', s.strip())
        if m:
            words.append(m.group(1))
    if not words:
        return False
    most_common_word, count = Counter(words).most_common(1)[0]
    return count / len(sections) > 0.45


def detect_columns(sections: list[dict], page_width: float) -> ColumnInfo:
    def is_stw_title(t: str) -> bool:
        t = t.lower()
        return "standard treatment workflow" in t or bool(re.match(r'^icd-', t))

    anchored = [h for h in sections
                if 5.0 < h["x_pct"] < 85.0 and not is_stw_title(h["text"])]
    if len(anchored) < 2:
        return ColumnInfo(1, [], "too few headers")

    xs = sorted(h["x_pct"] for h in anchored)
    gaps = []
    for i in range(len(xs) - 1):
        if xs[i+1] - xs[i] > 12.0:
            gaps.append(round((xs[i] + xs[i+1]) / 2, 1))
    if not gaps:
        for i in range(len(xs) - 1):
            if xs[i+1] - xs[i] > 10.0:
                gaps.append(round((xs[i] + xs[i+1]) / 2, 1))

    deduped = []
    for b in sorted(gaps):
        if not deduped or b - deduped[-1] > 5.0:
            deduped.append(b)
    return ColumnInfo(len(deduped) + 1, deduped,
                      f"header clustering ({len(anchored)} anchored)")


def get_bbox_text(page, bbox_norm, W, H) -> str:
    try:
        l, t, r, b = bbox_norm
        crop = page.within_bbox((max(0, l*W-2), max(0, t*H-2),
                                  min(W, r*W+2), min(H, b*H+2)))
        return (crop.extract_text() or "").strip()
    except Exception:
        return ""


# ── PASS 1 — fast profiling ───────────────────────────────────────────────────

def pass1_profile(pdf_path: Path) -> DocumentProfile:
    """Run pdfplumber + docling + GLM-OCR. Tag uncertain cases."""
    pdf_path = Path(pdf_path)
    name = pdf_path.stem

    # pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        W, H = page.width, page.height
        page_count = len(pdf.pages)
        raw_text = page.extract_text() or ""
        tables = page.find_tables()

    # docling
    sections, pics, label_counts, docling_chars = [], [], {}, 0
    element_map = run_docling_pass(pdf_path)
    if element_map:
        elems = element_map.get(0, [])
        label_counts = dict(Counter(e.label for e in elems))
        text_elems = [e for e in elems if e.label in ("text","section_header","list_item","paragraph")]
        docling_chars = sum(len(e.text) for e in text_elems)

        for e in sorted(elems, key=lambda x: x.bbox_norm[1]):
            if e.label in ("section_header", "title"):
                sections.append({"text": e.text[:60],
                                  "x_pct": round(e.bbox_norm[0]*100, 1),
                                  "y_pct": round(e.bbox_norm[1]*100, 1)})

        with pdfplumber.open(pdf_path) as pdf:
            pg = pdf.pages[0]
            for i, e in enumerate(elems):
                if e.label != "picture":
                    continue
                l, t, r, b = e.bbox_norm
                area = (r-l)*(b-t)*100
                if area < 3.0:
                    continue
                txt = get_bbox_text(pg, e.bbox_norm, W, H)
                pics.append(PicSection(
                    index=i, y0_pct=round(t*100,1), y1_pct=round(b*100,1),
                    x0_pct=round(l*100,1), x1_pct=round(r*100,1),
                    area_pct=round(area,1), pdf_text=txt,
                    pdf_words=len(txt.split()) if txt else 0,
                ))

    doc_cov = round(docling_chars / len(raw_text) * 100, 1) if raw_text else 0
    columns = detect_columns(sections, W)

    # GLM-OCR
    glm_text, glm_time, glm_hall = "", 0.0, False
    if is_glm_ocr_available():
        try:
            pages = load_pages(pdf_path)
            if pages[0].image is not None:
                t0 = time.monotonic()
                glm_text = _ocr_page_glm(pages[0].image)
                glm_time = round(time.monotonic() - t0, 1)
        except OCRError:
            pass
        except Exception:
            pass

    raw_secs = extract_sections(glm_text) if glm_text else []
    if is_hallucination(raw_secs):
        glm_hall = True
        glm_secs = []
    else:
        glm_secs = raw_secs

    # Strategy
    if doc_cov < 30:
        strategy = "poster_mode"
    elif doc_cov < 60:
        strategy = "hybrid"
    else:
        strategy = "text_mode"

    # Tags
    tags = []
    if columns.count == 1 and len([h for h in sections if 5 < h["x_pct"] < 85]) < 4:
        tags.append("needs_vlm_columns")
    large_pics = [p for p in pics if p.area_pct > 10]
    if large_pics:
        tags.append("needs_vlm_picture_type")
    if not glm_text and doc_cov < 40 and sum(p.pdf_words for p in pics) > 200:
        tags.append("needs_vlm_ordering")

    return DocumentProfile(
        name=name, path=str(pdf_path), page_count=page_count,
        width_pts=round(W), height_pts=round(H),
        pdf_chars=len(raw_text), pdf_tables=len(tables),
        docling_coverage_pct=doc_cov, docling_sections=sections,
        docling_label_counts=label_counts, picture_sections=pics,
        columns=columns, glm_chars=len(glm_text), glm_time_s=glm_time,
        glm_text=glm_text, glm_sections=glm_secs,
        glm_hallucination=glm_hall, strategy=strategy, tags=tags,
    )


# ── PASS 2 — targeted VLM profiling ──────────────────────────────────────────

def _vlm_call(image: Image.Image, prompt: str, model: str,
              max_px: int = 512, timeout: float = 60.0) -> str:
    """Single VLM call with timeout. Returns response text or empty string."""
    w, h = image.size
    long_edge = max(w, h)
    if long_edge > max_px:
        scale = max_px / long_edge
        image = image.resize((int(w*scale), int(h*scale)), Image.LANCZOS)

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    result_q: queue.Queue = queue.Queue()
    def _worker():
        try:
            resp = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt, "images": [img_bytes]}],
                options={"temperature": 0.0, "num_ctx": 2048},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", exc))
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=timeout)
        return value if kind == "ok" else ""
    except queue.Empty:
        return ""


def pass2_vlm(prof: DocumentProfile) -> DocumentProfile:
    """Fill tagged profiling gaps using qwen3-vl:4b."""
    if not prof.tags:
        return prof

    pages = load_pages(Path(prof.path))
    page_img = pages[0].image if pages else None
    if page_img is None:
        return prof

    # Column count
    if "needs_vlm_columns" in prof.tags:
        prompt = (
            "How many main content columns does this medical document page have? "
            "Count only the main body columns, not headers or footers at the very top/bottom. "
            "Answer with a single digit only: 1, 2, 3, 4, or 5."
        )
        ans = _vlm_call(page_img, prompt, PASS2_MODEL, max_px=512, timeout=30)
        m = re.search(r'\b([1-5])\b', ans)
        if m:
            prof.columns.vlm_count = int(m.group(1))

    # Picture section types
    if "needs_vlm_picture_type" in prof.tags:
        W, H = page_img.size
        for pic in prof.picture_sections:
            if pic.area_pct < 10:
                continue
            # Crop the picture section
            x0 = int(pic.x0_pct / 100 * W)
            y0 = int(pic.y0_pct / 100 * H)
            x1 = int(pic.x1_pct / 100 * W)
            y1 = int(pic.y1_pct / 100 * H)
            crop = page_img.crop((max(0,x0), max(0,y0), min(W,x1), min(H,y1)))
            if crop.width < 20 or crop.height < 20:
                continue
            prompt = (
                "What type of content is shown in this image from a medical document? "
                "Answer with exactly one word from this list: flowchart, table, diagram, logo."
            )
            ans = _vlm_call(crop, prompt, PASS2_MODEL, max_px=512, timeout=30)
            ans_lower = ans.lower()
            if "flowchart" in ans_lower or "flow" in ans_lower:
                pic.vlm_type = "flowchart"
            elif "table" in ans_lower:
                pic.vlm_type = "table"
            elif "diagram" in ans_lower:
                pic.vlm_type = "diagram"
            elif "logo" in ans_lower:
                pic.vlm_type = "logo"
            else:
                pic.vlm_type = ans.split()[0].lower() if ans else "unknown"

    return prof


# ── JUDGE — evaluate the complete profile ────────────────────────────────────

def judge_profile(prof: DocumentProfile) -> DocumentProfile:
    """Evaluate profile correctness using qwen2.5vl:7b."""
    pages = load_pages(Path(prof.path))
    page_img = pages[0].image if pages else None
    if page_img is None:
        return prof

    W, H = page_img.size

    # Column count judgement
    col_claim = prof.columns.vlm_count if prof.columns.vlm_count > 0 else prof.columns.count
    prompt = (
        f"Look at this medical document page image carefully. "
        f"A profiling tool says it has {col_claim} main content column(s). "
        f"Looking at the layout, is that correct? "
        f"Count the main columns in the body of the document (not top headers or bottom footers). "
        f"Answer in this exact format: "
        f"CORRECT: [number] or WRONG: [actual number]"
    )
    ans = _vlm_call(page_img, prompt, JUDGE_MODEL, max_px=768, timeout=60)
    prof.columns.judge_count = col_claim  # default
    m = re.search(r'(CORRECT|WRONG)\s*:\s*(\d+)', ans.upper())
    if m:
        verdict = m.group(1)
        actual = int(m.group(2))
        prof.columns.judge_count = actual

    # Picture types judgement
    for pic in prof.picture_sections:
        if pic.area_pct < 10:
            continue
        x0 = int(pic.x0_pct / 100 * W)
        y0 = int(pic.y0_pct / 100 * H)
        x1 = int(pic.x1_pct / 100 * W)
        y1 = int(pic.y1_pct / 100 * H)
        crop = page_img.crop((max(0,x0), max(0,y0), min(W,x1), min(H,y1)))
        if crop.width < 20 or crop.height < 20:
            continue
        claim = pic.vlm_type if pic.vlm_type else "unknown"
        prompt = (
            f"Look at this image region from a medical document. "
            f"The profiler classified it as: {claim}. "
            f"Options are: flowchart (boxes with arrows/decisions), "
            f"table (rows and columns of data), "
            f"diagram (anatomical/technical illustration), "
            f"logo (institutional emblem/badge). "
            f"Answer: CORRECT or WRONG: [correct type]"
        )
        ans = _vlm_call(crop, prompt, JUDGE_MODEL, max_px=512, timeout=45)
        m = re.search(r'(CORRECT|WRONG)\s*:?\s*(\w+)?', ans.upper())
        if m:
            verdict = m.group(1)
            if verdict == "WRONG" and m.group(2):
                pic.judge_type = m.group(2).lower()
            else:
                pic.judge_type = claim

    # Strategy judgement
    prompt = (
        f"Look at this medical document page. "
        f"The profiler routed it as: {prof.strategy}. "
        f"Definitions: "
        f"text_mode = mostly readable structured text with clear sections. "
        f"hybrid = mix of structured text AND complex visual sections (tables/flowcharts). "
        f"poster_mode = mostly complex visual flowchart/poster layout, very little plain text. "
        f"Is the routing CORRECT or WRONG? If wrong, what should it be? "
        f"Answer: CORRECT or WRONG: [correct_strategy]"
    )
    ans = _vlm_call(page_img, prompt, JUDGE_MODEL, max_px=768, timeout=60)
    prof.judge_strategy_correct = ans.strip()[:120]
    prof.judge_notes = ans.strip()

    return prof


# ── Landing.ai comparison ─────────────────────────────────────────────────────

def load_landing_ai(json_path: Path) -> dict | None:
    try:
        with open(json_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def compare_landing_ai(prof: DocumentProfile, la_data: dict) -> dict:
    """Compare our profile against Landing.ai ground truth."""
    chunks = la_data.get("chunks", [])
    grounding = la_data.get("grounding", {})

    type_counts = Counter(c["type"] for c in chunks)

    # Landing.ai column structure — infer from text chunk X positions
    text_chunks = [c for c in chunks if c["type"] == "text"]
    la_x_positions = sorted(set(
        round(c["grounding"]["box"]["left"] * 100, 1)
        for c in text_chunks
        if c.get("grounding", {}).get("box")
    ))

    # Landing.ai table bboxes
    table_chunks = [c for c in chunks if c["type"] == "table"]
    la_tables = [{"y0_pct": round(c["grounding"]["box"]["top"]*100, 1),
                  "y1_pct": round(c["grounding"]["box"]["bottom"]*100, 1),
                  "x0_pct": round(c["grounding"]["box"]["left"]*100, 1),
                  "x1_pct": round(c["grounding"]["box"]["right"]*100, 1)}
                 for c in table_chunks if c.get("grounding")]

    # Landing.ai figure/flowchart bboxes
    figure_chunks = [c for c in chunks if c["type"] == "figure"]
    la_figures = [{"y0_pct": round(c["grounding"]["box"]["top"]*100, 1),
                   "y1_pct": round(c["grounding"]["box"]["bottom"]*100, 1)}
                  for c in figure_chunks if c.get("grounding")]

    # Confidence stats
    conf_values = [g.get("confidence") for g in grounding.values()
                   if g.get("confidence") is not None]
    avg_confidence = round(sum(conf_values)/len(conf_values), 3) if conf_values else None

    # Our vs Landing.ai picture section comparison
    our_pics = [(round(p.y0_pct,0), round(p.y1_pct,0)) for p in prof.picture_sections]
    la_pic_ranges = [(round(f["y0_pct"],0), round(f["y1_pct"],0)) for f in la_figures + la_tables]

    return {
        "la_element_types": dict(type_counts),
        "la_table_count": len(table_chunks),
        "la_figure_count": len(figure_chunks),
        "la_logo_count": type_counts.get("logo", 0),
        "la_tables": la_tables,
        "la_figures": la_figures,
        "la_avg_confidence": avg_confidence,
        "la_x_positions": la_x_positions[:20],
        "our_picture_sections": [(round(p.y0_pct,0), round(p.y1_pct,0), p.area_pct)
                                   for p in prof.picture_sections],
        "our_columns": prof.columns.count,
        "our_col_boundaries": prof.columns.boundaries_pct,
    }


# ── Annotated image generation ────────────────────────────────────────────────

def annotate_page(page_img: Image.Image, prof: DocumentProfile,
                  la_data: dict | None = None) -> Image.Image:
    """Draw bboxes, column lines, and labels on the page image."""
    img = page_img.copy().convert("RGB")
    draw = ImageDraw.Draw(img)
    W, H = img.size

    # Scale down for readability (max 1024px tall)
    if H > 1024:
        scale = 1024 / H
        img = img.resize((int(W*scale), 1024), Image.LANCZOS)
        draw = ImageDraw.Draw(img)
        W, H = img.size

    # Our column boundaries — green dashed vertical lines
    for b in prof.columns.boundaries_pct:
        x = int(b / 100 * W)
        for y in range(0, H, 20):
            draw.line([(x, y), (x, min(y+12, H))], fill=(0, 200, 0), width=3)
    # VLM column count if different
    if prof.columns.vlm_count > 0 and prof.columns.vlm_count != prof.columns.count:
        draw.text((5, H-30), f"VLM cols: {prof.columns.vlm_count}", fill=(255,200,0))

    # Our picture sections — red boxes
    for i, pic in enumerate(prof.picture_sections):
        if pic.area_pct < 3:
            continue
        x0, y0 = int(pic.x0_pct/100*W), int(pic.y0_pct/100*H)
        x1, y1 = int(pic.x1_pct/100*W), int(pic.y1_pct/100*H)
        draw.rectangle([x0, y0, x1, y1], outline=(255, 50, 50), width=4)
        label = f"pic{i+1}: {pic.vlm_type or '?'} ({pic.area_pct:.0f}%)"
        draw.text((x0+4, y0+4), label, fill=(255, 50, 50))

    # Landing.ai tables — blue boxes (ground truth)
    if la_data:
        la_comp = compare_landing_ai(prof, la_data)
        for t in la_comp["la_tables"]:
            x0 = int(t["x0_pct"]/100*W)
            y0 = int(t["y0_pct"]/100*H)
            x1 = int(t["x1_pct"]/100*W)
            y1 = int(t["y1_pct"]/100*H)
            draw.rectangle([x0, y0, x1, y1], outline=(50, 100, 255), width=3)
            draw.text((x0+4, y0+4), "LA:table", fill=(50, 100, 255))
        for f in la_comp["la_figures"]:
            y0 = int(f["y0_pct"]/100*H)
            y1 = int(f["y1_pct"]/100*H)
            draw.rectangle([5, y0, W-5, y1], outline=(100, 50, 255), width=2)
            draw.text((8, y0+4), "LA:figure", fill=(100, 50, 255))

    # Header info
    draw.rectangle([0, 0, W, 48], fill=(0, 0, 0, 180))
    header = (f"{prof.name}  |  cov={prof.docling_coverage_pct}%  "
              f"cols={prof.columns.count}  strategy={prof.strategy}  "
              f"glm={'ok' if prof.glm_chars else 'fail'}")
    draw.text((6, 6), header[:100], fill=(255, 255, 255))

    # Legend
    legend_y = H - 80
    draw.rectangle([0, legend_y, W, H], fill=(0, 0, 0, 160))
    draw.text((6, legend_y+4), "GREEN dashes = our columns  RED = our pic sections  BLUE = Landing.ai tables  PURPLE = Landing.ai figures",
              fill=(200, 200, 200))
    if prof.tags:
        draw.text((6, legend_y+22), f"TAGS: {', '.join(prof.tags)}", fill=(255, 220, 100))
    col_str = f"Cols: ours={prof.columns.count}"
    if prof.columns.vlm_count:
        col_str += f"  VLM={prof.columns.vlm_count}"
    if prof.columns.judge_count:
        col_str += f"  Judge={prof.columns.judge_count}"
    draw.text((6, legend_y+40), col_str, fill=(100, 255, 100))

    return img


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report(prof: DocumentProfile, la_data: dict | None = None) -> str:
    lines = []
    def L(s=""): lines.append(s)

    L("=" * 72)
    L(f"VALIDATION REPORT: {prof.name}")
    L("=" * 72)

    L(f"\n[PAGE]  {prof.width_pts}x{prof.height_pts}pts  ({prof.page_count}pp)")

    L(f"\n[PASS 1 — PROFILER]")
    L(f"  docling coverage: {prof.docling_coverage_pct}%")
    L(f"  pdf tables:       {prof.pdf_tables}")
    L(f"  strategy:         {prof.strategy}")
    L(f"  columns:          {prof.columns.count}  boundaries={prof.columns.boundaries_pct}")
    L(f"                    method: {prof.columns.method}")
    L(f"  GLM-OCR:          {prof.glm_chars} chars in {prof.glm_time_s}s")
    if prof.glm_hallucination:
        L(f"  GLM hallucination: DETECTED — sections discarded")
    elif prof.glm_sections:
        L(f"  GLM sections ({len(prof.glm_sections)}): {', '.join(prof.glm_sections[:5])}")
        if len(prof.glm_sections) > 5:
            L(f"                    ... +{len(prof.glm_sections)-5} more")
    L(f"  tags:             {prof.tags or ['none']}")

    L(f"\n  Picture sections ({len(prof.picture_sections)}):")
    for p in prof.picture_sections:
        if p.area_pct < 3:
            continue
        L(f"    [{p.index}] y={p.y0_pct:.0f}-{p.y1_pct:.0f}%  x={p.x0_pct:.0f}-{p.x1_pct:.0f}%  "
          f"area={p.area_pct:.0f}%  {p.pdf_words} words in bbox")
        if p.pdf_text:
            preview = p.pdf_text[:150].replace('\n', ' | ')
            L(f"        pdf text: {preview}")

    if any(p.vlm_type for p in prof.picture_sections):
        L(f"\n[PASS 2 — VLM (qwen3-vl:4b)]")
        if prof.columns.vlm_count:
            L(f"  columns: {prof.columns.vlm_count}  "
              f"{'AGREES' if prof.columns.vlm_count == prof.columns.count else 'DIFFERS from Pass 1'}")
        for p in prof.picture_sections:
            if p.vlm_type:
                L(f"  pic[{p.index}] type: {p.vlm_type}")

    if prof.columns.judge_count or prof.judge_strategy_correct or any(p.judge_type for p in prof.picture_sections):
        L(f"\n[JUDGE — qwen2.5vl:7b]")
        if prof.columns.judge_count:
            match = "AGREES" if prof.columns.judge_count == prof.columns.count else "DIFFERS"
            L(f"  columns: {prof.columns.judge_count}  {match}")
        for p in prof.picture_sections:
            if p.judge_type:
                match = "AGREES" if p.judge_type == p.vlm_type else "DIFFERS"
                L(f"  pic[{p.index}] type: {p.judge_type}  {match}")
        if prof.judge_strategy_correct:
            L(f"  strategy: {prof.judge_strategy_correct[:80]}")

    if la_data:
        la = compare_landing_ai(prof, la_data)
        L(f"\n[LANDING.AI COMPARISON — ground truth]")
        L(f"  LA element types: {la['la_element_types']}")
        L(f"  LA tables ({la['la_table_count']}):")
        for t in la["la_tables"]:
            L(f"    y={t['y0_pct']:.0f}-{t['y1_pct']:.0f}%  x={t['x0_pct']:.0f}-{t['x1_pct']:.0f}%")
        L(f"  LA figures ({la['la_figure_count']}):")
        for f in la["la_figures"]:
            L(f"    y={f['y0_pct']:.0f}-{f['y1_pct']:.0f}%")
        L(f"  LA avg confidence: {la['la_avg_confidence']}")
        L(f"\n  Our picture sections: {la['our_picture_sections']}")
        L(f"  Our columns: {la['our_columns']}  boundaries={la['our_col_boundaries']}")
        L(f"  LA inferred X positions: {la['la_x_positions'][:8]}")

    L(f"\n[YOUR VERDICT]")
    L(f"  Column count you see:         ___")
    L(f"  Picture section types:        ___  ___  (flowchart/table/diagram/logo)")
    L(f"  Strategy correct?             yes / no / partially")
    L(f"  Notes: _________________________________")
    L(f"")
    L(f"  VERDICT: [ ] Full agreement   [ ] Minor error   [ ] Major error")
    L("=" * 72)
    return "\n".join(lines)


# ── Main orchestration ────────────────────────────────────────────────────────

def validate_pdf(pdf_path: Path, landing_ai_json: Path | None = None,
                 run_pass2: bool = True, run_judge: bool = True) -> None:
    name = pdf_path.stem
    out_dir = VALIDATE_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Validating: {name}")
    print(f"{'='*60}")

    # Pass 1
    print("  Pass 1 (profiling)...", end=" ", flush=True)
    t0 = time.monotonic()
    prof = pass1_profile(pdf_path)
    print(f"{time.monotonic()-t0:.1f}s  cov={prof.docling_coverage_pct}%  "
          f"cols={prof.columns.count}  glm={prof.glm_chars}  tags={prof.tags}")

    # Pass 2
    if run_pass2 and prof.tags:
        print(f"  Pass 2 (qwen3-vl:4b)...", end=" ", flush=True)
        t0 = time.monotonic()
        prof = pass2_vlm(prof)
        print(f"{time.monotonic()-t0:.1f}s")
    else:
        print(f"  Pass 2: skipped (no tags)")

    # Judge
    if run_judge:
        print(f"  Judge (qwen2.5vl:7b)...", end=" ", flush=True)
        t0 = time.monotonic()
        prof = judge_profile(prof)
        print(f"{time.monotonic()-t0:.1f}s")
    else:
        print(f"  Judge: skipped")

    # Landing.ai
    la_data = load_landing_ai(landing_ai_json) if landing_ai_json else None
    if la_data:
        print(f"  Landing.ai: {len(la_data.get('chunks', []))} chunks loaded ✓")

    # Generate annotated image
    pages = load_pages(pdf_path)
    if pages and pages[0].image:
        img = annotate_page(pages[0].image, prof, la_data)
        img_path = out_dir / "annotated.png"
        img.save(str(img_path))
        print(f"  Annotated PNG → {img_path}")

    # Generate report
    report = generate_report(prof, la_data)
    report_path = out_dir / "report.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"  Report       → {report_path}")

    # Save structured JSON
    prof_dict = {
        "name": prof.name, "path": prof.path,
        "page_count": prof.page_count,
        "docling_coverage_pct": prof.docling_coverage_pct,
        "columns": {"count": prof.columns.count,
                    "boundaries": prof.columns.boundaries_pct,
                    "vlm_count": prof.columns.vlm_count,
                    "judge_count": prof.columns.judge_count,
                    "method": prof.columns.method},
        "strategy": prof.strategy,
        "glm_chars": prof.glm_chars,
        "glm_hallucination": prof.glm_hallucination,
        "glm_sections": prof.glm_sections,
        "tags": prof.tags,
        "picture_sections": [
            {"index": p.index, "y0": p.y0_pct, "y1": p.y1_pct,
             "area": p.area_pct, "pdf_words": p.pdf_words,
             "vlm_type": p.vlm_type, "judge_type": p.judge_type}
            for p in prof.picture_sections if p.area_pct >= 3
        ],
        "judge_strategy": prof.judge_strategy_correct,
    }
    if la_data:
        prof_dict["landing_ai_comparison"] = compare_landing_ai(prof, la_data)

    json_path = out_dir / "profile.json"
    json_path.write_text(json.dumps(prof_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Profile JSON → {json_path}")
    print(f"\n  {'─'*50}")
    print(report)


# ── CLI ───────────────────────────────────────────────────────────────────────

# The 20 selected validation documents
VALIDATION_DOCS = [
    "cardiology_af", "paediatrics_dengue", "neurology_stroke",
    "cardiology_stemi", "neurology_headache", "cardiology_nstemi",
    "neonatology_sepsis", "neurology_acute_paralysis", "psychiatry_depression",
    "neurology_neuroinfections", "ortho_low_back_pain", "cardiology_bradyarrhythmia",
    "neurology_epilepsy", "cardiology_heart_failure", "psychiatry_psychosis",
    "ortho_tibial_plateau", "tb_abdominal", "urology_male_infertility",
    "paediatrics_diarrhea", "oncology_breast",
]

def find_pdf(name: str, base_dir: Path) -> Path | None:
    for p in base_dir.rglob(f"{name}.pdf"):
        return p
    return None


def main():
    args = sys.argv[1:]

    # Parse flags
    no_pass2 = "--no-pass2" in args
    no_judge = "--no-judge" in args
    args = [a for a in args if not a.startswith("--no-")]

    landing_ai_json = None
    la_idx = next((i for i, a in enumerate(args) if a == "--landing-ai"), -1)
    if la_idx >= 0 and la_idx + 1 < len(args):
        landing_ai_json = Path(args[la_idx + 1])
        args = args[:la_idx] + args[la_idx+2:]

    doc_filter = None
    docs_idx = next((i for i, a in enumerate(args) if a == "--docs"), -1)
    if docs_idx >= 0 and docs_idx + 1 < len(args):
        doc_filter = args[docs_idx + 1].split(",")
        args = args[:docs_idx] + args[docs_idx+2:]

    target = Path(args[0]) if args else Path("data/samples/icmr_stw_full")

    if target.is_file():
        validate_pdf(target, landing_ai_json,
                     run_pass2=not no_pass2, run_judge=not no_judge)
    else:
        # Find the validation docs
        docs_to_run = doc_filter if doc_filter else VALIDATION_DOCS
        found, missing = [], []
        for name in docs_to_run:
            p = find_pdf(name, target)
            if p:
                found.append(p)
            else:
                missing.append(name)

        if missing:
            print(f"Not found: {missing}")
        print(f"Running validation on {len(found)} documents → {VALIDATE_DIR}/\n")

        for pdf_path in found:
            # Check if Landing.ai JSON matches this specific doc
            la = landing_ai_json if landing_ai_json and pdf_path.stem in str(landing_ai_json) else None
            validate_pdf(pdf_path, la,
                         run_pass2=not no_pass2, run_judge=not no_judge)

        print(f"\n{'='*60}")
        print(f"All reports saved to: {VALIDATE_DIR}/")
        print(f"Each doc has: annotated.png  report.txt  profile.json")


if __name__ == "__main__":
    main()
