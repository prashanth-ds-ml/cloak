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

from cloak.config import (
    MAX_IMAGE_PX,
    MODEL_KEEP_ALIVE,
    MODEL_NUM_CTX,
    VISION_PRIMARY,
    VISION_TIMEOUT,
)


# ── Exceptions ────────────────────────────────────────────────────────────────

class VisionTimeoutError(Exception):
    pass


class VisionCallError(Exception):
    pass


# ── Image serialisation ───────────────────────────────────────────────────────

def _prepare_image(image: Image.Image) -> bytes:
    """Resize so the long edge ≤ MAX_IMAGE_PX, then encode as PNG bytes."""
    w, h = image.size
    long_edge = max(w, h)
    if long_edge > MAX_IMAGE_PX:
        scale = MAX_IMAGE_PX / long_edge
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# ── Core timed call ───────────────────────────────────────────────────────────

def _call_timed(
    model: str,
    messages: list[dict[str, Any]],
    timeout: float,
    temperature: float = 0.1,
) -> str:
    """
    Run an ollama.chat() call in a daemon thread.
    Returns the text response or raises VisionTimeoutError / VisionCallError.
    """
    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            resp = ollama.chat(
                model=model,
                messages=messages,
                options={"temperature": temperature, "num_ctx": MODEL_NUM_CTX},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", exc))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=timeout)
    except queue.Empty:
        raise VisionTimeoutError(f"{model} did not respond within {timeout}s")

    if kind == "err":
        raise VisionCallError(f"{model} error: {value}") from value
    return value


# ── Prompts ───────────────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
You are a document parser. Extract ALL content from this page into structured markdown.

Include every heading, section title, body text, table, list, figure caption,
footnote, and abbreviation visible on the page.
Do NOT summarise — extract verbatim where possible.
Use ## for major headings and ### for sub-headings.
Reproduce tables in markdown table format.
Output only the markdown. No preamble or closing remarks."""

_ECG_PROMPT = """\
You are analysing an ECG tracing from a document.

Describe it completely:
1. Rhythm: regular or irregular?
2. Heart rate: estimated bpm
3. P waves: present, absent, or abnormal?
4. PR interval: normal, short, or prolonged?
5. QRS: narrow or wide? Any bundle branch block?
6. Notable features: ST changes, T-wave abnormalities, delta waves, long QT, etc.
7. Interpretation: what arrhythmia or condition does this demonstrate?

Output as structured markdown with clear labels."""

_DIAGRAM_PROMPT = """\
You are a document assistant transcribing a diagram.

Transcribe and describe it completely:
1. Transcribe ALL visible text exactly — including text in boxes, arrows, labels
2. Describe the structure: entry points, decision boxes (Yes/No), outcomes
3. If a flowchart: describe each step and every connection in order (use →)
4. If a table: reproduce in markdown table format
5. Preserve all values, labels, measurements, criteria, and references exactly

Output as structured markdown. Use → to show flow direction."""

_FIGURE_PROMPT = """\
You are a document assistant describing a figure.

Transcribe all visible text including labels and annotations.
Describe the visual content and what concept or information it illustrates.
Output as structured markdown."""

_JUDGE_PROMPT = """\
You are a document QA reviewer.

You are given:
  1. The original page image
  2. The extracted markdown (shown below)

Score how completely the markdown captures EVERYTHING visible on the page (0.0 to 10.0).
Then list any content that is missing or incorrectly extracted.
Finally decide the action:
  "accept"   — score >= 8.0, extraction is good enough
  "patch"    — score >= 5.0, specific gaps can be filled
  "fallback" — score < 5.0, extraction failed, try a different model

Respond ONLY with valid JSON — no explanation, no markdown fences:
{{"score": <float>, "gaps": [<string>, ...], "action": "<accept|patch|fallback>"}}

--- EXTRACTED MARKDOWN ---
{markdown}"""

_REGION_PROMPTS = {
    "ecg":     _ECG_PROMPT,
    "diagram": _DIAGRAM_PROMPT,
    "figure":  _FIGURE_PROMPT,
}

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
    return _call_timed(model, messages, timeout)


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
    return _call_timed(model, messages, timeout)



def judge_quality(
    page_image: Image.Image,
    extracted_md: str,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
) -> dict:
    """
    Score extracted markdown against the original page image.
    Returns dict with keys: score (float), gaps (list[str]), action (str).
    Falls back to a safe default on JSON parse failure.
    """
    prompt = _JUDGE_PROMPT.format(markdown=extracted_md[:6000])
    img_bytes = _prepare_image(page_image)
    messages = [{
        "role":    "user",
        "content": prompt,
        "images":  [img_bytes],
    }]

    raw = _call_timed(model, messages, timeout)

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    try:
        result = json.loads(cleaned)
        return {
            "score":  float(result.get("score", 0.0)),
            "gaps":   list(result.get("gaps", [])),
            "action": str(result.get("action", "patch")),
        }
    except (json.JSONDecodeError, ValueError):
        return {"score": 0.0, "gaps": ["JSON parse failed — model response was not valid JSON"], "action": "patch"}
