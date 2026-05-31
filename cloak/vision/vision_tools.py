"""
vision_tools.py — all Ollama vision model calls in one place.
Thin wrappers: no scoring logic, no routing decisions.
All calls run in daemon threads with configurable timeouts.
"""
from __future__ import annotations

import io
import json
import queue
import threading
from typing import Any

import ollama
from PIL import Image

import subprocess
import time
from typing import Callable

import httpx

from cloak.config import (
    EXAM_MAX_IMAGE_PX,
    JUDGE_MAX_IMAGE_PX,
    MAX_IMAGE_PX,
    MODEL_KEEP_ALIVE,
    OLLAMA_BASE_URL,
    ORCHESTRATOR_MODEL,
    STALL_SECONDS,
    VISION_NUM_CTX,
    VISION_PRIMARY,
    VISION_TIMEOUT,
)


# ── Exceptions ────────────────────────────────────────────────────────────────

class VisionTimeoutError(Exception):
    pass


class VisionCallError(Exception):
    pass


# ── Image serialisation ───────────────────────────────────────────────────────

def _prepare_image(image: Image.Image, max_px: int = MAX_IMAGE_PX) -> bytes:
    """Resize so the long edge ≤ max_px, then encode as PNG bytes."""
    w, h = image.size
    long_edge = max(w, h)
    if long_edge > max_px:
        scale = max_px / long_edge
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# ── Core timed call ───────────────────────────────────────────────────────────

# ── Progress callback (set by parser_agent during active phases) ──────────────
# Signature: (token_count: int, elapsed: float, since_last: float, label: str) -> None
_progress_cb: Callable[[int, float, float, str], None] | None = None


def set_progress_callback(fn: Callable[[int, float, float, str], None] | None) -> None:
    """Register a live-update callback. Call with None to clear."""
    global _progress_cb
    _progress_cb = fn


# ── Stall detection ───────────────────────────────────────────────────────────

def _stall_reason(model: str, token_count: int, since_last: float) -> str:
    """Probe GPU OOM and Ollama state to explain why a model stopped generating."""
    # Check if model is still loaded in Ollama
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=3)
        loaded = [m["name"] for m in resp.json().get("models", [])]
        if not any(model.split(":")[0] in n for n in loaded):
            return "model was unloaded from Ollama (OOM or Ollama restart)"
    except Exception:
        pass

    # Check GPU memory pressure
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split(",")
            used, total = float(parts[0].strip()), float(parts[1].strip())
            pct = used / total * 100
            if pct > 95:
                return f"GPU OOM ({used:.0f}/{total:.0f} MiB, {pct:.0f}% used)"
    except Exception:
        pass

    if token_count == 0:
        return "no tokens generated — model may be loading or context too large"
    return f"generation paused {since_last:.0f}s — CPU offload lag or context pressure"


def _thinking_options(think: bool, num_ctx: int, temperature: float = 0.1) -> dict:
    """Build Ollama options dict. Adds think=True/False for gemma4 and qwen3 VLM families (D49)."""
    opts: dict = {"temperature": temperature, "num_ctx": num_ctx}
    model_lower = VISION_PRIMARY.lower()
    if "gemma4" in model_lower or "qwen3" in model_lower:
        opts["think"] = think
    return opts


