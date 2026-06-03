"""
test_flowchart.py -- Test granite3.2-vision on the cardiology_af management algorithm flowchart.

Crops the exact flowchart region (y=74-92% from Landing.ai JSON) and sends it
to granite3.2-vision with a structured flowchart transcription prompt.
Compares output against Landing.ai's known correct output.

Usage:
  python scripts/test_flowchart.py
  python scripts/test_flowchart.py --model granite3.2-vision
  python scripts/test_flowchart.py --model minicpm-v
"""
from __future__ import annotations
import sys, io, time, queue, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from PIL import Image
import ollama

from cloak.extraction.pdf_tools import load_pages
from cloak.config import MODEL_KEEP_ALIVE

# ── Landing.ai ground truth for comparison ────────────────────────────────────
LANDING_AI_OUTPUT = """MANAGEMENT ALGORITHM
Sign/symptoms suggestive of AF
Confirm by 12 channel rhythm strip
Hemodynamic Instability

NSR required?
  Yes -> DC version
  No -> Very rapid HR >130/min?
    No -> Symptomatic?
      Yes -> Consider DC version
      No -> Clinical Follow-up
    Yes -> CHF?
      Yes -> HR control Aim <110/min?
        Yes -> Careful BB/Dig Amiodarone
        No -> BB or Ca Block or combine
      No -> HR control Aim <110/min?
        Yes -> Careful BB/Dig Amiodarone
        No -> BB or Ca Block or combine

Pharmacological cardio version
Success?
  No -> DC version
  Yes -> Continue

Anti-coagulants in all Except
• Limited period with reversible etiology
• Score <2"""

# ── Flowchart transcription prompt ───────────────────────────────────────────
FLOWCHART_PROMPT = """\
This image shows a clinical decision flowchart from a medical guideline for Atrial Fibrillation management.

Transcribe it as a decision tree following these exact rules:
1. Start from the entry point at the top of the flowchart
2. For each decision node (a question), write the question exactly as shown
3. For each branch, show the path using:
     Yes -> [next step or action]
     No -> [next step or action]
4. Use indentation (2 spaces per level) to show hierarchy depth
5. Include every action box and endpoint exactly as written
6. Follow arrows carefully — trace every path to its end
7. Do NOT describe the flowchart — transcribe its content verbatim

Output format:
[Entry point text]
  [Question?]
    Yes -> [Action or next question]
      [Sub-question?]
        Yes -> [Action]
        No -> [Action]
    No -> [Action]"""

# ── Also test the second large picture section (y=44-73%, heart rate control) ─
HEART_RATE_PROMPT = """\
This image shows clinical management tables from an Atrial Fibrillation treatment guideline.

Extract ALL content from this image as structured markdown:
1. For tables: use markdown table format with | headers | and | --- | separator rows
2. For section headers: use ## heading format
3. For lists: use bullet points
4. Preserve all clinical values, drug names, and thresholds exactly
5. Do NOT describe — extract verbatim

Output only the extracted content, no preamble."""


def call_model(model: str, image: Image.Image, prompt: str,
               max_px: int = 1024, timeout: float = 120.0) -> tuple[str, float]:
    """Call a VLM and return (response_text, elapsed_seconds)."""
    # Resize
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
                options={"temperature": 0.0, "num_ctx": 4096},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", str(exc)))

    t0 = time.monotonic()
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=timeout)
        elapsed = round(time.monotonic() - t0, 1)
        if kind == "ok":
            return value, elapsed
        return f"ERROR: {value}", elapsed
    except queue.Empty:
        return "TIMEOUT", round(time.monotonic() - t0, 1)


def crop_region(page_img: Image.Image, y0_pct: float, y1_pct: float,
                x0_pct: float = 0.0, x1_pct: float = 100.0) -> Image.Image:
    """Crop a normalized percentage region from a page image."""
    W, H = page_img.size
    x0 = int(x0_pct / 100 * W)
    y0 = int(y0_pct / 100 * H)
    x1 = int(x1_pct / 100 * W)
    y1 = int(y1_pct / 100 * H)
    return page_img.crop((max(0,x0), max(0,y0), min(W,x1), min(H,y1)))


