"""
deep_review.py — Phase 9: post-pipeline deep quality review.

Runs AFTER all pipeline models are unloaded via model_router.teardown_pdf().
Loads DEEP_REVIEW_MODEL (typically larger than pipeline models) and lets Ollama
place it across CPU+GPU shared memory automatically.

Compares the raw pdfplumber text (ground-truth text content) against the final
AI-processed markdown and writes an actionable quality improvement report.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

import ollama

from cloak.config import (
    DEEP_REVIEW_MODEL,
    DEEP_REVIEW_NUM_CTX,
    DEEP_REVIEW_TIMEOUT,
    MODEL_KEEP_ALIVE,
    OLLAMA_BASE_URL,
)

# ── Review prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a document quality auditor. You compare two versions of a document and identify \
extraction errors. You output structured reports with specific section headings only — \
no summaries, no general commentary, no extra sections."""

_USER_FORMAT = """\
You are comparing two versions of the same document. Fill in the template below exactly as shown.
Do NOT change the section headings. Do NOT add extra sections. Replace each placeholder with your analysis.

RAW TEXT is the ground truth (extracted directly from the PDF by a library).
FINAL MD is the AI-processed result (may have gaps, formatting errors, or added content).

Images, diagrams, and logos are described by a vision model — they appear in FINAL MD but NOT in RAW TEXT.
Do NOT flag vision-described content as missing.

---

## Missing Content
[Every piece of information in RAW TEXT absent or truncated in FINAL MD. Quote the missing text and name the section it belongs under. If nothing is missing, write exactly: None identified.]

## Wrong or Missing Headings
[Headings at the wrong level, misspelled, merged, or absent. Format each as: current state → correct state. If none, write exactly: None identified.]

## Table Issues
[Tables with missing columns/rows, broken syntax, or misaligned data. Quote the affected table header and describe the problem. If none, write exactly: None identified.]

## Duplicate Content
[Sections or paragraphs repeated unnecessarily. If none, write exactly: None identified.]

## Formatting Problems
[Numbered lists shown as bullets, bold/italic lost, content inside code fences that should be inline text, etc. If none, write exactly: None identified.]

## Overall Assessment
[Exactly two sentences: sentence 1 — overall extraction quality. Sentence 2 — the main failure pattern, or "No major issues found."]

## Quality Score
Score: [X]/10

## Priority Fixes
[Numbered list, max 6 items, most impactful first. Each item: one sentence — what to fix and where. If no fixes needed, write: None required.]

---

--- RAW TEXT ---
{raw_text}

--- FINAL MD ---
{final_md}"""


def parse_review_score(text: str) -> float | None:
    """Extract 'Score: X/10' from gemma4 review output. Returns None if not found."""
    import re
    m = re.search(r"Score:\s*(\d+(?:\.\d+)?)\s*/\s*10", text)
    return float(m.group(1)) if m else None


def _is_model_installed(model: str) -> bool:
    """Check if the model is available in Ollama without loading it."""
    try:
        import httpx
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        names = [m["name"] for m in resp.json().get("models", [])]
        return any(model == n or model.split(":")[0] == n.split(":")[0] for n in names)
    except Exception:
        return False


def _call(raw_text: str, final_md: str) -> str:
    """Send raw text + final markdown to DEEP_REVIEW_MODEL. Returns the review text."""
    # Truncate to fit within a large context window
    raw_trimmed = raw_text[:10_000]
    md_trimmed  = final_md[:10_000]

    user_msg = _USER_FORMAT.format(raw_text=raw_trimmed, final_md=md_trimmed)

    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            opts: dict = {"temperature": 0.1, "num_ctx": DEEP_REVIEW_NUM_CTX}
            model_lower = DEEP_REVIEW_MODEL.lower()
            if "gemma4" in model_lower or "qwen3" in model_lower:
                opts["think"] = True   # deep audit benefits from reasoning chain (D49)
            resp = ollama.chat(
                model=DEEP_REVIEW_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                options=opts,
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp.message.content.strip()))
        except Exception as exc:
            result_q.put(("err", exc))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=DEEP_REVIEW_TIMEOUT)
    except queue.Empty:
        raise TimeoutError(f"{DEEP_REVIEW_MODEL} did not respond within {DEEP_REVIEW_TIMEOUT}s")

    if kind == "err":
        raise RuntimeError(f"{DEEP_REVIEW_MODEL} error: {value}") from value
    return value


def _unload() -> None:
    """
    Unload DEEP_REVIEW_MODEL from memory after review.
    With two-model split (D49), DEEP_REVIEW_MODEL == ORCHESTRATOR_MODEL — skip
    unload here so teardown_pdf() handles it cleanly after _run_phase9() returns.
    """
    from cloak.config import ORCHESTRATOR_MODEL
    if DEEP_REVIEW_MODEL == ORCHESTRATOR_MODEL:
        return  # same model as pipeline — let teardown_pdf() handle unload
    try:
        import httpx
        httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": DEEP_REVIEW_MODEL, "keep_alive": 0},
            timeout=10,
        )
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def run(
    pdf_path: Path,
    pages: list,           # list[PageData] — still in memory after pipeline
    final_markdown: str,
    review_out: Path,
    console,
) -> tuple[Path | None, float | None]:
    """
    Run deep quality review and write {stem}_review.md.
    Returns the path written, or None if the review failed.
    DEEP_REVIEW_MODEL is unloaded when done regardless of success.
    """
    # Build raw text from pdfplumber — one block per page
    raw_parts = []
    for pg in pages:
        text = pg.text.strip() if pg.text else ""
        if text:
            raw_parts.append(f"[Page {pg.page_num + 1}]\n{text}")
    raw_text = "\n\n---\n\n".join(raw_parts) if raw_parts else "(no pdfplumber text — image-only PDF)"

    if not _is_model_installed(DEEP_REVIEW_MODEL):
        console.print(
            f"  [yellow]Deep review skipped — {DEEP_REVIEW_MODEL} not installed.[/yellow]\n"
            f"  [dim]Install with: ollama pull {DEEP_REVIEW_MODEL}[/dim]"
        )
        return None, None

    try:
        body = _call(raw_text, final_markdown)
        score = parse_review_score(body)
    except Exception as exc:
        console.print(f"  [red]Deep review failed: {exc}[/red]")
        return None, None
    finally:
        _unload()

    report = (
        f"# Quality Review — {pdf_path.name}\n\n"
        f"**Model:** `{DEEP_REVIEW_MODEL}`  ·  "
        f"**Pages reviewed:** {len(pages)}\n\n"
        f"---\n\n"
        f"{body}\n"
    )

    review_out.write_text(report, encoding="utf-8")
    return review_out, score