def _call_timed(
    model: str,
    messages: list[dict[str, Any]],
    timeout: float,
    temperature: float = 0.1,
    think: bool = False,
    num_ctx: int = VISION_NUM_CTX,
    label: str = "",
    json_format: bool = False,
) -> str:
    """
    Stream tokens from Ollama with live progress and stall detection.

    Progress is reported via the module-level _progress_cb (set by parser_agent).
    Stall detection fires after STALL_SECONDS with no new tokens — probes GPU OOM
    and Ollama state, then raises VisionTimeoutError with a human-readable reason.
    Hard timeout fires at `timeout` seconds regardless.
    json_format=True passes format="json" to Ollama, enforcing valid JSON output.
    """
    chunks: list[str] = []
    token_count = 0
    last_token_at = time.monotonic()
    error_holder: list[Exception | None] = [None]
    done_event = threading.Event()
    start = time.monotonic()

    def _worker() -> None:
        nonlocal token_count, last_token_at
        try:
            kwargs: dict[str, Any] = dict(
                model=model,
                messages=messages,
                options=_thinking_options(think, num_ctx, temperature),
                keep_alive=MODEL_KEEP_ALIVE,
                stream=True,
            )
            if json_format:
                kwargs["format"] = "json"
            for chunk in ollama.chat(**kwargs):
                piece = chunk.message.content or ""
                if piece:
                    chunks.append(piece)
                    token_count += 1
                    last_token_at = time.monotonic()
        except Exception as exc:
            error_holder[0] = exc
        finally:
            done_event.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    while not done_event.wait(timeout=0.5):
        elapsed    = time.monotonic() - start
        since_last = time.monotonic() - last_token_at

        # Hard timeout — report stall reason
        if elapsed >= timeout:
            reason = _stall_reason(model, token_count, since_last)
            raise VisionTimeoutError(
                f"{model} timed out after {timeout:.0f}s — {reason}"
            )

        # Stall threshold — only fires after first token is received.
        # token_count == 0 means model is still loading (17GB cold load can take 60-120s);
        # that is NOT a stall — the hard timeout handles genuinely stuck cold loads.
        if token_count > 0 and since_last >= STALL_SECONDS:
            reason = _stall_reason(model, token_count, since_last)
            raise VisionTimeoutError(
                f"{model} stalled mid-generation ({reason}) — {token_count} tokens then silent {since_last:.0f}s"
            )

        # Live progress update via registered callback
        if _progress_cb is not None:
            _progress_cb(token_count, elapsed, since_last, label or model)

    if error_holder[0] is not None:
        raise VisionCallError(f"{model} error: {error_holder[0]}") from error_holder[0]

    return "".join(chunks).strip()


# ── Prompts ───────────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
You are a document parser. Extract ALL content from this page into structured markdown.

Include every heading, section title, body text, table, list, figure caption,
footnote, and abbreviation visible on the page.
Do NOT summarise — extract verbatim where possible.
Use ## for major headings and ### for sub-headings.
Reproduce tables in markdown table format.
Output only the markdown. No preamble, no closing remarks, no code fences (``` or ```markdown)."""

_ECG_PROMPT = """\
You are analysing an ECG tracing from a document.

Describe it completely:
- Rhythm: regular or irregular?
- Heart rate: estimated bpm
- P waves: present, absent, or abnormal?
- PR interval: normal, short, or prolonged?
- QRS: narrow or wide? Any bundle branch block?
- Notable features: ST changes, T-wave abnormalities, delta waves, long QT, etc.
- Interpretation: what arrhythmia or condition does this demonstrate?

Output as plain markdown with labelled bullet points. No code fences."""

_DIAGRAM_PROMPT = """\
You are a document assistant transcribing a diagram or flowchart.

Produce a single clean markdown block:
- Transcribe ALL visible text exactly — boxes, arrows, labels, decision nodes
- For flowcharts: write each step and connection using → (e.g. Step A → Yes → Step B)
- For tables: reproduce in markdown table format with | header | ... |
- Preserve every value, label, measurement, criterion, and reference

Output as plain markdown. Use → for flow direction.
Do NOT use code fences (``` or ```markdown). Do NOT add section headers like "Visual Content" or "Text Transcription"."""

_FIGURE_PROMPT = """\
You are a document assistant describing a figure or image.

First, transcribe every piece of visible text (labels, annotations, captions, titles).
Then, in one or two sentences, describe what the figure shows or illustrates.

Output as plain markdown. Do NOT use code fences (``` or ```markdown).
Do NOT add meta-headers like "Visual Content:", "Concept Illustrated:", or "Description:"."""

_JUDGE_PROMPT = """\
You are a document QA reviewer. Compare the page image with the extracted markdown below.

