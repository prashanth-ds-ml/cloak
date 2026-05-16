"""
Vision processing for meaningful images embedded in ICMR STW PDFs.

Multi-model cascade:
  1. qwen2.5vl:7b  — primary, best for medical docs and OCR in diagrams
  2. llama3.2-vision:11b — fallback for complex clinical images
Each model has its own timeout; failure moves to the next model.
"""
import logging
import queue
import threading
from pathlib import Path
from typing import Optional

import ollama

log = logging.getLogger(__name__)

# (model_name, timeout_seconds) tried in order; first success wins.
# Generous timeouts — Ollama may cold-load a model before processing starts.
VISION_MODEL_CASCADE = [
    ("qwen2.5vl:7b",        180),  # primary — best for OCR / medical flowcharts
    ("llama3.2-vision:11b", 180),  # fallback — strong general vision
]

ECG_PROMPT = """You are a clinical assistant analysing a medical image embedded in an ICMR Standard Treatment Workflow document.

This image appears to be an ECG (electrocardiogram) tracing.

Please describe it clinically and completely:
1. Rhythm: regular or irregular?
2. Heart rate: estimated bpm (count RR intervals if visible)
3. P waves: present, absent, or abnormal?
4. PR interval: normal, short, or prolonged?
5. QRS complex: narrow or wide? Any bundle branch block morphology?
6. Notable features: (e.g., delta waves, ST changes, T-wave abnormalities, long QT)
7. Clinical interpretation: What arrhythmia or condition does this ECG demonstrate?

Output as structured markdown text with clear section labels."""


DIAGRAM_PROMPT = """You are a clinical assistant analysing a medical image embedded in an ICMR Standard Treatment Workflow document.

This image appears to be a clinical diagram, flowchart, or algorithm.

Please transcribe and describe it completely:
1. Transcribe ALL visible text exactly as written — including text inside boxes, arrows, and labels
2. Describe the flow/structure: entry points, decision boxes (Yes/No branches), outcomes
3. If it is a flowchart, describe each step and every arrow/connection in order
4. If it contains a table or grid, reproduce it in markdown table format
5. Preserve all clinical values, drug names, dosages, time windows, and criteria exactly

Output as structured markdown. Use arrows (→) to show flow direction."""


GENERIC_PROMPT = """You are a clinical assistant analysing a medical image embedded in an ICMR Standard Treatment Workflow document.

Please describe this image completely and accurately:
- Transcribe all visible text, including text inside boxes and labels
- Describe any diagrams, charts, flowcharts, or visual elements
- Include all clinical values, drug names, dosages, and annotations
- Note what clinical concept this image is illustrating

Output as structured markdown text."""


def _detect_image_type(width: int, height: int) -> str:
    """Heuristic: narrow-and-wide → ECG strip; large square/tall → diagram."""
    aspect = width / height if height > 0 else 1
    if aspect > 2.5 and width > 400:
        return "ecg"
    if width > 600 and height > 400:
        return "diagram"
    return "generic"


def _call_model_timed(model: str, image_bytes: bytes, prompt: str, timeout: float) -> str:
    """
    Call Ollama vision model in a daemon thread.
    Raises TimeoutError after `timeout` seconds without blocking the main thread.
    Daemon threads don't prevent process exit or hang on ThreadPoolExecutor shutdown.
    """
    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            response = ollama.chat(
                model=model,
                messages=[{
                    "role":    "user",
                    "content": prompt,
                    "images":  [image_bytes],
                }],
                options={"temperature": 0.1},
            )
            result_q.put(("ok", response.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", exc))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(f"{model} did not respond within {timeout}s")

    if kind == "err":
        raise value
    return value


# Session-level sticky model: once a model succeeds for a PDF, reuse it.
# Avoids repeated Ollama model-swap overhead across images in the same PDF.
_sticky_model: Optional[str] = None


def reset_sticky_model() -> None:
    """Call at the start of each PDF to clear the sticky model cache."""
    global _sticky_model
    _sticky_model = None


def describe_image(
    image_bytes: bytes,
    width: int,
    height: int,
    condition: str,
    specialty: str,
    image_index: int = 0,
) -> str:
    """
    Describe a clinical image using the model cascade.
    Returns a markdown description, or a placeholder on total failure.
    Uses a sticky model: once a model succeeds, subsequent images in the
    same PDF use that model first to avoid Ollama model-swap overhead.
    """
    global _sticky_model

    img_type = _detect_image_type(width, height)
    base_prompt = {"ecg": ECG_PROMPT, "diagram": DIAGRAM_PROMPT}.get(img_type, GENERIC_PROMPT)
    prompt = (
        f"Context: This image is from the ICMR Standard Treatment Workflow for "
        f"**{condition}** (Specialty: {specialty}).\n\n"
        + base_prompt
    )

    # Build cascade: put sticky model first if we have one
    cascade = list(VISION_MODEL_CASCADE)
    if _sticky_model:
        cascade = [(m, t) for m, t in cascade if m == _sticky_model] + \
                  [(m, t) for m, t in cascade if m != _sticky_model]

    for model, timeout in cascade:
        log.info("  [vision] Trying %s (timeout=%ds)…", model, timeout)
        try:
            result = _call_model_timed(model, image_bytes, prompt, timeout)
            if len(result) > 50:
                log.info("  [vision] %s succeeded (%d chars)", model, len(result))
                _sticky_model = model
                return result
            log.warning("  [vision] %s returned short response (%d chars) — trying next", model, len(result))
        except TimeoutError:
            log.warning("  [vision] %s timed out after %ds — trying next", model, timeout)
        except Exception as e:
            log.warning("  [vision] %s failed: %s — trying next", model, e)

    return (
        f"[Image {image_index + 1}: All vision models failed — "
        f"ECG/diagram description unavailable]"
    )


def save_image(
    image_bytes: bytes,
    ext: str,
    out_dir: Path,
    condition: str,
    index: int,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{condition.lower().replace(' ', '_')}_{index}.{ext}"
    path  = out_dir / fname
    path.write_bytes(image_bytes)
    return path
