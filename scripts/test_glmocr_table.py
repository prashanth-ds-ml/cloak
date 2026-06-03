"""Test GLM-OCR on the CHA2DS2-VASc table crop and compare vs pdfplumber/camelot/Landing.ai"""
import sys, io, re, json, queue, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from PIL import Image
import ollama, pdfplumber

from cloak.extraction.pdf_tools import load_pages
from cloak.config import MODEL_KEEP_ALIVE

PDF     = Path("data/samples/icmr_stw_full/cardiology/cardiology_af.pdf")
LA_JSON = Path("C:/Users/prash/Downloads/1768823343_cardiology_1-1.parse.json")

LA_TABLE = """| CHA2DS2-VASc | SCORE | HAS-BLED | SCORE |
| --- | --- | --- | --- |
| Congestive heart failure/LV dysfunction | 1 | Hypertension i.e. uncontrolled BP | 1 |
| Hypertension | 1 | Abnormal renal/liver function | 1 or 2 |
| Aged >= 75 years | 2 | Stroke | 1 |
| Diabetes mellitus | 1 | Bleeding tendency or predisposition | 1 |
| Stroke/TIA/TE | 2 | Labile INR | 1 |
| Vascular disease [prior MI, PAD or aortic plaque] | 1 | Age (e.g. >65) | 1 |
| Aged 65-74 years | 1 | Drugs (concomitant aspirin or NSAIDs or alcohol) | 1 |
| Maximum Score | 9 | | 9 |"""


def recall_precision(output, reference):
    def w(t): return set(re.findall(r'\b[a-zA-Z0-9]{2,}\b', t.lower()))
    o, r = w(output), w(reference)
    rec = round(len(o & r) / len(r) if r else 0, 2)
    pre = round(len(o & r) / len(o) if o else 0, 2)
    return rec, pre


def call_glm(image: Image.Image, prompt: str, timeout: float = 60.0) -> tuple[str, float]:
    # Resize to avoid GGML errors
    w, h = image.size
    if max(w, h) > 1024:
        scale = 1024 / max(w, h)
        image = image.resize((int(w*scale), int(h*scale)), Image.LANCZOS)

    if image.mode != "RGB":
        image = image.convert("RGB")

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    result_q: queue.Queue = queue.Queue()
    def _worker():
        try:
            resp = ollama.chat(
                model="glm-ocr",
                messages=[{"role": "user", "content": prompt, "images": [img_bytes]}],
                options={"num_ctx": 4096},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", str(exc)))

    t0 = time.monotonic()
    threading.Thread(target=_worker, daemon=True).start()
    try:
        kind, val = result_q.get(timeout=timeout)
        return (val if kind == "ok" else f"ERROR: {val}"), round(time.monotonic()-t0, 1)
    except queue.Empty:
        return "TIMEOUT", round(time.monotonic()-t0, 1)


def main():
    la = json.loads(LA_JSON.read_text(encoding="utf-8"))
    g  = la["grounding"]["6c338217-a67b-40a6-b658-45b0782fbaee"]["box"]

    # Load page and crop
    pages    = load_pages(PDF)
    page_img = pages[0].image
    W, H     = page_img.size
    crop = page_img.crop((
        int(g["left"]*W), int(g["top"]*H),
        int(g["right"]*W), int(g["bottom"]*H)
    ))
    print(f"Table crop: {crop.width}x{crop.height}px\n")

    # ── GLM-OCR with default prompt ──────────────────────────────────────────
    print("=" * 60)
    print("TEST: GLM-OCR — default extraction prompt")
    print("=" * 60)
    prompt1 = (
        "Extract all content from this document page into clean markdown.\n"
        "- Preserve reading order top to bottom, left to right.\n"
        "- Reproduce tables in markdown table format with | header | ... | separator rows.\n"
        "- Preserve all text exactly — do NOT summarise or paraphrase.\n"
        "- Output only the extracted markdown content, no preamble or commentary."
    )
    out1, t1 = call_glm(crop, prompt1)
    r1, p1 = recall_precision(out1, LA_TABLE)
    print(f"Time: {t1}s  recall={r1}  precision={p1}\n")
    print("Output:")
    for line in out1.splitlines():
        print(f"  {line}")

    # ── GLM-OCR with table-specific prompt ──────────────────────────────────
    print("\n" + "=" * 60)
    print("TEST: GLM-OCR — table-specific prompt")
    print("=" * 60)
    prompt2 = (
        "Extract this table as clean markdown.\n"
        "Rules:\n"
        "- Use | col1 | col2 | format with a | --- | --- | separator after the header row\n"
        "- Preserve all values exactly including numbers and text\n"
        "- Each condition should be its own row\n"
        "- Output only the markdown table, no preamble"
    )
    out2, t2 = call_glm(crop, prompt2)
    r2, p2 = recall_precision(out2, LA_TABLE)
    print(f"Time: {t2}s  recall={r2}  precision={p2}\n")
    print("Output:")
    for line in out2.splitlines():
        print(f"  {line}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY — CHA2DS2-VASc table extraction")
    print("=" * 60)
    print(f"  {'METHOD':<35} {'RECALL':>7} {'PREC':>6} {'TIME':>6}")
    print(f"  {'-'*55}")
    print(f"  {'granite3.2-vision':<35} {'0.09':>7} {'1.00':>6} {'10.5s':>6}  FAIL")
    print(f"  {'pdfplumber':<35} {'0.94':>7} {'0.92':>6} {'<1s':>6}  GOOD")
    print(f"  {'camelot stream':<35} {'0.98':>7} {'0.96':>6} {'<1s':>6}  BEST")
    print(f"  {'GLM-OCR (default prompt)':<35} {str(r1):>7} {str(p1):>6} {str(t1)+'s':>6}")
    print(f"  {'GLM-OCR (table prompt)':<35} {str(r2):>7} {str(p2):>6} {str(t2)+'s':>6}")
    print(f"  {'Landing.ai (reference)':<35} {'1.00':>7} {'1.00':>6} {'—':>6}  TARGET")

    print("\n" + "=" * 60)
    print("Landing.ai reference:")
    for line in LA_TABLE.splitlines():
        print(f"  {line}")


if __name__ == "__main__":
    main()
