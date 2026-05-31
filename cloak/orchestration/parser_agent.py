"""
parser_agent.py — 9-phase agentic PDF parse pipeline.

Phases:
  0  load_pages          — PyMuPDF + pdfplumber, no model
  1  doc intelligence    — docling layout pass → DoclingPageMap (D29)
                           heuristic page profiles → DocProfile → ParsePlan (D28)
  2  model staging       — probe vision based on ParsePlan.model_tier (D28)
  3  _extract_by_route   — docling path when available (D29); fallback: type-based (D23)
  4  _run_format_session — qwen3:8b FORMAT once (D20)
  5  quality_judge       — vision scores sampled pages; combined content+structure (D31)
  6  _run_patch_loop     — qwen3:8b fills gaps
  5–6 repeat up to ParsePlan.max_rounds; judge+patch only (D19)
  8  write final.md + confidence_report.md + flagged.md
  9  deep_review         — DEEP_REVIEW_MODEL audits final markdown (D27)

Hard rules: D2 (best round wins), D3 (threshold 8.0), D5 (content-loss guard),
D6 (context cap), D19 (extract-once), D20 (FORMAT before patch), D28 (ParsePlan drives all).
"""
from __future__ import annotations

import json
import queue
import re
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
    FORMAT_TIMEOUT,
    JUDGE_SKIP_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD,
    MAX_AGENT_ITERS,
    MAX_ROUNDS,
    MD_DIR,
    MODEL_KEEP_ALIVE,
    MODEL_NUM_CTX,
    ORCHESTRATOR_MODEL,
    QUALITY_THRESHOLD,
    VISION_PRIMARY,
)
from cloak.extraction.pdf_tools import PageData, load_pages
from cloak.orchestration import context_manager, model_router
from cloak.profiling.doc_profiler import (
    DoclingElement,
    DoclingPageMap,
    build_doc_profile,
    build_parse_plan,
    run_docling_pass,
)
from cloak.profiling.page_profiler import (
    build_route_map,
    profile_all,
    update_vision_from_docling,
)
from cloak.profiling.page_profiler import summarise as summarise_profiles
from cloak.quality import quality_judge
from cloak.quality.quality_judge import QualityMetrics, compute_metrics
from cloak.quality import postprocess
from cloak import registry as _registry
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
            "name": "get_page_elements",
            "description": (
                "Return the docling structural element map for a page: labels, heading levels, "
                "and text. Use this to see the page skeleton (headings, tables, figures, "
                "footnotes) before deciding which regions need patching."
            ),
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
            "name": "finish",
            "description": "Signal that all patches are done. Call this when you have filled all gaps. No arguments needed — the draft is updated automatically by patch_section/add_section.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
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
    element_map: DoclingPageMap | None = None,
) -> tuple[str, str]:
    """Execute a tool call from the orchestrator. Returns (result_text, updated_draft)."""
    if name == "get_page_elements":
        page_num = int(args.get("page_num", 0))
        if element_map is None:
            return "Docling element map not available for this document.", draft
        elements = element_map.get(page_num)
        if not elements:
            return f"No docling elements found for page {page_num}.", draft
        lines = [f"Page {page_num} elements ({len(elements)} total):"]
        for el in elements:
            if el.label == "section_header":
                lines.append(f"  - section_header [L{el.level}]: {el.text[:120]!r}")
            elif el.label == "table":
                rows = el.table_md.count("\n") if el.table_md else 0
                lines.append(f"  - table: ({rows} rows markdown)")
            elif el.label == "picture":
                cap = f" caption={el.caption!r}" if el.caption else ""
                lines.append(f"  - picture{cap}")
            elif el.label == "footnote":
                lines.append(f"  - footnote: {el.text[:80]!r}")
            else:
                lines.append(f"  - {el.label}: {el.text[:120]!r}")
        return "\n".join(lines), draft

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
        content = _strip_think_artifacts(args.get("content", ""))
        new_draft = _replace_section(draft, heading, content)
        return f"Section '{heading}' patched.", new_draft

    if name == "add_section":
        heading = args.get("heading", "")
        content = _strip_think_artifacts(args.get("content", ""))
        new_draft = draft + f"\n\n## {heading}\n\n{content}"
        return f"Section '{heading}' added.", new_draft

    if name == "finish":
        return "__FINISH__", draft

    return f"Unknown tool: {name}", draft


def _replace_section(markdown: str, heading: str, content: str) -> str:
    pattern = rf"(##\s+{re.escape(heading)}\s*\n)(.*?)(?=\n##\s|\Z)"
    replacement = rf"\g<1>{content}\n"
    new = re.sub(pattern, replacement, markdown, flags=re.DOTALL | re.IGNORECASE)
    return new if new != markdown else markdown + f"\n\n## {heading}\n\n{content}"


# ── Agent tool-calling loop (Phase 6) ────────────────────────────────────────

_PATCH_SYSTEM = """\
You are patching gaps in an extracted PDF document.
Use patch_section() or add_section() to fill missing content retrieved via get_page_text() or get_page_elements().
Do NOT remove or rewrite existing content — only add what is missing.
When all gaps are filled, call finish() with no arguments."""


