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
    DEEP_REVIEW_TIMEOUT,
    MODEL_KEEP_ALIVE,
    OLLAMA_BASE_URL,
)

# ── Review prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a document quality auditor for medical PDF extractions.

You are given two versions of the same document:

  RAW TEXT   — text extracted directly from the PDF by a library (complete, unformatted)
  FINAL MD   — the result of vision OCR + AI formatting (may have gaps or errors)

Note: images, ECGs, diagrams, and flowcharts are described via a separate vision model
and may appear in FINAL MD but not in RAW TEXT — do not flag these as missing.

Produce a structured quality improvement report using EXACTLY these section headings:

## Missing Content
Every piece of information in RAW TEXT that is absent or truncated in FINAL MD.
For each gap: quote the missing text, state which section it belongs under.

## Wrong or Missing Headings
Headings that are at the wrong level, misspelled, merged, or not present.
Show: current state → correct state.

## Table Issues
Tables with missing columns, missing rows, broken markdown syntax, or misaligned data.
Quote the affected table header and describe the problem.

## Duplicate Content
Sections or paragraphs repeated unnecessarily. Quote the duplicate and its location.

## Formatting Problems
Numbered lists shown as unordered bullets, bold/italic text lost, code blocks broken, etc.

## Overall Assessment
Two-sentence summary of extraction quality and the main failure pattern.

## Quality Score
A single number from 0 to 10. 10 = perfect. 0 = unreadable.
Format: `Score: X/10`

## Priority Fixes
Numbered list, most impactful first, max 6 items.
Each item: one sentence — what to fix and where."""


def _call(raw_text: str, final_md: str) -> str:
    """Send raw text + final markdown to DEEP_REVIEW_MODEL. Returns the review text."""
    # Truncate to fit within a large context window
    raw_trimmed = raw_text[:10_000]
    md_trimmed  = final_md[:10_000]

    user_msg = (
        f"--- RAW TEXT (pdfplumber, ground truth) ---\n{raw_trimmed}\n\n"
        f"--- FINAL MD (vision + AI processed) ---\n{md_trimmed}"
    )

    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            resp = ollama.chat(
                model=DEEP_REVIEW_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": user_msg},
                ],
                options={"temperature": 0.1},
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
    """Unload DEEP_REVIEW_MODEL from memory after review."""
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
) -> Path | None:
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

    try:
        body = _call(raw_text, final_markdown)
    except Exception as exc:
        console.print(f"  [red]Deep review failed: {exc}[/red]")
        return None
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
    return review_out
