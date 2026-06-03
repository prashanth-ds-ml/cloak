"""
test_single_elements.py -- Test granite3.2-vision on individual Landing.ai element crops.

Uses the exact bboxes from Landing.ai JSON to crop each element separately,
then tests the model on each one. This is the correct approach — one element at a time.

Usage: python scripts/test_single_elements.py
"""
from __future__ import annotations
import sys, io, time, json, queue, threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from PIL import Image
import ollama
from cloak.extraction.pdf_tools import load_pages
from cloak.config import MODEL_KEEP_ALIVE

MODEL = "granite3.2-vision"
LANDING_AI_JSON = Path("C:/Users/prash/Downloads/1768823343_cardiology_1-1.parse.json")
PDF_PATH = Path("data/samples/icmr_stw_full/cardiology/cardiology_af.pdf")
OUT_DIR = Path("data/validation/cardiology_af/elements")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Prompts per element type ──────────────────────────────────────────────────

PROMPTS = {
    "table": """\
Extract this table as clean markdown.
Rules:
- Use | col1 | col2 | format with a | --- | --- | separator row after the header
- Preserve all values exactly including numbers, scores, and text
- For merged cells, repeat the value in each affected cell
- Output only the markdown table, no preamble""",

    "flowchart": """\
This is a clinical decision flowchart. Transcribe it as a decision tree.
Rules:
- Start from the entry point at the top
- For each decision, write the question exactly as shown
- Show branches: Yes -> [action]  or  No -> [action]
- Use 2-space indentation per level
- Include every action and endpoint verbatim
- Follow arrows carefully

Output only the transcribed decision tree, no description""",

    "text": """\
Extract all text from this document section exactly as it appears.
Rules:
- Preserve headings, bullet points, and structure
- Use ## for section headings
- Use bullet points for lists
- Output only the extracted content, no commentary""",

    "logo": """\
Describe this logo or emblem in one sentence.
Output only the description.""",
}

# ── VLM call ─────────────────────────────────────────────────────────────────

def call_vlm(image: Image.Image, prompt: str, timeout: float = 90.0) -> tuple[str, float]:
    w, h = image.size
    if max(w, h) > 1024:
        scale = 1024 / max(w, h)
        image = image.resize((int(w*scale), int(h*scale)), Image.LANCZOS)

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    result_q: queue.Queue = queue.Queue()
    def _worker():
        try:
            resp = ollama.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt, "images": [img_bytes]}],
                options={"temperature": 0.0, "num_ctx": 2048},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", str(exc)))

    t0 = time.monotonic()
    threading.Thread(target=_worker, daemon=True).start()
    try:
        kind, val = result_q.get(timeout=timeout)
        elapsed = round(time.monotonic() - t0, 1)
        return (val if kind == "ok" else f"ERROR: {val}"), elapsed
    except queue.Empty:
        return "TIMEOUT", round(time.monotonic() - t0, 1)


def crop_bbox(img: Image.Image, box: dict) -> Image.Image:
    W, H = img.size
    x0 = int(box["left"] * W)
    y0 = int(box["top"] * H)
    x1 = int(box["right"] * W)
    y1 = int(box["bottom"] * H)
    return img.crop((max(0,x0), max(0,y0), min(W,x1), min(H,y1)))


# ── Landing.ai reference content per element ──────────────────────────────────

LA_REFERENCE = {
    "CHA2DS2-VASc table": """| CHA₂DS₂-VASc | SCORE | HAS-BLED | SCORE |
| --- | --- | --- | --- |
| Congestive heart failure/LV dysfunction | 1 | Hypertension i.e. uncontrolled BP | 1 |
| Hypertension | 1 | Abnormal renal/liver function | 1 or 2 |
| Aged ≥ 75 years | 2 | Stroke | 1 |
| Diabetes mellitus | 1 | Bleeding tendency or predisposition | 1 |
| Stroke/TIA/TE | 2 | Labile INR | 1 |
| Vascular disease | 1 | Age (e.g. >65) | 1 |
| Aged 65-74 years | 1 | Drugs (e.g. concomitant aspirin or NSAIDs or alcohol) | 1 |
| Maximum Score | 9 | | 9 |""",

    "Heart Rate Control table": """| HEART RATE CONTROL ||||
| In all patients except hemodynamic instability | Beta blocker or calcium blocker or combination | BB ± digoxin in HF | Rate aim to be less than 110/min |""",

    "Conversion to NSR table": """| CONVERSION TO NSR ||||
| Hemodynamic instability | Uncontrolled symptoms despite HR control | Unacceptable rate control drug side effects | Patients' preference |""",

    "Management Algorithm": """MANAGEMENT ALGORITHM
Sign/symptoms suggestive of AF → Confirm by 12 channel rhythm strip
Hemodynamic Instability → NSR required?
  Yes → Pharmacological cardio version → Success? Yes → Continue, No → DC version
  No → Very rapid HR >130/min?
    Yes → CHF? Yes/No → HR control Aim <110/min → BB choices
    No → Symptomatic? Yes → Consider DC version, No → Clinical Follow-up
Anti-coagulants in all Except: Limited period / Score <2""",

    "Pharmacological Cardioversion": """Pharmacological Cardioversion
CHF / CAD / Abnormal LVH → Amiodarone
Normal Heart → Flecainide, Ibutilide, Propafenone OR Pill in pocket (Flecainide OR Propafenone)""",
}