Score how completely the markdown captures EVERYTHING visible on the page (0.0 to 10.0).
List any content that is missing, wrong, or out of order as gaps.
Action: "accept" (score >= 8.0), "patch" (score >= 5.0), "fallback" (score < 5.0).

You MUST respond with ONLY this JSON object — no other text, no markdown fences, no explanation:
{{"score": 8.5, "gaps": ["missing table in section X", "figure caption absent"], "action": "accept"}}

Fill in the real values from your assessment. Output the JSON object and nothing else.

--- EXTRACTED MARKDOWN ---
{markdown}"""

_JUDGE_GROUNDED_PROMPT = """\
You are a document QA reviewer verifying a specific checklist.

The document layout analyser detected these elements on this page:
{element_checklist}

Check the extracted markdown below against this checklist.
Score completeness 0.0 to 10.0. List only missing or incorrect items as gaps.
Action: "accept" (score >= 8.0), "patch" (score >= 5.0), "fallback" (score < 5.0).

You MUST respond with ONLY this JSON object — no other text, no markdown fences, no explanation:
{{"score": 8.5, "gaps": ["missing table in section X", "figure caption absent"], "action": "accept"}}

Fill in the real values from your assessment. Output the JSON object and nothing else.

--- EXTRACTED MARKDOWN ---
{markdown}"""

_SLIDE_PROMPT = """\
You are extracting a presentation slide into structured markdown.

Rules:
- Slide title → ## heading
- Sub-headings or section labels → ### heading
- Bullet points → preserve indent level (-, --, ---) exactly as shown
- Numbered lists → preserve numbering
- Figures, charts, diagrams → describe as [Figure: one-sentence description]
- Tables → reproduce in markdown table format
- Speaker notes or footnotes (if visible) → > blockquote
- Equations → write LaTeX inline: $formula$ or block: $$formula$$

Output only the markdown for this one slide. No preamble, no "This slide shows..." wrapper."""

_EXAM_PROMPT = """\
You are extracting an exam question paper page into structured markdown.

Rules:
- Question numbers (Q.1, Q2, 1., (i)) → preserve exactly as shown
- Section headers (Section A, PART I) → ## heading
- Mathematical expressions → write as LaTeX: inline $formula$, display $$formula$$
- Chemical equations → write inline with proper notation
- Numbered/lettered answer options (A) B) (a) (b)) → preserve as-is on separate lines
- Diagrams, circuits, graphs → describe as [Figure: one-sentence description]
- Tables → reproduce in markdown table format
- Do NOT add preamble or explanation — output only the markdown content of this page
- Capture ALL equations, even simple ones like x = 2 or F = ma"""

_POSTER_PROMPT = """\
You are a transcription machine for a clinical flowchart or medical poster.
Your ONLY job is to copy the text you see in the image. Do NOT add, invent, or improve anything.

CRITICAL RULES — violations produce wrong clinical output:
1. COPY ONLY: Write exactly what you see. If the image shows "RL 10-15ml/kg/hr", write "RL 10-15ml/kg/hr".
2. DO NOT rewrite, correct, expand, or "improve" the content. Do NOT say "The provided content appears to be..." or generate your own version.
3. DO NOT add information from your training data. Only text visible in the image.
4. Use ## for major section headings (e.g. ## WHEN TO SUSPECT?, ## INVESTIGATIONS, ## SHOCK)
5. Use ### for sub-headings within sections
6. Use bullet points for list items
7. Show branching using indentation:
   - Parent step
     - Branch condition (Improvement / No Improvement)
       - Next step with exact values