def score_similarity(our_output: str, reference: str) -> dict:
    """Simple scoring vs Landing.ai reference output."""
    import re
    def words(text):
        return set(re.findall(r'\b[a-zA-Z]{3,}\b', text.lower()))

    our_words = words(our_output)
    ref_words = words(reference)

    if not ref_words:
        return {"word_recall": 0, "word_precision": 0}

    recall = len(our_words & ref_words) / len(ref_words)
    precision = len(our_words & ref_words) / len(our_words) if our_words else 0

    # Check for key clinical terms
    key_terms = ["hemodynamic", "nsr", "cardioversion", "anticoagulants",
                 "symptomatic", "pharmacological", "amiodarone", "tachycardia"]
    found = sum(1 for t in key_terms if t in our_output.lower())

    return {
        "word_recall": round(recall, 2),
        "word_precision": round(precision, 2),
        "key_terms_found": f"{found}/{len(key_terms)}",
    }


def main():
    model = "granite3.2-vision"
    for arg in sys.argv[1:]:
        if arg.startswith("--model"):
            model = arg.split("=")[-1] if "=" in arg else sys.argv[sys.argv.index(arg)+1]

    pdf_path = Path("data/samples/icmr_stw_full/cardiology/cardiology_af.pdf")
    if not pdf_path.exists():
        # Try alternate location
        pdf_path = Path("data/samples/icmr_stw/cardiology_af.pdf")
    if not pdf_path.exists():
        print("cardiology_af.pdf not found. Run from project root.")
        sys.exit(1)

    print(f"Loading {pdf_path.name}...")
    pages = load_pages(pdf_path)
    page_img = pages[0].image
    W, H = page_img.size
    print(f"Page size: {W}x{H}px")

    print(f"\nTesting model: {model}")
    print("=" * 60)

    # ── Test 1: Management Algorithm flowchart (y=74-92%) ────────────────────
    print("\n[TEST 1] Management Algorithm flowchart (y=74-92%)")
    print("Landing.ai bbox: left=0.1%, top=73.4%, right=99.6%, bottom=91.6%")
    crop1 = crop_region(page_img, y0_pct=73.4, y1_pct=91.6, x0_pct=0.1, x1_pct=99.6)
    print(f"Crop size: {crop1.width}x{crop1.height}px")

    # Save crop for inspection
    crop1_path = Path("data/validation/cardiology_af/crop_flowchart.png")
    crop1_path.parent.mkdir(parents=True, exist_ok=True)
    crop1.save(str(crop1_path))
    print(f"Crop saved: {crop1_path}")

    print(f"\nSending to {model}...")
    response1, elapsed1 = call_model(model, crop1, FLOWCHART_PROMPT, max_px=1024)
    print(f"Response ({elapsed1}s):\n")
    print(response1)

    score1 = score_similarity(response1, LANDING_AI_OUTPUT)
    print(f"\nVs Landing.ai: word_recall={score1['word_recall']}  "
          f"precision={score1['word_precision']}  "
          f"key_terms={score1['key_terms_found']}")

    print("\n" + "─"*60)
    print("Landing.ai reference output:")
    print(LANDING_AI_OUTPUT)

    # ── Test 2: Heart Rate Control section (y=44-73%) ────────────────────────
    print("\n\n[TEST 2] Heart Rate Control tables (y=44-73%)")
    print("This is the complex management table (2 tables + flowcharts)")
    crop2 = crop_region(page_img, y0_pct=43.5, y1_pct=73.0, x0_pct=0.0, x1_pct=100.0)
    print(f"Crop size: {crop2.width}x{crop2.height}px")

    crop2_path = Path("data/validation/cardiology_af/crop_heartrate.png")
    crop2.save(str(crop2_path))
    print(f"Crop saved: {crop2_path}")

    print(f"\nSending to {model}...")
    response2, elapsed2 = call_model(model, crop2, HEART_RATE_PROMPT, max_px=1024)
    print(f"Response ({elapsed2}s):\n")
    print(response2)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"Model: {model}")
    print(f"Test 1 (flowchart): {elapsed1}s  recall={score1['word_recall']}  key_terms={score1['key_terms_found']}")
    print(f"Test 2 (tables):    {elapsed2}s")
    print(f"\nCrops saved to: data/validation/cardiology_af/")


if __name__ == "__main__":
    main()