def main():
    print(f"Loading PDF and Landing.ai JSON...")
    pages = load_pages(PDF_PATH)
    page_img = pages[0].image
    W, H = page_img.size
    print(f"Page: {W}x{H}px")

    la_data = json.loads(LANDING_AI_JSON.read_text(encoding="utf-8"))
    chunks = la_data["chunks"]
    grounding = la_data["grounding"]
    print(f"Landing.ai chunks: {len(chunks)}")

    # Pick the most interesting elements to test
    test_elements = [
        # (element_id, display_name, element_type, prompt_key)
        ("6c338217-a67b-40a6-b658-45b0782fbaee", "CHA2DS2-VASc table",          "table",     "table"),
        ("5e154b1e-cd2a-4b4e-9e22-12da01a99ae3", "Heart Rate Control table",     "table",     "table"),
        ("f2188633-e0ea-4943-a1e1-43256d3950f4", "Conversion to NSR table",      "table",     "table"),
        ("bca16dab-a43a-4cc3-8c6e-60253e879d86", "Management section",           "text",      "text"),
        ("0097471b-50da-488b-a863-76a25bd5ae78", "Management Algorithm",         "figure",    "flowchart"),
        ("d35ffa81-99e5-4eca-bdd3-55e9361f7d61", "Pharmacological Cardioversion", "figure",   "flowchart"),
        ("d18e6f86-0c6f-4f32-89ae-f258c912bcad", "Long-term Rhythm Control",     "figure",    "flowchart"),
        ("dc645ffd-946f-45e1-b35f-ba48de37a594", "Government of India logo",     "logo",      "logo"),
        ("f7e303fe-e2dd-4196-b58f-38b32eaba94c", "SYMPTOMS/SIGNS/WHEN TO SUSPECT", "text",   "text"),
    ]

    results = []

    for elem_id, name, la_type, prompt_key in test_elements:
        print(f"\n{'='*60}")
        print(f"Element: {name}  (type: {la_type})")

        # Get bbox from grounding
        g = grounding.get(elem_id)
        if not g:
            print(f"  No grounding found for {elem_id}")
            continue

        box = g["box"]
        la_confidence = g.get("confidence")
        print(f"  BBox: left={box['left']:.3f} top={box['top']:.3f} right={box['right']:.3f} bottom={box['bottom']:.3f}")
        if la_confidence:
            print(f"  Landing.ai confidence: {la_confidence}")

        # Crop the element
        crop = crop_bbox(page_img, box)
        print(f"  Crop size: {crop.width}x{crop.height}px")

        # Save crop
        safe_name = name.replace(" ", "_").replace("/", "-").lower()
        crop_path = OUT_DIR / f"{safe_name}.png"
        crop.save(str(crop_path))

        # Run model
        prompt = PROMPTS[prompt_key]
        print(f"  Calling {MODEL}...")
        response, elapsed = call_vlm(crop, prompt)
        print(f"  Response ({elapsed}s):")
        print()
        for line in response.splitlines():
            print(f"    {line}")

        # Compare with Landing.ai reference if available
        la_ref = LA_REFERENCE.get(name)
        if la_ref:
            # Word overlap
            import re
            def words(t): return set(re.findall(r'\b[a-zA-Z]{3,}\b', t.lower()))
            our_w = words(response)
            ref_w = words(la_ref)
            recall = round(len(our_w & ref_w) / len(ref_w) if ref_w else 0, 2)
            precision = round(len(our_w & ref_w) / len(our_w) if our_w else 0, 2)
            print()
            print(f"  vs Landing.ai: recall={recall}  precision={precision}")
            print(f"  Landing.ai reference:")
            for line in la_ref.splitlines():
                print(f"    {line}")

        results.append({
            "name": name,
            "type": la_type,
            "crop_size": f"{crop.width}x{crop.height}",
            "elapsed_s": elapsed,
            "response_chars": len(response),
            "timed_out": "TIMEOUT" in response,
        })

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'Element':<35} {'Type':<10} {'Size':<12} {'Time':>6} {'Chars':>6} {'OK':>4}")
    print("-" * 80)
    for r in results:
        ok = "✓" if not r["timed_out"] else "✗"
        print(f"  {r['name']:<33} {r['type']:<10} {r['crop_size']:<12} "
              f"{r['elapsed_s']:>5}s {r['response_chars']:>6} {ok:>4}")

    print(f"\nElement crops saved to: {OUT_DIR}/")


if __name__ == "__main__":
    main()