8. Preserve ALL numbers, units, signs (>, <, ≥, ≤, %) EXACTLY as shown — never change them
9. Reproduce tables as markdown: | Column | Column |
10. Include ALL text at the bottom — disclaimers, copyright, dates, URLs
11. Output only the transcribed markdown. No preamble, no commentary, no code fences."""

_REGION_PROMPTS = {
    "ecg":     _ECG_PROMPT,
    "diagram": _DIAGRAM_PROMPT,
    "figure":  _FIGURE_PROMPT,
}

# ── Output cleaning ───────────────────────────────────────────────────────────

import re as _re

def _strip_code_fences(text: str) -> str:
    """
    Unwrap ```markdown...``` blocks that VLMs emit when transcribing document text.
    Only strips fences explicitly tagged as 'markdown' — preserves genuine code blocks
    (tagged python, bash, etc., or bare ``` which may fence actual code).
    Applied to full_page_extract and region_describe output only (not judge_quality).
    """
    text = _re.sub(r'```markdown\s*\n(.*?)\n```', r'\1', text, flags=_re.DOTALL)
    return text.strip()


_HALLUCINATION_RE = _re.compile(
    r'^(?:it seems|i notice|the description (?:appears|seems|provided)|'
    r'based on the partial|the image description you provided|'
    r'the provided description|unfortunately|i (?:cannot|can\'t) (?:see|read|interpret)|'
    # Rewrite/correction patterns — model generates its own version instead of transcribing
    r'the provided content (?:appears|seems)|'
    r'the (?:following|content) (?:appears|seems) to be (?:fragmented|incorrect|incomplete)|'
    r'below is a (?:structured|corrected|formatted|revised|cleaned)|'
    r'here is a (?:structured|corrected|formatted|revised|cleaned)|'
    r'i (?:have|\'ve) (?:restructured|reformatted|corrected|cleaned up)|'
    r'this (?:appears|seems) to be a (?:fragmented|partial|incomplete))',
    _re.IGNORECASE,
)


def _strip_hallucination(text: str) -> str:
    """Gap F: return empty when VLM generates meta-commentary instead of describing the figure."""
    if _HALLUCINATION_RE.match(text.strip()):
        return ""
    return text


# ── Public API ────────────────────────────────────────────────────────────────

def full_page_extract(
    image: Image.Image,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
) -> str:
    """Send a full page image to a vision model and return raw markdown."""
    img_bytes = _prepare_image(image)
    messages = [{
        "role":    "user",
        "content": _EXTRACT_PROMPT,
        "images":  [img_bytes],
    }]
    return _strip_hallucination(_strip_code_fences(
        _call_timed(model, messages, timeout, think=False, label="full-page extract")
    ))


def region_describe(
    image: Image.Image,
    label: str,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
) -> str:
    """Describe a region crop (ECG, diagram, figure) using the label-specific prompt."""
    prompt = _REGION_PROMPTS.get(label, _FIGURE_PROMPT)
    img_bytes = _prepare_image(image)
    messages = [{
        "role":    "user",
        "content": prompt,
        "images":  [img_bytes],
    }]
    return _strip_hallucination(_strip_code_fences(
        _call_timed(model, messages, timeout, think=False, label=f"region:{label}")
    ))


def slide_page(
    image: Image.Image,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
) -> str:
    """Extract a presentation slide into structured markdown (D38)."""
    img_bytes = _prepare_image(image, max_px=EXAM_MAX_IMAGE_PX)  # higher res for dense slides
    messages = [{
        "role":    "user",
        "content": _SLIDE_PROMPT,
        "images":  [img_bytes],
    }]
    return _strip_hallucination(_strip_code_fences(
        _call_timed(model, messages, timeout, think=False, num_ctx=VISION_NUM_CTX, label="slide")
    ))


def exam_page(
    image: Image.Image,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
) -> str:
    """Extract an exam question paper page with full math (D39). think=False — transcription, not reasoning."""
    img_bytes = _prepare_image(image, max_px=EXAM_MAX_IMAGE_PX)  # higher res for dense math
    messages = [{
        "role":    "user",
        "content": _EXAM_PROMPT,
        "images":  [img_bytes],
    }]
    return _strip_hallucination(_strip_code_fences(
        _call_timed(model, messages, timeout, think=False, num_ctx=VISION_NUM_CTX)
    ))


def poster_page(
    image: Image.Image,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
) -> str:
    """
    Full-page VLM extraction for clinical flowcharts and poster-format PDFs (D51).
    Uses a specialized transcription prompt — not generic description.
    Higher resolution (EXAM_MAX_IMAGE_PX) to read dense box text clearly.
    """
    img_bytes = _prepare_image(image, max_px=EXAM_MAX_IMAGE_PX)
    messages = [{
        "role":    "user",
        "content": _POSTER_PROMPT,
        "images":  [img_bytes],
    }]
    return _strip_hallucination(_strip_code_fences(
        _call_timed(model, messages, timeout, think=False,
                    num_ctx=VISION_NUM_CTX, label="poster")
    ))



def _build_element_checklist(page_elements: list) -> str:
    """Build a human-readable checklist from docling elements for the grounded judge prompt."""
    from collections import Counter
    counts = Counter(el.label for el in page_elements)
    lines = []
    label_names = {
        "section_header": "section heading",
        "title": "document title",
        "table": "table",
        "picture": "figure/image",
        "formula": "equation/formula",
        "list_item": "list item",
        "text": "text block",
        "footnote": "footnote",
    }
    for label, count in sorted(counts.items()):
        name = label_names.get(label, label)
        lines.append(f"  - {count}x {name}")
    return "\n".join(lines) if lines else "  - (no structured elements detected)"


def judge_quality(
    page_image: Image.Image,
    extracted_md: str,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
    page_elements: list | None = None,   # DoclingElement list → grounded prompt
) -> dict:
    """
    L4 judge: score extracted markdown against the original page image (D47).
    When page_elements provided, uses grounded prompt with docling checklist.
    Returns dict with keys: score (float), gaps (list[str]), action (str).
    Falls back to a safe default on JSON parse failure.
    """
    if page_elements:
        checklist = _build_element_checklist(page_elements)
        prompt = _JUDGE_GROUNDED_PROMPT.format(
            element_checklist=checklist,
            markdown=extracted_md[:12000],
        )
    else:
        prompt = _JUDGE_PROMPT.format(markdown=extracted_md[:12000])
    img_bytes = _prepare_image(page_image, max_px=JUDGE_MAX_IMAGE_PX)
    messages = [{
        "role":    "user",
        "content": prompt,
        "images":  [img_bytes],
    }]

    # think=False: judge needs quick completeness assessment, not deep reasoning (D48).
    # json_format=False: format="json" causes qwen3-vl to stall (0 tokens for 13+ min)
    # because Ollama buffers the full response for grammar validation with vision input.
    # JSON reliability is handled by explicit prompt template + robust extraction below.
    raw = _call_timed(model, messages, timeout, think=False, label="quality-judge")

    cleaned = raw.strip()
    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    # Attempt 1: parse the whole response as JSON
    try:
        result = json.loads(cleaned)
        return {
            "score":  float(result.get("score", 0.0)),
            "gaps":   list(result.get("gaps", [])),
            "action": str(result.get("action", "patch")),
        }
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 2: extract a JSON object embedded anywhere in a prose response
    # qwen3-vl sometimes wraps the JSON in explanation text
    m = _re.search(r'\{[^{}]*"score"\s*:[^{}]*\}', raw, _re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            return {
                "score":  float(result.get("score", 0.0)),
                "gaps":   list(result.get("gaps", [])),
                "action": str(result.get("action", "patch")),
            }
        except (json.JSONDecodeError, ValueError):
            pass

    # D34: regex fallback chain — extract score from partial or non-JSON response
    m = _re.search(r'"score"\s*:\s*([0-9]+(?:\.[0-9]+)?)', raw)
    if m:
        score = min(10.0, float(m.group(1)))
        return {"score": score, "gaps": ["partial JSON — score extracted by regex"],
                "action": "accept" if score >= 8.0 else "patch"}
    m = _re.search(r'\b([0-9]+(?:\.[0-9]+)?)\s*/\s*10\b', raw)
    if m:
        score = min(10.0, float(m.group(1)))
        return {"score": score, "gaps": ["text response — score extracted from X/10 format"],
                "action": "accept" if score >= 8.0 else "patch"}
    return {"score": 5.0, "gaps": ["JSON parse failed — model response was not valid JSON"], "action": "patch"}
