"""
parser_agent.py — 9-phase agentic PDF parse pipeline.

Phases:
  0  load_pages          — PyMuPDF + pdfplumber, no model
  1  profile_all         — heuristic page classification, no model (D21)
  2  routing display     — console summary
  3  _extract_by_route   — vision for all page types when available (D23)
  4  _run_format_session — qwen3:8b FORMAT once before the judge-patch loop (D20)
  5  quality_judge       — vision model judges all pages, produces PageScore list
  6  _run_patch_loop     — qwen3:8b fills gaps flagged by judge
  5–6 repeat up to MAX_ROUNDS; judge+patch only — no re-extraction (D19)
  8  write final.md + confidence_report.md
  9  deep_review         — DEEP_REVIEW_MODEL audits final markdown (D27)

Hard rules: D2 (best round wins), D3 (threshold 8.0), D5 (content-loss guard),
D6 (context cap), D19 (extract-once), D20 (FORMAT before patch), D23 (vision for all types).
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import ollama
from PIL import Image
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

console = Console()

from cloak.config import (
    AGENT_TIMEOUT,
    CONTENT_LOSS_LIMIT,
    FORMAT_NUM_CTX,
    MAX_AGENT_ITERS,
    MAX_ROUNDS,
    MD_DIR,
    MODEL_KEEP_ALIVE,
    MODEL_NUM_CTX,
    ORCHESTRATOR_MODEL,
    QUALITY_THRESHOLD,
)
from cloak.extraction.pdf_tools import PageData, load_pages
from cloak.orchestration import context_manager, model_router
from cloak.profiling.page_profiler import build_route_map, profile_all
from cloak.profiling.page_profiler import summarise as summarise_profiles
from cloak.quality import quality_judge
from cloak.vision import vision_tools


# ── Round tracking ────────────────────────────────────────────────────────────

@dataclass
class RoundResult:
    round_num: int
    markdown: str
    score: float
    gaps: list[str]
    action: str
    page_scores: list[quality_judge.PageScore]


# ── Phase checklist UI ────────────────────────────────────────────────────────

class _PhaseUI:
    """
    Compact ticking-checklist display for parse phases.

    Usage:
        ui = _PhaseUI()
        ui.begin("0", "Load")          # records start time, no output yet
        ...do work...
        ui.done("3 pages · 312 KB")    # prints: ✓  Phase 0  Load  3 pages · 312 KB  (0.1s)
    """

    _OK   = "[green]✓[/green]"
    _WARN = "[yellow]⚠[/yellow]"
    _FAIL = "[red]✗[/red]"
    _SKIP = "[dim]–[/dim]"

    def __init__(self) -> None:
        self._phase_t0: float = time.monotonic()
        self._label: str = ""

    def begin(self, phase: str, name: str) -> None:
        self._phase_t0 = time.monotonic()
        self._label = f"[bold cyan]{phase}[/bold cyan]  [bold]{name}[/bold]"

    def done(self, detail: str = "", *, warn: bool = False,
             skip: bool = False, fail: bool = False) -> None:
        icon = self._FAIL if fail else (self._SKIP if skip else (self._WARN if warn else self._OK))
        elapsed = time.monotonic() - self._phase_t0
        t_str   = f"  [dim]{elapsed:.1f}s[/dim]" if elapsed >= 0.5 else ""
        d_str   = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  {icon}  {self._label}{d_str}{t_str}")

    def round_header(self, round_num: int, max_rounds: int) -> None:
        console.print(f"\n  [bold]Round {round_num}/{max_rounds}[/bold]")

    def score_line(self, avg: float, gaps: int, action: str,
                   threshold: float, elapsed: float) -> None:
        threshold_hit = avg >= threshold
        score_color = "green" if avg >= 8.0 else ("yellow" if avg >= 5.0 else "red")
        score_str = f"[{score_color}]{avg:.1f}/10[/{score_color}]"
        threshold_str = (
            f"  [green]✓ threshold {threshold} reached[/green]" if threshold_hit else ""
        )
        gap_str = f"  {gaps} gap{'s' if gaps != 1 else ''}"
        t_str   = f"  [dim]{elapsed:.1f}s[/dim]" if elapsed >= 0.5 else ""
        console.print(f"  {self._OK}  [bold cyan]Judge[/bold cyan]  {score_str}{gap_str}{threshold_str}{t_str}")

    def patch_line(self, before: int, after: int, elapsed: float) -> None:
        delta = after - before
        sign  = "+" if delta >= 0 else ""
        t_str = f"  [dim]{elapsed:.1f}s[/dim]" if elapsed >= 0.5 else ""
        console.print(
            f"  {self._OK}  [bold cyan]Patch[/bold cyan]"
            f"  [dim]{after:,} chars ({sign}{delta:,})[/dim]{t_str}"
        )


# ── Tool definitions for qwen3:8b ─────────────────────────────────────────────

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_page_text",
            "description": "Return spatially sorted text content for a page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_num": {"type": "integer", "description": "0-indexed page number"},
                },
                "required": ["page_num"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_region_description",
            "description": "Return a vision model description of an image region on a page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_num": {"type": "integer"},
                    "region_index": {"type": "integer", "description": "Index into page.regions"},
                },
                "required": ["page_num", "region_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_section",
            "description": "Replace a section in the current markdown draft.",
            "parameters": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["heading", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_section",
            "description": "Append a new section to the current markdown draft.",
            "parameters": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["heading", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that patching is complete. Returns the final markdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {"type": "string", "description": "The complete, final markdown."},
                },
                "required": ["markdown"],
            },
        },
    },
]


# ── Tool execution ────────────────────────────────────────────────────────────

def _execute_tool(
    name: str,
    args: dict,
    pages: list[PageData],
    draft: str,
    images_dir: Path | None = None,
) -> tuple[str, str]:
    """Execute a tool call from the orchestrator. Returns (result_text, updated_draft)."""
    if name == "get_page_text":
        page_num = int(args.get("page_num", 0))
        if page_num >= len(pages):
            return f"Page {page_num} does not exist.", draft
        return pages[page_num].text, draft

    if name == "get_region_description":
        page_num = int(args.get("page_num", 0))
        region_index = int(args.get("region_index", 0))
        if page_num >= len(pages):
            return f"Page {page_num} does not exist.", draft
        regions = pages[page_num].regions
        if region_index >= len(regions):
            return f"Region {region_index} does not exist on page {page_num}.", draft
        region = regions[region_index]
        m = model_router.get_vision_model()
        try:
            desc = vision_tools.region_describe(region.image, region.label, model=m)
            model_router.mark_success(m)
            if images_dir is not None:
                rel = _save_region(region.image, images_dir, page_num, region.label, region_index)
                return f"![{region.label}]({rel})\n\n{desc}", draft
            return desc, draft
        except Exception as exc:
            return f"Region description failed: {exc}", draft

    if name == "patch_section":
        heading = args.get("heading", "")
        content = args.get("content", "")
        new_draft = _replace_section(draft, heading, content)
        return f"Section '{heading}' patched.", new_draft

    if name == "add_section":
        heading = args.get("heading", "")
        content = args.get("content", "")
        new_draft = draft + f"\n\n## {heading}\n\n{content}"
        return f"Section '{heading}' added.", new_draft

    if name == "finish":
        return "__FINISH__", args.get("markdown", draft)

    return f"Unknown tool: {name}", draft


def _replace_section(markdown: str, heading: str, content: str) -> str:
    import re
    pattern = rf"(##\s+{re.escape(heading)}\s*\n)(.*?)(?=\n##\s|\Z)"
    replacement = rf"\g<1>{content}\n"
    new = re.sub(pattern, replacement, markdown, flags=re.DOTALL | re.IGNORECASE)
    return new if new != markdown else markdown + f"\n\n## {heading}\n\n{content}"


# ── Agent tool-calling loop (Phase 6) ────────────────────────────────────────

_PATCH_SYSTEM = """\
You are patching gaps in an extracted document.
Use the available tools to retrieve missing content from the PDF and add it to the markdown.
Do NOT remove or rewrite existing content — only add what is missing.
When done, call finish() with the complete updated markdown."""


def _run_patch_loop(
    pages: list[PageData],
    draft: str,
    gaps: list[str],
    messages: list[dict],
    images_dir: Path | None = None,
) -> str:
    """Run the qwen3:8b tool-calling loop to fill gaps. Returns updated markdown."""
    gap_text = "\n".join(f"- {g}" for g in gaps[:20])
    messages = list(messages)
    messages.append({
        "role": "user",
        "content": (
            f"The quality judge found these gaps in the current extraction:\n{gap_text}\n\n"
            f"Please fill them using the available tools. "
            f"Current draft has {len(draft)} characters."
        ),
    })

    current_draft = draft

    for iteration in range(MAX_AGENT_ITERS):
        result_q: queue.Queue = queue.Queue()

        def _worker() -> None:
            try:
                resp = ollama.chat(
                    model=ORCHESTRATOR_MODEL,
                    messages=messages,
                    tools=_TOOLS,
                    options={"temperature": 0.1, "num_ctx": MODEL_NUM_CTX},
                    keep_alive=MODEL_KEEP_ALIVE,
                )
                result_q.put(("ok", resp))
            except Exception as exc:
                result_q.put(("err", exc))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        try:
            kind, value = result_q.get(timeout=AGENT_TIMEOUT)
        except queue.Empty:
            console.print(f"  [yellow]Agent timeout on iteration {iteration + 1}[/yellow]")
            break

        if kind == "err":
            console.print(f"  [red]Agent error: {value}[/red]")
            break

        resp = value
        assistant_msg: dict = {"role": "assistant", "content": resp.message.content or ""}

        tool_calls = getattr(resp.message, "tool_calls", None) or []
        if not tool_calls:
            messages.append(assistant_msg)
            break

        assistant_msg["tool_calls"] = [
            {"function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in tool_calls
        ]
        messages.append(assistant_msg)

        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = (
                    tc.function.arguments
                    if isinstance(tc.function.arguments, dict)
                    else json.loads(tc.function.arguments)
                )
            except (json.JSONDecodeError, TypeError):
                fn_args = {}

            tool_result, current_draft = _execute_tool(fn_name, fn_args, pages, current_draft, images_dir)
            messages.append({"role": "tool", "content": tool_result})

            if tool_result == "__FINISH__":
                return current_draft

    return current_draft


# ── Phase 3: Per-page extraction strategies ───────────────────────────────────

def _extract_text_page(pg: PageData) -> str:
    """
    Pdfplumber-only extraction: raw text + tables with dedup marker.
    Used as fallback (no vision) and as the text base inside _extract_mixed_page.
    """
    parts: list[str] = []
    if pg.text.strip():
        parts.append(pg.text.strip())
    table_mds = [tbl.to_markdown() for tbl in pg.tables if tbl.to_markdown().strip()]
    if table_mds:
        parts.append(
            "<!-- TABLES: structured form of page content — use these, remove any duplicate prose above -->"
        )
        parts.extend(table_mds)
    return "\n\n".join(p for p in parts if p)


def _extract_text_page_vision(
    pg: PageData,
    model: str,
    images_dir: Path | None = None,
) -> str:
    """
    text_rich with vision: full_page_extract reads the page image and assigns ##/### headings
    from the visual layout in a single pass — no separate layout-hints call needed.
    Region crops (ECGs, figures) are described in detail and saved alongside.
    pdfplumber tables appended as a supplement for reliable cell content.
    Falls back to _extract_text_page() on vision failure.
    """
    if pg.image is None:
        return _extract_text_page(pg)

    try:
        parts = [vision_tools.full_page_extract(pg.image, model=model)]
        model_router.mark_success(model)
    except (vision_tools.VisionTimeoutError, vision_tools.VisionCallError) as exc:
        console.print(
            f"  [yellow]Vision failed page {pg.page_num}: {type(exc).__name__}"
            f" — text fallback[/yellow]"
        )
        return _extract_text_page(pg)

    # Detailed specialist descriptions for embedded image regions
    for i, r in enumerate(pg.regions):
        try:
            desc = vision_tools.region_describe(r.image, r.label, model=model)
            model_router.mark_success(model)
            if images_dir is not None:
                rel = _save_region(r.image, images_dir, r.page_num, r.label, i)
                parts.append(f"![{r.label}]({rel})\n\n{desc}")
            else:
                parts.append(desc)
        except Exception as exc:
            parts.append(
                f"<!-- region {i}: {r.label} — description failed: {type(exc).__name__} -->"
            )

    # pdfplumber tables: more reliable than vision for exact cell content
    table_mds = [tbl.to_markdown() for tbl in pg.tables if tbl.to_markdown().strip()]
    if table_mds:
        parts.append(
            "<!-- TABLES: structured form of page content — use these, remove any duplicate prose above -->"
        )
        parts.extend(table_mds)

    return "\n\n".join(p for p in parts if p)


def _extract_table_page(pg: PageData) -> str:
    """table_heavy: pdfplumber tables only — raw text excluded to prevent duplication."""
    table_mds = [tbl.to_markdown() for tbl in pg.tables if tbl.to_markdown().strip()]
    return "\n\n".join(table_mds) if table_mds else pg.text.strip()


def _extract_scanned_page(pg: PageData) -> str:
    """scanned: Tesseract OCR. Falls back to raw PyMuPDF text on OCRError (D22)."""
    from cloak.extraction import ocr_tools
    if pg.image is None:
        return pg.text
    try:
        return ocr_tools.ocr_page(pg.image)
    except ocr_tools.OCRError as exc:
        console.print(
            f"  [yellow]OCR failed page {pg.page_num}: {exc} — raw text fallback[/yellow]"
        )
        return pg.text


def _extract_vision_page(pg: PageData, model: str) -> str:
    """image_heavy: full-page vision extraction via qwen2.5vl:7b."""
    try:
        md = vision_tools.full_page_extract(pg.image, model=model)
        model_router.mark_success(model)
        for tbl in pg.tables:
            md += "\n\n" + tbl.to_markdown()
        return md
    except (vision_tools.VisionTimeoutError, vision_tools.VisionCallError) as exc:
        console.print(
            f"  [yellow]Vision failed page {pg.page_num}: {type(exc).__name__}"
            f" — text fallback[/yellow]"
        )
        return _extract_text_page(pg)


def _extract_mixed_page(
    pg: PageData,
    model: str,
    images_dir: Path | None = None,
) -> str:
    """mixed: PyMuPDF text + pdfplumber tables + region vision for image blocks."""
    md = _extract_text_page(pg)
    for i, r in enumerate(pg.regions):
        try:
            desc = vision_tools.region_describe(r.image, r.label, model=model)
            model_router.mark_success(model)
            if images_dir is not None:
                rel = _save_region(r.image, images_dir, r.page_num, r.label, i)
                md += f"\n\n![{r.label}]({rel})\n\n{desc}"
            else:
                md += f"\n\n{desc}"
        except Exception as exc:
            md += (
                f"\n\n<!-- region {i}: {r.label} — vision failed: {type(exc).__name__} -->"
            )
    return md


def _extract_by_route(
    pages: list[PageData],
    route_map: dict[int, str],
    vision_available: bool,
    on_page_done=None,
    images_dir: Path | None = None,
) -> str:
    """
    Phase 3: dispatch each page to its extraction strategy based on RouteMap.

    text_rich + vision  → _extract_text_page_vision  (full_page_extract assigns ##/### headings)
    image_heavy + vision → _extract_vision_page       (full_page_extract)
    mixed + vision       → _extract_mixed_page        (text + region vision)
    table_heavy          → _extract_table_page        (pdfplumber tables)
    scanned              → _extract_scanned_page      (Tesseract OCR)
    no-vision fallback   → _extract_text_page         (pdfplumber text)
    """
    parts: list[str] = []
    model = model_router.get_vision_model() if vision_available else ""

    for pg in pages:
        page_type = route_map.get(pg.page_num, "text_rich")

        if page_type == "scanned":
            md = _extract_scanned_page(pg)
        elif page_type == "image_heavy" and vision_available:
            md = _extract_vision_page(pg, model)
        elif page_type == "mixed" and vision_available:
            md = _extract_mixed_page(pg, model, images_dir=images_dir)
        elif page_type == "table_heavy":
            md = _extract_table_page(pg)
        elif page_type == "text_rich" and vision_available:
            # Vision reads visual layout → headings embedded in output, no post-hoc hint pass
            md = _extract_text_page_vision(pg, model, images_dir=images_dir)
        else:
            # No vision or unsupported type: pdfplumber text only
            md = _extract_text_page(pg)
            if page_type in ("image_heavy", "mixed") and not vision_available:
                for i, r in enumerate(pg.regions):
                    md += f"\n\n<!-- image region {i}: {r.label} (vision unavailable) -->"

        parts.append(md)
        if on_page_done is not None:
            on_page_done(pg.page_num, page_type)

    return "\n\n---\n\n".join(parts)


# ── Phase 4: FORMAT step ──────────────────────────────────────────────────────

_FORMAT_SYSTEM_BODY = """\
You are a document formatter. The input is already structured markdown extracted from a PDF \
by a vision model — headings (## and ###) are already present from the visual layout.
Your job is to clean and consolidate, not to reconstruct structure.

Rules:

1. DEDUPLICATE — The input may contain this marker:
   <!-- TABLES: structured form of page content — use these, remove any duplicate prose above -->
   When you see it: keep the TABLE version, delete the prose above the marker that duplicates it.
   Never output the same information as both bullets/prose AND as a table.

2. HEADINGS — Preserve ## and ### headings exactly as they appear. Do not re-level or remove them.

3. MARKDOWN TABLES — Ensure correct syntax: header row, then | --- | --- | separator, then data rows.
   Use <br> for multi-line content inside cells.

4. ABBREVIATIONS — Merge ALL abbreviation lists found anywhere in the document into ONE table at the end:
   | Abbreviation | Definition |
   | --- | --- |
   Remove the original scattered lists; keep only this single merged table.

5. Preserve ALL unique content. Do not summarise, paraphrase, or omit any information.
6. Output ONLY the formatted markdown — no preamble, no closing remarks."""

_NO_THINK_PREFIX = "/no_think\n"


def _format_system_prompt() -> str:
    """Prepend /no_think for qwen3 models (suppresses thinking chain)."""
    if "qwen3" in ORCHESTRATOR_MODEL.lower():
        return _NO_THINK_PREFIX + _FORMAT_SYSTEM_BODY
    return _FORMAT_SYSTEM_BODY


def _run_format_session(raw_content: str) -> str:
    """
    Phase 4: qwen3:8b cleans and consolidates pre-structured markdown (D20).
    Content already has ##/### headings from vision extraction — FORMAT deduplicates and tidies.
    Falls back to raw_content on failure, timeout, or content-loss (D5).
    """
    char_cap = FORMAT_NUM_CTX * 3   # rough chars that fit within token budget
    content_in = raw_content[:char_cap]
    truncated = len(raw_content) > char_cap

    user_msg = f"Clean and consolidate this extracted document:\n\n{content_in}"

    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            resp = ollama.chat(
                model=ORCHESTRATOR_MODEL,
                messages=[
                    {"role": "system", "content": _format_system_prompt()},
                    {"role": "user", "content": user_msg},
                ],
                options={"temperature": 0.1, "num_ctx": FORMAT_NUM_CTX},
                keep_alive=MODEL_KEEP_ALIVE,
            )
            result_q.put(("ok", resp))
        except Exception as exc:
            result_q.put(("err", exc))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        kind, value = result_q.get(timeout=AGENT_TIMEOUT)
    except queue.Empty:
        console.print("  [yellow]FORMAT timeout — using raw content[/yellow]")
        return raw_content

    if kind == "err":
        console.print(f"  [yellow]FORMAT error: {value} — using raw content[/yellow]")
        return raw_content

    formatted = (value.message.content or "").strip()
    if not formatted:
        return raw_content

    if not _content_loss_ok(content_in, formatted):
        console.print("  [yellow]FORMAT content-loss guard — output too short, reverting to raw[/yellow]")
        return raw_content

    if truncated:
        formatted += "\n\n" + raw_content[char_cap:]

    return formatted


# ── Content-loss guard ────────────────────────────────────────────────────────

def _content_loss_ok(original: str, updated: str) -> bool:
    if not original:
        return True
    return len(updated) >= len(original) * (1 - CONTENT_LOSS_LIMIT)



# ── Output paths ──────────────────────────────────────────────────────────────

def _output_path(pdf_path: Path) -> Path:
    """data/raw/cardiology/heart_failure.pdf → data/markdown/cardiology/heart_failure.md"""
    parts = pdf_path.parts
    try:
        raw_idx = next(i for i, p in enumerate(parts) if p == "raw")
        specialty = parts[raw_idx + 1]
        out_dir = MD_DIR / specialty
    except (StopIteration, IndexError):
        out_dir = MD_DIR

    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / (pdf_path.stem + ".md")


def _confidence_path(md_path: Path) -> Path:
    return md_path.with_name(md_path.stem + "_confidence.md")


def _review_path(md_path: Path) -> Path:
    return md_path.with_name(md_path.stem + "_review.md")


def _images_dir(pdf_path: Path) -> Path:
    """Return {output_dir}/{stem}_images/ for region crop PNGs. Created lazily on first save."""
    out = _output_path(pdf_path)
    return out.parent / f"{out.stem}_images"


def _save_region(
    img: Image.Image,
    images_dir: Path,
    page_num: int,
    label: str,
    idx: int,
) -> str:
    """Save a region crop as PNG. Returns the relative markdown path (images_dir.name/filename)."""
    images_dir.mkdir(parents=True, exist_ok=True)
    filename = f"p{page_num + 1}_{label}_{idx}.png"
    img.save(str(images_dir / filename), format="PNG")
    return f"{images_dir.name}/{filename}"


# ── Confidence report ─────────────────────────────────────────────────────────

def _build_confidence_report(page_scores: list[quality_judge.PageScore], pdf_name: str) -> str:
    lines = [
        f"# Confidence Report — {pdf_name}",
        "",
        "| Page | Confidence | Score | Notes |",
        "|---|---|---|---|",
    ]
    for ps in sorted(page_scores, key=lambda x: x.page_num):
        notes = "; ".join(ps.gaps[:2]) if ps.gaps and ps.confidence != "High" else "—"
        lines.append(f"| {ps.page_num + 1} | {ps.confidence} | {ps.score:.1f} | {notes} |")
    return "\n".join(lines)


# ── Vision probe ──────────────────────────────────────────────────────────────

def _probe_vision() -> bool:
    """Try VISION_PRIMARY then VISION_FALLBACK. Returns False only if both fail."""
    from cloak.config import VISION_FALLBACK, VISION_PRIMARY
    tiny = Image.new("RGB", (8, 8), color=(255, 255, 255))

    for model in (VISION_PRIMARY, VISION_FALLBACK):
        try:
            vision_tools.full_page_extract(tiny, model=model, timeout=30)
            model_router.mark_success(model)
            console.print(f"  Vision probe: [green]{model}[/green] loaded OK")
            return True
        except vision_tools.VisionCallError:
            console.print(
                f"  Vision probe: [yellow]{model}[/yellow] insufficient RAM — trying next"
            )
        except vision_tools.VisionTimeoutError:
            model_router.mark_success(model)
            console.print(f"  Vision probe: [yellow]{model}[/yellow] slow but loaded")
            return True

    return False


# ── Public API ────────────────────────────────────────────────────────────────

_ROUTE_LABELS = {
    "text_rich":   "vision extraction (headings from visual layout)",
    "table_heavy": "pdfplumber tables",
    "image_heavy": "full-page vision",
    "mixed":       "text + region vision",
    "scanned":     "Tesseract OCR",
}


def parse(pdf_path: Path | str, deep_review: bool = True) -> str:
    """
    Full 9-phase agentic parse pipeline. Returns best-scoring markdown string.
    Writes final.md and confidence_report.md to data/markdown/{specialty}/.
    deep_review: if True, runs Phase 9 (DEEP_REVIEW_MODEL) after pipeline teardown.
    """
    pdf_path = Path(pdf_path)
    file_kb   = pdf_path.stat().st_size // 1024
    console.print(Panel.fit(
        f"[bold cyan]cloak[/bold cyan] — [green]{pdf_path.name}[/green]  "
        f"[dim]{file_kb} KB[/dim]",
        border_style="cyan",
    ))

    ui = _PhaseUI()

    # ── Phase 0: Load ─────────────────────────────────────────────────────────
    ui.begin("0", "Load")
    pages = load_pages(pdf_path)
    images_dir = _images_dir(pdf_path)
    pg_word = "page" if len(pages) == 1 else "pages"
    ui.done(f"{len(pages)} {pg_word} · {file_kb} KB")

    # ── Phase 1: Profile (heuristic, no model) ────────────────────────────────
    ui.begin("1", "Profile")
    profiles  = profile_all(pages)
    route_map = build_route_map(profiles)
    counts    = summarise_profiles(profiles)
    profile_summary = "  ".join(
        f"{ptype}×{n}" for ptype, n in sorted(counts.items())
    )
    ui.done(profile_summary)

    # ── Phase 2: Routing ──────────────────────────────────────────────────────
    ui.begin("2", "Route")
    model_router.reset()
    vision_available = _probe_vision()
    if not vision_available:
        console.print(
            "  [yellow]Vision unavailable — image/diagram regions will be skipped[/yellow]"
        )

    route_parts: list[str] = []
    for ptype, cnt in sorted(counts.items()):
        label = _ROUTE_LABELS.get(ptype, ptype)
        route_parts.append(f"{cnt}× {ptype}")
    ui.done("  ".join(route_parts))

    # ── Phase 3: Extraction — extract-once, D19/D23 ───────────────────────────
    ui.begin("3", "Extract")
    if vision_available:
        model_router.before_vision_phase()

    extract_t0 = time.monotonic()
    with Progress(
        SpinnerColumn(), BarColumn(), MofNCompleteColumn(),
        TextColumn("{task.description}"), console=console,
    ) as p:
        extract_task = p.add_task("Extracting", total=len(pages))

        def _on_page_done(page_num: int, page_type: str) -> None:
            p.update(extract_task, advance=1, description=f"[dim]{page_type}[/dim]")

        raw_content = _extract_by_route(
            pages, route_map, vision_available, _on_page_done, images_dir=images_dir
        )

    extract_elapsed = time.monotonic() - extract_t0
    vision_model = model_router.get_vision_model() if vision_available else "text-only"
    ui._phase_t0 = extract_t0
    ui.done(f"{vision_model} · {len(pages)}/{len(pages)} pages · {len(raw_content):,} chars")

    # Switch to orchestrator for Phase 4
    model_router.before_orchestrator_phase()

    # ── Phase 4: FORMAT once — D20 ────────────────────────────────────────────
    ui.begin("4", "Format")
    fmt_t0 = time.monotonic()
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        p.add_task("Formatting ...", total=None)
        markdown = _run_format_session(raw_content)
    fmt_elapsed = time.monotonic() - fmt_t0

    guard_ok = _content_loss_ok(raw_content, markdown)
    guard_str = "" if guard_ok else "  [yellow]⚠ guard triggered — raw kept[/yellow]"
    ui._phase_t0 = fmt_t0
    ui.done(
        f"{len(raw_content):,} → {len(markdown):,} chars{guard_str}",
        warn=not guard_ok,
    )

    # ── Text-only path: skip judge-patch loop ─────────────────────────────────
    if not vision_available:
        out_path = _output_path(pdf_path)
        out_path.write_text(markdown, encoding="utf-8")

        ui.begin("8", "Output")
        ui.done(f"{out_path.name}")

        model_router.teardown_pdf()

        if deep_review:
            _run_phase9(pdf_path, pages, markdown, out_path, ui)

        return markdown

    # ── Phases 5–6: Judge + Patch loop — no re-extraction (D19) ──────────────
    best = RoundResult(
        round_num=0, markdown=markdown, score=0.0, gaps=[], action="patch", page_scores=[]
    )
    messages: list[dict] = [{"role": "system", "content": _PATCH_SYSTEM}]

    for round_num in range(1, MAX_ROUNDS + 1):
        ui.round_header(round_num, MAX_ROUNDS)

        # Phase 5: Judge
        model_router.before_vision_phase()
        judge_t0 = time.monotonic()
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
            p.add_task("Judging pages ...", total=None)
            page_scores = [
                quality_judge.judge(
                    page_num=pg.page_num,
                    page_image=pg.image,
                    extracted_md=markdown,
                    round_num=round_num,
                    model=model_router.get_vision_model(),
                )
                for pg in pages
            ]
            avg_score, all_gaps, action = quality_judge.aggregate_page_results(page_scores)

        if avg_score > best.score:
            best = RoundResult(round_num, markdown, avg_score, all_gaps, action, page_scores)

        ui.score_line(avg_score, len(all_gaps), action, QUALITY_THRESHOLD,
                      time.monotonic() - judge_t0)

        if best.score >= QUALITY_THRESHOLD:
            break
        if action == "accept" or round_num == MAX_ROUNDS:
            break

        # Phase 6: Patch
        model_router.before_orchestrator_phase()
        patch_t0 = time.monotonic()
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
            p.add_task(f"Patching {len(all_gaps)} gap(s) ...", total=None)
            messages = context_manager.compress_history(messages)
            updated = _run_patch_loop(pages, markdown, all_gaps, messages, images_dir=images_dir)

        if not _content_loss_ok(markdown, updated):
            console.print(
                f"  [red]Content-loss guard triggered "
                f"({len(markdown):,}→{len(updated):,} chars) — reverting[/red]"
            )
        else:
            markdown = updated

        ui.patch_line(len(best.markdown), len(markdown), time.monotonic() - patch_t0)

    # ── Phase 8: Write output ─────────────────────────────────────────────────
    ui.begin("8", "Output")
    out_path  = _output_path(pdf_path)
    conf_path = _confidence_path(out_path)
    out_path.write_text(best.markdown, encoding="utf-8")
    conf_path.write_text(
        _build_confidence_report(best.page_scores, pdf_path.name), encoding="utf-8"
    )
    saved_count = len(list(images_dir.glob("*.png"))) if images_dir.exists() else 0
    images_str  = f"  {saved_count} image(s)" if saved_count else ""
    ui.done(
        f"score {best.score:.1f}/10 · round {best.round_num}"
        f"  →  {out_path.name}{images_str}"
    )

    model_router.teardown_pdf()

    if deep_review:
        _run_phase9(pdf_path, pages, best.markdown, out_path, ui)

    return best.markdown


def _run_phase9(
    pdf_path: Path,
    pages: list,
    final_markdown: str,
    out_path: Path,
    ui: _PhaseUI,
) -> None:
    """Phase 9: post-pipeline deep review (gemma4:latest). Always unloads model when done."""
    from cloak.config import DEEP_REVIEW_MODEL
    from cloak.quality import deep_review as dr

    ui.begin("9", "Deep Review")
    console.print(f"       [dim]Loading {DEEP_REVIEW_MODEL} (CPU+GPU split) ...[/dim]")

    rev_path = dr.run(
        pdf_path=pdf_path,
        pages=pages,
        final_markdown=final_markdown,
        review_out=_review_path(out_path),
        console=console,
    )
    if rev_path:
        ui.done(f"{rev_path.name}")
    else:
        ui.done("skipped", skip=True)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("[red]Usage: python -m cloak.orchestration.parser_agent <pdf_path>[/red]")
        sys.exit(1)

    result = parse(sys.argv[1])
    console.print(f"\nMarkdown length: {len(result)} chars")