def _run_patch_loop(
    pages: list[PageData],
    draft: str,
    gaps: list[str],
    messages: list[dict],
    images_dir: Path | None = None,
    element_map: DoclingPageMap | None = None,
) -> str:
    """Run the qwen3:8b tool-calling loop to fill gaps. Returns updated markdown."""
    gap_text = "\n".join(f"- {g}" for g in gaps[:20])
    headings = [ln.lstrip("#").strip() for ln in draft.splitlines() if ln.startswith("##")]
    heading_outline = "\n".join(f"  {h}" for h in headings[:40]) if headings else "  (no headings)"
    messages = list(messages)
    messages.append({
        "role": "user",
        "content": (
            f"The quality judge found these gaps in the current extraction:\n{gap_text}\n\n"
            f"Document sections available for patch_section():\n{heading_outline}\n\n"
            f"Use get_page_text() or get_page_elements() to retrieve missing content, "
            f"then call patch_section() or add_section() to fill each gap. "
            f"Call finish() with no arguments when done."
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
                    options=_orchestrator_options(think=True, num_ctx=MODEL_NUM_CTX),
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
            from cloak.vision import vision_tools as _vt_patch
            reason = _vt_patch._stall_reason(ORCHESTRATOR_MODEL, 0, AGENT_TIMEOUT)
            console.print(
                f"  [yellow]Patch timeout iter {iteration + 1} — {reason}[/yellow]"
            )
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

            tool_result, current_draft = _execute_tool(
                fn_name, fn_args, pages, current_draft, images_dir, element_map
            )
            messages.append({"role": "tool", "content": tool_result})

            if tool_result == "__FINISH__":
                return current_draft

    return current_draft


# ── Docling extraction helpers ────────────────────────────────────────────────

def _crop_normalized(
    img: Image.Image,
    bbox_norm: tuple[float, float, float, float],
) -> Image.Image | None:
    """Return a crop of img using normalised [0,1] bbox (l, t, r, b). None if degenerate."""
    l, t, r, b = bbox_norm
    if r <= l or b <= t:
        return None
    w, h = img.size
    crop = img.crop((int(l * w), int(t * h), int(r * w), int(b * h)))
    if crop.width < 4 or crop.height < 4:
        return None
    return crop


def _extract_docling_page(
    elements: list[DoclingElement],
    pg: PageData,
    vision_available: bool,
    model: str,
    images_dir: Path | None = None,
    use_math_ocr: bool = False,
) -> str:
    """
    Build structured markdown from a docling element map for one page (D29).
    PageHeader/PageFooter are never in elements — discarded by run_docling_pass.
    Figure regions: cropped using normalised bbox → vision region_describe.
    Footnotes collected and appended at section end with --- separator.
    """
    parts: list[str] = []
    footnotes: list[str] = []
    fig_idx = 0

    for el in elements:
        label = el.label

        if label == "title":
            if el.text.strip():
                parts.append(f"# {el.text}")

        elif label == "section_header":
            level = max(1, el.level or 1)
            hashes = "#" * (level + 1)   # L1→##, L2→###, L3→####
            if el.text.strip():
                parts.append(f"{hashes} {el.text}")

        elif label == "table":
            table_content = el.table_md.strip()
            # GLM-OCR: try bbox crop for better complex-table extraction (D45)
            if pg.image is not None and el.bbox_norm:
                crop = _crop_normalized(pg.image, el.bbox_norm)
                if crop is not None:
                    from cloak.extraction.ocr_tools import extract_table_glm
                    glm_result = extract_table_glm(crop)
                    if len(glm_result) > len(table_content):
                        table_content = glm_result
            if table_content:
                parts.append(table_content)

        elif label == "picture":
            if vision_available and pg.image is not None:
                crop = _crop_normalized(pg.image, el.bbox_norm)
                if crop is not None:
                    try:
                        desc = vision_tools.region_describe(crop, "figure", model=model)
                        model_router.mark_success(model)
                        if images_dir is not None:
                            rel = _save_region(crop, images_dir, pg.page_num, "figure", fig_idx)
                            block = f"![figure]({rel})\n\n{desc}"
                        else:
                            block = desc
                        if el.caption:
                            block += f"\n\n*{el.caption}*"
                        parts.append(block)
                    except Exception as exc:
                        parts.append(
                            f"<!-- figure {fig_idx}: vision failed ({type(exc).__name__}) -->"
                        )
                else:
                    parts.append(f"<!-- figure {fig_idx}: degenerate bbox -->")
            else:
                caption = el.caption or f"figure {fig_idx}"
                parts.append(f"<!-- figure: {caption} (vision unavailable) -->")
            fig_idx += 1

        elif label == "footnote":
            if el.text.strip():
                footnotes.append(el.text)

        elif label == "formula":
            rendered = ""
            if use_math_ocr and pg.image is not None:
                crop = _crop_normalized(pg.image, el.bbox_norm)
                if crop is not None:
                    from cloak.extraction import math_ocr
                    rendered = math_ocr.ocr_equation(crop)
            if rendered:
                parts.append(f"$$\n{rendered}\n$$")
            elif el.text.strip():
                parts.append(f"`{el.text}`")

        else:
            # text, paragraph, list_item, reference, caption, code, etc.
            if el.text.strip():
                parts.append(el.text)

    if footnotes:
        parts.append("---\n**Footnotes**")
        parts.extend(f"- {f}" for f in footnotes)

    return "\n\n".join(p for p in parts if p.strip())


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


def _extract_poster_page(pg: PageData, model: str) -> str:
    """
    D51: full-page VLM extraction for clinical flowcharts and poster-format PDFs.
    Bypasses docling and pdfplumber — the VLM reads the rendered page image and
    follows the visual branching structure that pdfplumber cannot reconstruct.
    Falls back to pdfplumber text on vision failure.
    """
    if pg.image is None:
        return _extract_text_page(pg)
    try:
        md = vision_tools.poster_page(pg.image, model=model)
        model_router.mark_success(model)
        return md
    except (vision_tools.VisionTimeoutError, vision_tools.VisionCallError) as exc:
        console.print(
            f"  [yellow]Poster vision failed page {pg.page_num}: {type(exc).__name__}"
            f" — text fallback[/yellow]"
        )
        return _extract_text_page(pg)


def _extract_slide_page(
    pg: PageData,
    model: str,
    images_dir: Path | None = None,
) -> str:
    """D38: slide deck mode — send full page image with slide-specific prompt."""
    if pg.image is None:
        return _extract_text_page(pg)
    try:
        md = vision_tools.slide_page(pg.image, model=model)
        model_router.mark_success(model)
        if images_dir is not None:
            rel = _save_region(pg.image, images_dir, pg.page_num, "slide", 0)
            md = f"![slide]({rel})\n\n{md}"
        return md
    except (vision_tools.VisionTimeoutError, vision_tools.VisionCallError) as exc:
        console.print(
            f"  [yellow]Slide vision failed page {pg.page_num}: {type(exc).__name__}"
            f" — text fallback[/yellow]"
        )
        return _extract_text_page(pg)


def _extract_exam_page(
    pg: PageData,
    model: str,
) -> str:
    """
    D39: exam_mode — full-page VLM extraction for JEE/GATE/ESE question paper pages.
    Docling text is bypassed because Symbol-font math is fragmented and unreadable.
    """
    if pg.image is not None:
        try:
            md = vision_tools.exam_page(pg.image, model=model)
            model_router.mark_success(model)
            return md
        except (vision_tools.VisionTimeoutError, vision_tools.VisionCallError) as exc:
            console.print(
                f"  [yellow]Exam vision failed page {pg.page_num}: {type(exc).__name__}"
                f" — text fallback[/yellow]"
            )
    return _extract_text_page(pg)


def _extract_table_page(pg: PageData) -> str:
    """table_heavy: pdfplumber tables only — raw text excluded to prevent duplication."""
    table_mds = [tbl.to_markdown() for tbl in pg.tables if tbl.to_markdown().strip()]
    return "\n\n".join(table_mds) if table_mds else pg.text.strip()


def _extract_scanned_page(pg: PageData) -> str:
    """scanned: Surya OCR primary, Tesseract fallback. Falls back to raw PyMuPDF text on OCRError (D30)."""
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


def _detect_poster(pages: list[PageData], element_map: DoclingPageMap | None) -> bool:
    """
    D51: detect clinical flowchart / poster-format documents.

    Signal: short doc (<=5 pages) where pdfplumber extracts substantial text but
    docling finds very few structured text elements per page. This mismatch
    indicates a complex visual layout (flowchart boxes + arrows as PDF vector art)
    that docling cannot parse but pdfplumber can partially read out-of-order.

    Sending these pages through full-page VLM extraction with a specialized
    poster prompt produces correct clinical content with proper branching structure.
    """
    if len(pages) > 5:
        return False
    if element_map is None:
        return False
    for pg in pages:
        elements = element_map.get(pg.page_num, [])
        text_elements = [
            e for e in elements
            if e.label in ("text", "section_header", "list_item", "paragraph")
        ]
        # Lots of pdfplumber text but very few docling text elements = visual flowchart layout
        if len(pg.text) > 800 and len(text_elements) < 8:
            return True
    return False


def _detect_exam_paper(pages: list[PageData]) -> bool:
    """
    D39: heuristic check — True when the document looks like JEE/GATE/ESE exam paper.
    Samples the first 5 pages (covers instructions + first question block).
    """
    import re
    _EXAM_RE = re.compile(
        r'Q\.?\s*\d+\b'                          # Q.1 / Q1
        r'|Maximum\s+Marks'                       # exam header
        r'|GATE\s+\d{4}'                          # GATE 2024
        r'|JEE.{0,20}(?:Advanced|Main)'           # JEE Advanced / JEE Main
        r'|ESE\s+\d{4}|IES\s+\d{4}'             # ESE 2023 / IES 2023
        r'|UPSC\s+(?:ESE|IES)'                   # UPSC ESE
        r'|(?:PART|PAPER)\s+[A-Z0-9]\b.*Marks',  # PART A — 20 Marks (specific)
        re.IGNORECASE,
    )
    sample = pages[:5]
    for pg in sample:
        if _EXAM_RE.search(pg.text):
            return True
    return False


def _extract_by_route(
    pages: list[PageData],
    route_map: dict[int, str],
    vision_available: bool,
    on_page_done=None,
    images_dir: Path | None = None,
    element_map: DoclingPageMap | None = None,
    use_math_ocr: bool = False,
    slide_mode: bool = False,
    exam_mode: bool = False,
    poster_mode: bool = False,
) -> str:
    """
    Phase 3: dispatch each page to its extraction strategy.

    Priority order:
      poster_mode (D51)   → _extract_poster_page     (full VLM with flowchart prompt)
      exam_mode (D39)     → _extract_exam_page        (full VLM with math prompt)
      slide_mode (D38)    → _extract_slide_page       (full VLM with slide prompt)
      docling path (D29)  → _extract_docling_page     (heading hierarchy + figure crops)
      scanned             → _extract_scanned_page     (surya→tesseract OCR, D30)
      image_heavy+vision  → _extract_vision_page      (full_page_extract)
      mixed+vision        → _extract_mixed_page       (text + region vision)
      table_heavy         → _extract_table_page       (pdfplumber tables)
      text_rich+vision    → _extract_text_page_vision (full_page_extract)
      fallback            → _extract_text_page        (pdfplumber text only)
    """
    parts: list[str] = []
    model = model_router.get_vision_model() if vision_available else ""

    for pg in pages:
        page_type = route_map.get(pg.page_num, "text_rich")

        # D51: poster_mode — full VLM extraction, bypasses docling and pdfplumber entirely
        if poster_mode and vision_available:
            md = _extract_poster_page(pg, model)

        # D39: exam_mode — bypass docling text for text_rich/mixed; use Mathpix or vision
        elif exam_mode and page_type in ("text_rich", "mixed") and (vision_available or True):
            md = _extract_exam_page(pg, model)

        # D38: slide mode overrides docling for image_heavy/mixed pages — per-slide VLM prompt
        elif slide_mode and vision_available and page_type in ("image_heavy", "mixed"):
            md = _extract_slide_page(pg, model, images_dir=images_dir)

        # Docling path: structured extraction for all non-scanned pages (D29)
        elif element_map is not None and pg.page_num in element_map and page_type != "scanned":
            md = _extract_docling_page(
                element_map[pg.page_num], pg, vision_available, model, images_dir,
                use_math_ocr=use_math_ocr,
            )
            # Gap A: if docling yielded nothing (TOC, complex layouts), fall back to pdfplumber
            if not md.strip():
                md = _extract_text_page(pg)
            # Gap C: if docling text is garbled glyph codes, fall back to vision extraction
            elif _is_garbled(md) and vision_available:
                md = _extract_vision_page(pg, model)

        elif page_type == "scanned":
            # Always use OCR for scanned pages — docling's do_ocr=False means no text there (D30)
            md = _extract_scanned_page(pg)

        elif page_type == "image_heavy" and vision_available:
            md = _extract_vision_page(pg, model)

        elif page_type == "mixed" and vision_available:
            md = _extract_mixed_page(pg, model, images_dir=images_dir)

        elif page_type == "table_heavy":
            md = _extract_table_page(pg)

        elif page_type == "text_rich" and vision_available:
            md = _extract_text_page_vision(pg, model, images_dir=images_dir)

        else:
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

5. UNWRAP CODE FENCES — If content appears inside ```markdown...``` or ``` blocks that is NOT actual
   code (commands, scripts, source code), remove the fence markers and include the content as regular
   markdown. Only keep code fences around genuine code samples.

6. Preserve ALL unique content. Do not summarise, paraphrase, or omit any information.
7. Output ONLY the formatted markdown — no preamble, no closing remarks."""

_NO_THINK_PREFIX = "/no_think\n"

_THINK_BLOCK_RE   = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_INLINE_RE  = re.compile(r"\s*/think\s*$", re.MULTILINE)


def _strip_think_artifacts(text: str) -> str:
    """Remove qwen3/gemma4 thinking-chain artifacts that leak into responses."""
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_INLINE_RE.sub("", text)
    return text.strip()


def _orchestrator_options(think: bool, num_ctx: int) -> dict:
    """Build Ollama options for the orchestrator. Adds think flag for gemma4/qwen3 (D49)."""
    opts: dict = {"temperature": 0.1, "num_ctx": num_ctx}
    model_lower = ORCHESTRATOR_MODEL.lower()
    if "gemma4" in model_lower or "qwen3" in model_lower:
        opts["think"] = think
    return opts


_LEADER_DOT_RE   = re.compile(r"(\n\.){3,}", re.MULTILINE)
_GLYPH_CODE_RE   = re.compile(r"/g[0-9a-f]{2,4}", re.IGNORECASE)


def _is_garbled(text: str) -> bool:
    """Detect PDF glyph-code encoding artifacts (/gXX patterns) — Gap C."""
    tokens = text.split()
    if len(tokens) < 5:
        return False
    glyph_count = sum(1 for t in tokens if _GLYPH_CODE_RE.match(t))
    return glyph_count / len(tokens) > 0.25


def _clean_output_artifacts(text: str) -> str:
    """Remove visual PDF artifacts that pollute markdown output."""
    # Leader dot sequences
    text = _LEADER_DOT_RE.sub("\n", text)
    text = re.sub(r"^\.$", "", text, flags=re.MULTILINE)   # Gap D: standalone dot lines
    # <math> watermark: "Digitized by Google" rendered as MathJax by vision model
    text = re.sub(r"<math[^>]*>\\mathsf\{Digitized\}.*?</math>", "", text, flags=re.DOTALL)
    text = re.sub(r"<math[^>]*>.*?Digitized.*?</math>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Collapse 4+ consecutive blank lines to 2
    text = re.sub(r"\n{4,}", "\n\n", text)
    return text


def _format_system_prompt() -> str:
    """Prepend /no_think for qwen3 models. gemma4 uses think=False in options instead."""
    if "qwen3" in ORCHESTRATOR_MODEL.lower():
        return _NO_THINK_PREFIX + _FORMAT_SYSTEM_BODY
    return _FORMAT_SYSTEM_BODY


def _content_needs_format(content: str) -> bool:
    """
    Heuristic: does this content actually need FORMAT cleanup?
    Skip FORMAT for already-clean docling text output — it's expensive and adds nothing.
    Only run FORMAT when specific issues are detected.
    """
    # Code fences from VLM (e.g. ```markdown ... ```)
    if "```" in content:
        return True
    # Think artifacts not yet stripped (safety net)
    if "/think" in content or "<think>" in content:
        return True
    # Heading level skips (e.g. ## followed directly by ####)
    if re.search(r"^##[^#].*\n+####[^#]", content, re.MULTILINE):
        return True
    # Consecutive blank H2 headings (docling artifact from page headers)
    if re.search(r"^## \w+\n\n## \w+\n\n## \w+", content, re.MULTILINE):
        return True
    # No structural issues detected — skip FORMAT
    return False


def _run_format_session(raw_content: str) -> str:
    """
    Phase 4: qwen3:8b cleans and consolidates pre-structured markdown (D20).
    Content already has ##/### headings from vision extraction — FORMAT deduplicates and tidies.
    Falls back to raw_content on failure, timeout, or content-loss (D5).
    Skipped entirely if content_needs_format() returns False (saves 150-800s for clean docs).
    """
    if not _content_needs_format(raw_content):
        return raw_content

    char_cap = FORMAT_NUM_CTX * 3   # rough chars that fit within token budget
    content_in = raw_content[:char_cap]
    truncated = len(raw_content) > char_cap

    user_msg = f"Clean and consolidate this extracted document:\n\n{content_in}"

    result_q: queue.Queue = queue.Queue()

    from cloak.vision import vision_tools as _vt_fmt

    chunks: list[str] = []
    token_count = 0
    last_token_at = [time.monotonic()]
    error_holder: list[Exception | None] = [None]
    done_event = threading.Event()
    fmt_start = time.monotonic()

    def _worker() -> None:
        nonlocal token_count
        try:
            for chunk in ollama.chat(
                model=ORCHESTRATOR_MODEL,
                messages=[
                    {"role": "system", "content": _format_system_prompt()},
                    {"role": "user",   "content": user_msg},
                ],
                options=_orchestrator_options(think=False, num_ctx=FORMAT_NUM_CTX),
                keep_alive=MODEL_KEEP_ALIVE,
                stream=True,
            ):
                piece = chunk.message.content or ""
                if piece:
                    chunks.append(piece)
                    token_count += 1
                    last_token_at[0] = time.monotonic()
        except Exception as exc:
            error_holder[0] = exc
        finally:
            done_event.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    while not done_event.wait(timeout=0.5):
        elapsed    = time.monotonic() - fmt_start
        since_last = time.monotonic() - last_token_at[0]

        if elapsed >= FORMAT_TIMEOUT:
            reason = _vt_fmt._stall_reason(ORCHESTRATOR_MODEL, token_count, since_last)
            console.print(f"  [yellow]FORMAT timeout ({reason}) — using raw content[/yellow]")
            return raw_content

        if since_last >= STALL_SECONDS and elapsed > 5:
            reason = _vt_fmt._stall_reason(ORCHESTRATOR_MODEL, token_count, since_last)
            console.print(f"  [yellow]FORMAT stalled — {reason} — using raw content[/yellow]")
            return raw_content

        cb = _vt_fmt._progress_cb
        if cb is not None:
            cb(token_count, elapsed, since_last, "format")

    if error_holder[0] is not None:
        console.print(f"  [yellow]FORMAT error: {error_holder[0]} — using raw content[/yellow]")
        return raw_content

    formatted = _strip_think_artifacts("".join(chunks))
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


def _flagged_path(md_path: Path) -> Path:
    return md_path.with_name(md_path.stem + "_flagged.md")


def _write_flagged_pages(
    page_scores: list[quality_judge.PageScore],
    pages: list,
    pdf_name: str,
    flagged_path: Path,
) -> int:
    """
    Write pages scoring below LOW_CONFIDENCE_THRESHOLD to a flagged report.
    Each entry shows the judge gaps and raw pdfplumber source so the problem is
    immediately visible. Returns number of pages flagged (0 = nothing written).
    """
    low = [ps for ps in page_scores if ps.score < LOW_CONFIDENCE_THRESHOLD]
    if not low:
        if flagged_path.exists():
            flagged_path.unlink()  # clean up stale file from a prior run
        return 0

    low.sort(key=lambda ps: ps.page_num)
    page_nums_str = ", ".join(str(ps.page_num + 1) for ps in low)

    lines = [
        f"# Flagged Pages — {pdf_name}",
        "",
        f"**{len(low)} page(s) scored below {LOW_CONFIDENCE_THRESHOLD}/10 and need review.**  ",
        f"Pages: {page_nums_str}",
        "",
        "---",
        "",
    ]

    for ps in low:
        raw_text = (pages[ps.page_num].text.strip() if ps.page_num < len(pages) else "")
        gaps_md  = "\n".join(f"- {g}" for g in ps.gaps) if ps.gaps else "- (no specific gaps recorded)"

        lines += [
            f"## Page {ps.page_num + 1}  ·  score {ps.score:.1f}/10",
            "",
            "**Gaps identified by judge:**",
            gaps_md,
            "",
            "**Raw source text (pdfplumber):**",
            "",
            "```",
            raw_text or "(no extractable text — image-only page)",
            "```",
            "",
            "---",
            "",
        ]

    flagged_path.write_text("\n".join(lines), encoding="utf-8")
    return len(low)


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

def _build_confidence_report(
    page_scores: list[quality_judge.PageScore],
    pdf_name: str,
    metrics: QualityMetrics | None = None,
) -> str:
    lines = [f"# Confidence Report — {pdf_name}", ""]

    if metrics is not None:
        high_pages = sum(1 for ps in page_scores if ps.score >= QUALITY_THRESHOLD)
        n_pages    = len(page_scores)
        lines += [
            "## Summary",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        if metrics.judged:
            lines.append(f"| Judge Score | {metrics.judge_score:.1f} / 10 |")
            lines.append(
                f"| Coverage | {int(metrics.coverage_rate * 100)}%"
                f" pages ≥ {QUALITY_THRESHOLD} ({high_pages}/{n_pages}) |"
            )
        lines.append(f"| Completeness | {int(metrics.completeness_ratio * 100)}% words captured |")
        lines.append(f"| Structure | {metrics.heading_count} headings · {metrics.table_count} tables |")
        if metrics.review_score is not None:
            lines.append(f"| Review Score | {metrics.review_score:.1f} / 10 ({ORCHESTRATOR_MODEL}) |")
        lines += ["", "---", ""]

    if page_scores:
        lines += [
            "## Per-Page Scores",
            "",
            "| Page | Confidence | Score | Notes |",
            "|---|---|---|---|",
        ]
        for ps in sorted(page_scores, key=lambda x: x.page_num):
            notes = "; ".join(ps.gaps[:2]) if ps.gaps and ps.confidence != "High" else "—"
            lines.append(f"| {ps.page_num + 1} | {ps.confidence} | {ps.score:.1f} | {notes} |")
    else:
        lines.append("*Vision judge did not run — text-only extraction.*")

    return "\n".join(lines)


def _print_metrics_summary(metrics: QualityMetrics) -> None:
    from rich.table import Table as _Table

    def _color(val: float, high: float, mid: float) -> str:
        return "green" if val >= high else ("yellow" if val >= mid else "red")

    tbl = _Table.grid(padding=(0, 3))
    tbl.add_column(style="dim", no_wrap=True)
    tbl.add_column(no_wrap=True)
    tbl.add_column(style="dim", no_wrap=True)
    tbl.add_column(no_wrap=True)

    if metrics.judged:
        jc = _color(metrics.judge_score, 8.0, 5.0)
        js = f"[{jc}]{metrics.judge_score:.1f}/10[/{jc}]"
        cov_pct = int(metrics.coverage_rate * 100)
        cc = _color(metrics.coverage_rate, 0.8, 0.5)
        cs = f"[{cc}]{cov_pct}%[/{cc}] [dim]pages ≥ 8.0[/dim]"
        tbl.add_row("Judge", js, "Coverage", cs)

    comp_pct = int(metrics.completeness_ratio * 100)
    pc = _color(metrics.completeness_ratio, 0.85, 0.6)
    ps_str = f"[{pc}]{comp_pct}%[/{pc}] [dim]words captured[/dim]"
    struct_s = f"[dim]{metrics.heading_count} headings · {metrics.table_count} tables[/dim]"
    tbl.add_row("Complete", ps_str, "Structure", struct_s)

    if metrics.review_score is not None:
        rc = _color(metrics.review_score, 8.0, 5.0)
        rs = f"[{rc}]{metrics.review_score:.1f}/10[/{rc}] [dim]({ORCHESTRATOR_MODEL} deep review)[/dim]"
        tbl.add_row("Review", rs, "", "")

    console.print()
    console.print(tbl)
    console.print()


# ── Vision probe ──────────────────────────────────────────────────────────────

def _probe_vision() -> bool:
    """Try vision models in VRAM-aware order. Returns False only if all fail."""
    # 64x64 minimum — qwen3-vl family crashes on images smaller than ~32x32
    tiny = Image.new("RGB", (64, 64), color=(255, 255, 255))

    for model in model_router.vision_models_to_try():
        try:
            vision_tools.full_page_extract(tiny, model=model, timeout=30)
            model_router.mark_success(model)
            console.print(f"  Vision probe: [green]{model}[/green] loaded OK")
            return True
        except vision_tools.VisionCallError:
            console.print(
                f"  Vision probe: [yellow]{model}[/yellow] failed to load — trying next"
            )
        except vision_tools.VisionTimeoutError:
            model_router.mark_success(model)
            console.print(f"  Vision probe: [yellow]{model}[/yellow] slow but loaded")
            return True

    return False


# ── Public API ────────────────────────────────────────────────────────────────

_ROUTE_LABELS = {
    "text_rich":   "docling structure",
    "table_heavy": "docling tables",
    "image_heavy": "docling + vision figures",
    "mixed":       "docling + vision regions",
    "scanned":     "surya OCR",
}


def parse(
    pdf_path: Path | str,
    deep_review: bool = True,
    workspace: Path | None = None,
) -> str:
    """
    Full 9-phase agentic parse pipeline. Returns best-scoring markdown string.
    Writes final.md, confidence_report.md, and optionally flagged.md.
    workspace: root for .cloak/registry.json (defaults to cwd).
    """
    pdf_path = Path(pdf_path)
    parse_t0 = time.monotonic()
    file_kb  = pdf_path.stat().st_size // 1024

    # Registry: open and mark in-progress before any work starts
    _ws       = (workspace or Path.cwd()).resolve()
    _reg, _ws = _registry.load(_ws)
    _registry.upsert(_reg, pdf_path, _ws,
                     status=_registry.PROCESSING,
                     last_parsed=_registry.now_iso())
    _registry.save(_reg, _ws)

    console.print(Panel.fit(
        f"[bold cyan]cloak[/bold cyan] — [green]{pdf_path.name}[/green]  "
        f"[dim]{file_kb} KB[/dim]",
        border_style="cyan",
    ))

    # Pre-flight: warn if Ollama is not reachable (extraction falls back to pdfplumber only)
    if not model_router.is_ollama_available():
        from cloak.config import OLLAMA_BASE_URL as _OLLAMA_URL
        console.print(
            f"  [yellow]⚠[/yellow]  Ollama not reachable at {_OLLAMA_URL}. "
            "Extraction will use pdfplumber/docling only — no vision, no patching. "
            "For full quality: [bold]ollama serve[/bold]"
        )

    ui = _PhaseUI()

    # ── Phase 0: Load ─────────────────────────────────────────────────────────
    ui.begin("0", "Load")
    pages = load_pages(pdf_path)
    images_dir = _images_dir(pdf_path)
    pg_word = "page" if len(pages) == 1 else "pages"
    ui.done(f"{len(pages)} {pg_word} · {file_kb} KB")

    # ── Phase 1: Doc intelligence — docling layout pass + DocProfile + ParsePlan ─
    ui.begin("1", "Profile")
    profiles  = profile_all(pages)

    # Docling layout pass (D29) — zero Ollama calls, CPU-only
    console.print("       [dim]Running docling layout analysis ...[/dim]")
    element_map = run_docling_pass(pdf_path)
    if element_map:
        update_vision_from_docling(profiles, element_map)  # refine needs_vision (D29)

    route_map         = build_route_map(profiles)
    counts            = summarise_profiles(profiles)

    # Estimate model viability using total memory (VRAM + RAM) before the actual probe
    _est_vram    = model_router._free_vram_gb()
    _est_ram     = model_router._free_ram_gb()
    _primary_sz  = model_router._MODEL_SIZE_GB.get(VISION_PRIMARY, 7.3)
    _gpu_est     = (_est_vram + _est_ram) >= _primary_sz
    doc_profile  = build_doc_profile(profiles, element_map)
    _exam_paper  = _detect_exam_paper(pages)
    _poster      = _detect_poster(pages, element_map)
    plan         = build_parse_plan(doc_profile, primary_viable=_gpu_est, use_docling=element_map is not None, exam_paper=_exam_paper, poster=_poster)

    # D51: poster pages always need VLM judge — override docling's needs_vision=False
    if _poster:
        for p in profiles:
            p.needs_vision = True

    # D33: pages where needs_vision=False after docling refine → heuristic judge
    _needs_vision_map = {p.page_num: p.needs_vision for p in profiles}
    model_router.set_parse_plan(plan)

    type_summary = "  ".join(f"{ptype}×{n}" for ptype, n in sorted(counts.items()))
    docling_str  = f"  [green]docling[/green]:{len(element_map)} pages" if element_map else ""
    flags = []
    if plan.use_math_ocr:
        flags.append("math_ocr")
    if plan.slide_mode:
        flags.append("slide_mode")
    if plan.exam_mode:
        flags.append("exam_mode")
    if plan.poster_mode:
        flags.append("poster_mode")
    flag_str  = ("  [cyan]" + " · ".join(flags) + "[/cyan]") if flags else ""
    plan_str  = f"  [dim][{doc_profile.size_tier} · {plan.max_rounds}r · {int(plan.judge_sample_rate * 100)}%][/dim]"
    ui.done(type_summary + docling_str + flag_str + plan_str)

    # ── Phase 2: Model staging — probe based on ParsePlan.model_tier ──────────
    ui.begin("2", "Route")
    model_router.reset()
    model_router.set_parse_plan(plan)   # re-set after reset()
    # Unload orchestrator BEFORE probing so free-VRAM check is accurate (D43).
    # Orchestrator stays warm between PDFs; without this, the probe sees near-zero
    # free VRAM and always picks 4b even when 8b would fit after the unload.
    if plan.model_tier != "none":
        model_router.before_vision_phase()
    vision_available = _probe_vision()
    if not vision_available:
        if plan.model_tier == "none":
            console.print("  [dim]Vision skipped — document has no image content[/dim]")
        else:
            console.print(
                "  [yellow]Vision unavailable — image/diagram regions will be skipped[/yellow]"
            )

    route_parts: list[str] = []
    for ptype, cnt in sorted(counts.items()):
        route_parts.append(f"{cnt}× {ptype}")
    ui.done("  ".join(route_parts))

    # ── Phase 3: Extraction — extract-once, D19/D23 ───────────────────────────
    ui.begin("3", "Extract")
    # Orchestrator already unloaded before probe in Phase 2 (when vision is used)

    extract_t0 = time.monotonic()
    with Progress(
        SpinnerColumn(), BarColumn(), MofNCompleteColumn(),
        TextColumn("{task.description}"), console=console,
    ) as p:
        extract_task = p.add_task("Extracting", total=len(pages))

        def _on_page_done(page_num: int, page_type: str) -> None:
            p.update(extract_task, advance=1, description=f"[dim]{page_type}[/dim]")

        def _on_token(tok: int, elapsed: float, since: float, lbl: str) -> None:
            stall = f"  [yellow]⚠ no tokens {since:.0f}s — stall?[/yellow]" if since > 15 else ""
            p.update(
                extract_task,
                description=f"[cyan]{lbl}[/cyan]  [dim]{tok:,} tok · {elapsed:.0f}s[/dim]{stall}",
            )

        from cloak.vision import vision_tools as _vt
        _vt.set_progress_callback(_on_token)
        try:
            raw_content = _extract_by_route(
                pages, route_map, vision_available, _on_page_done,
                images_dir=images_dir, element_map=element_map,
                use_math_ocr=plan.use_math_ocr,
                slide_mode=plan.slide_mode,
                exam_mode=plan.exam_mode,
                poster_mode=plan.poster_mode,
            )
        finally:
            _vt.set_progress_callback(None)

    extract_elapsed = time.monotonic() - extract_t0
    vision_model = model_router.get_vision_model() if vision_available else "text-only"
    ui._phase_t0 = extract_t0
    ui.done(f"{vision_model} · {len(pages)}/{len(pages)} pages · {len(raw_content):,} chars")

    # ── Phase 4: FORMAT once — D20 ────────────────────────────────────────────
    ui.begin("4", "Format")
    fmt_t0    = time.monotonic()
    needs_fmt = _content_needs_format(raw_content)

    # Switch to orchestrator only if FORMAT will actually run — D37
    if needs_fmt:
        model_router.before_orchestrator_phase()

    if needs_fmt:
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
            fmt_task = p.add_task("Formatting ...", total=None)

            def _fmt_token(tok: int, elapsed: float, since: float, lbl: str) -> None:
                stall = f"  [yellow]⚠ no tokens {since:.0f}s[/yellow]" if since > 15 else ""
                p.update(fmt_task, description=f"[cyan]format[/cyan]  [dim]{tok:,} tok · {elapsed:.0f}s[/dim]{stall}")

            from cloak.vision import vision_tools as _vt2
            _vt2.set_progress_callback(_fmt_token)
            try:
                markdown = _run_format_session(raw_content)
            finally:
                _vt2.set_progress_callback(None)
    else:
        markdown = raw_content

    fmt_elapsed = time.monotonic() - fmt_t0

    guard_ok = _content_loss_ok(raw_content, markdown)
    guard_str = "" if guard_ok else "  [yellow]⚠ guard triggered — raw kept[/yellow]"
    skip_str  = "  [dim]skipped — already clean[/dim]" if not needs_fmt else ""
    ui._phase_t0 = fmt_t0
    ui.done(
        f"{len(raw_content):,} → {len(markdown):,} chars{guard_str}{skip_str}",
        warn=not guard_ok,
        skip=not needs_fmt,
    )

    # ── Text-only path: heuristic judge, then output ──────────────────────────
    if not vision_available:
        out_path  = _output_path(pdf_path)
        conf_path = _confidence_path(out_path)
        out_path.write_text(postprocess.run(markdown), encoding="utf-8")

        # Run heuristic judge on all pages (no vision needed — word-overlap only)
        heur_scores: list[quality_judge.PageScore] = [
            quality_judge.heuristic_judge(
                page_num=pg.page_num,
                page_text=pg.text,
                extracted_md=markdown,
                round_num=1,
            )
            for pg in pages
        ]

        ui.begin("8", "Output")
        ui.done(f"{out_path.name}")

        review_score: float | None = None
        if deep_review:
            review_score = _run_phase9(pdf_path, pages, markdown, out_path, ui)

        # Teardown AFTER Phase 9 — LLM reused for deep review (D49)
        model_router.teardown_pdf()

        metrics = compute_metrics(heur_scores, pages, markdown, review_score)
        conf_path.write_text(
            _build_confidence_report(heur_scores, pdf_path.name, metrics), encoding="utf-8"
        )
        _print_metrics_summary(metrics)

        _registry.upsert(_reg, pdf_path, _ws,
                         status=_registry.DONE,
                         last_parsed=_registry.now_iso(),
                         elapsed_seconds=round(time.monotonic() - parse_t0, 1),
                         judge_score=metrics.judge_score if metrics.judged else None,
                         review_score=review_score,
                         completeness=metrics.completeness_ratio,
                         flagged_pages=0,
                         total_pages=len(pages),
                         heading_count=metrics.heading_count,
                         table_count=metrics.table_count,
                         model="text-only",
                         output_md=str(out_path))
        _registry.save(_reg, _ws)
        return markdown

    # ── Phases 5–6: Judge + Patch loop — no re-extraction (D19) ──────────────
    best = RoundResult(
        round_num=0, markdown=markdown, score=0.0, gaps=[], action="patch", page_scores=[]
    )
    messages: list[dict] = [{"role": "system", "content": _PATCH_SYSTEM}]
    # Pages that scored ≥ JUDGE_SKIP_THRESHOLD are not re-judged in later rounds.
    carryover: dict[int, quality_judge.PageScore] = {}

    _max_rounds = plan.max_rounds

    for round_num in range(1, _max_rounds + 1):
        ui.round_header(round_num, _max_rounds)

        # Phase 5: Judge — skip already-excellent pages from round 1 onward
        model_router.before_vision_phase()
        judge_t0 = time.monotonic()
        pages_not_carried = [pg for pg in pages if pg.page_num not in carryover]

        # Adaptive sampling (D28): prioritise visual / complex pages
        if plan.judge_sample_rate < 1.0:
            import random as _rand
            n_sample  = max(1, int(len(pages_not_carried) * plan.judge_sample_rate))
            priority  = [p for p in pages_not_carried
                         if route_map.get(p.page_num) in ("image_heavy", "mixed", "scanned", "table_heavy")]
            remainder = [p for p in pages_not_carried if p not in set(priority)]
            _rand.shuffle(remainder)
            pages_to_judge = (priority + remainder)[:n_sample]
        else:
            pages_to_judge = pages_not_carried

        skipped = len(pages) - len(pages_to_judge) - len(carryover)

        # D33: only call vision for pages that actually have visual content
        vision_pages    = [pg for pg in pages_to_judge if _needs_vision_map.get(pg.page_num, True)]
        heuristic_pages = [pg for pg in pages_to_judge if not _needs_vision_map.get(pg.page_num, True)]

        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
            desc = f"Judging {len(pages_to_judge)}/{len(pages)} page(s)"
            n_carried = len(carryover)
            if n_carried:
                desc += f" · {n_carried} carried (≥{JUDGE_SKIP_THRESHOLD:.0f})"
            if skipped > 0:
                desc += f" · {skipped} sampled out"
            if heuristic_pages:
                desc += f" · {len(heuristic_pages)} heuristic"
            judge_task = p.add_task(desc + " ...", total=None)

            def _judge_token(tok: int, elapsed: float, since: float, lbl: str) -> None:
                stall = f"  [yellow]⚠ no tokens {since:.0f}s[/yellow]" if since > 15 else ""
                p.update(judge_task, description=f"[cyan]{lbl}[/cyan]  [dim]{tok:,} tok · {elapsed:.0f}s[/dim]{stall}")

            from cloak.vision import vision_tools as _vt_judge
            _vt_judge.set_progress_callback(_judge_token)
            try:
                new_scores: list[quality_judge.PageScore] = []
                # Heuristic judge: no vision call, word-overlap + structure score
                for pg in heuristic_pages:
                    new_scores.append(quality_judge.heuristic_judge(
                        page_num=pg.page_num,
                        page_text=pg.text,
                        extracted_md=markdown,
                        round_num=round_num,
                    ))
                # Vision judge: only for image_heavy / mixed / scanned pages
                for pg in vision_pages:
                    new_scores.append(quality_judge.judge(
                        page_num=pg.page_num,
                        page_image=pg.image,
                        extracted_md=markdown,
                        round_num=round_num,
                        model=model_router.get_vision_model(),
                    ))
            finally:
                _vt_judge.set_progress_callback(None)

        # Carry over high-scoring pages so they aren't re-judged next round
        judged_nums = {ps.page_num for ps in new_scores}
        for ps in new_scores:
            if ps.score >= JUDGE_SKIP_THRESHOLD:
                carryover[ps.page_num] = ps

        # Gap B: exclude carryover pages already in new_scores to avoid double-counting
        page_scores = new_scores + [ps for ps in carryover.values() if ps.page_num not in judged_nums]
        avg_score, all_gaps, action = quality_judge.aggregate_page_results(page_scores)

        if avg_score > best.score:
            best = RoundResult(round_num, markdown, avg_score, all_gaps, action, page_scores)

        ui.score_line(avg_score, len(all_gaps), action, QUALITY_THRESHOLD,
                      time.monotonic() - judge_t0)

        if best.score >= QUALITY_THRESHOLD:
            break
        if action == "accept" or round_num == _max_rounds:
            break

        # Phase 6: Patch — skip with a clear warning if Ollama is unreachable
        if not model_router.is_ollama_available():
            console.print(
                "  [red]✗[/red]  [bold cyan]6[/bold cyan]  [bold]Patch[/bold]"
                "  [red]Ollama unreachable — patch skipped. "
                "Is Ollama running? Run: ollama serve[/red]"
            )
            break

        model_router.before_orchestrator_phase()
        patch_t0 = time.monotonic()
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
            p.add_task(f"Patching {len(all_gaps)} gap(s) ...", total=None)
            messages = context_manager.compress_history(messages)
            updated = _run_patch_loop(
                pages, markdown, all_gaps, messages,
                images_dir=images_dir, element_map=element_map,
            )

        pre_patch_len = len(markdown)
        patch_changed = updated != markdown
        if not _content_loss_ok(markdown, updated):
            console.print(
                f"  [red]Content-loss guard triggered "
                f"({len(markdown):,}→{len(updated):,} chars) — reverting[/red]"
            )
        else:
            markdown = updated

        ui.patch_line(pre_patch_len, len(markdown), time.monotonic() - patch_t0)

        if not patch_changed:
            console.print(
                "  [dim]Patch produced no changes — stopping early[/dim]"
            )
            break

    # ── Phase 8 + 8.5: Post-process then write ───────────────────────────────
    ui.begin("8", "Output")
    out_path  = _output_path(pdf_path)
    conf_path = _confidence_path(out_path)
    out_path.write_text(postprocess.run(best.markdown), encoding="utf-8")
    saved_count = len(list(images_dir.glob("*.png"))) if images_dir.exists() else 0
    images_str  = f"  {saved_count} image(s)" if saved_count else ""
    ui.done(
        f"score {best.score:.1f}/10 · round {best.round_num}"
        f"  →  {out_path.name}{images_str}"
    )

    _used_model = model_router.get_vision_model() or "text-only"

    review_score: float | None = None
    if deep_review:
        review_score = _run_phase9(pdf_path, pages, best.markdown, out_path, ui)

    # Teardown AFTER Phase 9 — LLM reused for deep review (D49)
    model_router.teardown_pdf()

    metrics = compute_metrics(best.page_scores, pages, best.markdown, review_score)
    conf_path.write_text(
        _build_confidence_report(best.page_scores, pdf_path.name, metrics), encoding="utf-8"
    )

    flagged_count = _write_flagged_pages(
        best.page_scores, pages, pdf_path.name, _flagged_path(out_path)
    )
    if flagged_count:
        console.print(
            f"  [yellow]⚠[/yellow]  [yellow]{flagged_count} page(s) flagged for review[/yellow]"
            f"  [dim]→ {_flagged_path(out_path).name}[/dim]"
        )

    _print_metrics_summary(metrics)

    _registry.upsert(_reg, pdf_path, _ws,
                     status=_registry.FLAGGED if flagged_count else _registry.DONE,
                     last_parsed=_registry.now_iso(),
                     elapsed_seconds=round(time.monotonic() - parse_t0, 1),
                     judge_score=metrics.judge_score,
                     review_score=metrics.review_score,
                     completeness=metrics.completeness_ratio,
                     flagged_pages=flagged_count,
                     total_pages=len(pages),
                     heading_count=metrics.heading_count,
                     table_count=metrics.table_count,
                     model=_used_model,
                     output_md=str(out_path))
    _registry.save(_reg, _ws)

    return best.markdown


def _run_phase9(
    pdf_path: Path,
    pages: list,
    final_markdown: str,
    out_path: Path,
    ui: _PhaseUI,
) -> float | None:
    """Phase 9: deep review using ORCHESTRATOR_MODEL already loaded from Phase 6 (D49)."""
    from cloak.config import DEEP_REVIEW_MODEL
    from cloak.quality import deep_review as dr

    # Ensure LLM is active — unloads VLM if judge loop exited after a judge round (D49)
    model_router.before_orchestrator_phase()

    ui.begin("9", "Deep Review")
    console.print(f"       [dim]{DEEP_REVIEW_MODEL} (reusing loaded model)[/dim]")

    rev_path, rev_score = dr.run(
        pdf_path=pdf_path,
        pages=pages,
        final_markdown=final_markdown,
        review_out=_review_path(out_path),
        console=console,
    )
    if rev_path:
        score_str = f" · {rev_score:.1f}/10" if rev_score is not None else ""
        ui.done(f"{rev_path.name}{score_str}")
    else:
        ui.done("skipped", skip=True)
    return rev_score


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("[red]Usage: python -m cloak.orchestration.parser_agent <pdf_path>[/red]")
        sys.exit(1)

    result = parse(sys.argv[1])
    console.print(f"\nMarkdown length: {len(result)} chars")
