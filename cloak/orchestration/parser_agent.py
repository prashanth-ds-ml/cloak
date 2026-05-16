"""
parser_agent.py — 8-phase agentic PDF parse pipeline.

Phases:
  0  load_pages          — PyMuPDF + pdfplumber, no model
  1  profile_all         — heuristic page classification, no model (D21)
  2  routing display     — console summary
  3  _extract_by_route   — selective per-page extraction; vision only for image_heavy/mixed (D19/D23)
  4  _run_format_session — qwen3:8b FORMAT once before the judge-patch loop (D20)
  5  quality_judge       — qwen2.5vl:7b judges all pages, produces PageScore list
  6  _run_patch_loop     — qwen3:8b fills gaps flagged by judge
  5–6 repeat up to MAX_ROUNDS; judge+patch only — no re-extraction (D19)
  8  write final.md + confidence_report.md

Hard rules: D2 (best round wins), D3 (threshold 8.0), D5 (content-loss guard),
D6 (context cap), D19 (extract-once), D20 (FORMAT before patch), D23 (selective vision).
"""
from __future__ import annotations

import io
import json
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import ollama
from PIL import Image
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

# Force UTF-8 stdout on Windows so Rich spinner chars don't crash in cp1252 terminals
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

console = Console()

from cloak.config import (
    AGENT_TIMEOUT,
    CONTENT_LOSS_LIMIT,
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

            tool_result, current_draft = _execute_tool(fn_name, fn_args, pages, current_draft)
            messages.append({"role": "tool", "content": tool_result})

            if tool_result == "__FINISH__":
                return current_draft

    return current_draft


# ── Phase 3: Per-page extraction strategies ───────────────────────────────────

def _extract_text_page(pg: PageData) -> str:
    """text_rich: PyMuPDF text + pdfplumber tables."""
    parts = [pg.text] if pg.text.strip() else []
    for tbl in pg.tables:
        parts.append(tbl.to_markdown())
    return "\n\n".join(parts)


def _extract_table_page(pg: PageData) -> str:
    """table_heavy: pdfplumber tables first, then surrounding text."""
    parts = [tbl.to_markdown() for tbl in pg.tables]
    if pg.text.strip():
        parts.append(pg.text)
    return "\n\n".join(parts) if parts else pg.text


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


def _extract_mixed_page(pg: PageData, model: str) -> str:
    """mixed: PyMuPDF text + pdfplumber tables + region vision for image blocks."""
    md = _extract_text_page(pg)
    for i, r in enumerate(pg.regions):
        try:
            desc = vision_tools.region_describe(r.image, r.label, model=model)
            model_router.mark_success(model)
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
) -> str:
    """
    Phase 3: dispatch each page to its extraction strategy based on RouteMap.
    Vision is called only for image_heavy and mixed pages (D23).
    Scanned pages always use OCR regardless of vision availability.
    """
    parts: list[str] = []
    model = model_router.get_vision_model() if vision_available else ""

    for pg in pages:
        page_type = route_map.get(pg.page_num, "text_rich")
        console.print(
            f"  [dim]Page {pg.page_num + 1}/{len(pages)}: {page_type}[/dim]",
            highlight=False,
        )

        if page_type == "scanned":
            md = _extract_scanned_page(pg)
        elif page_type == "image_heavy" and vision_available:
            md = _extract_vision_page(pg, model)
        elif page_type == "mixed" and vision_available:
            md = _extract_mixed_page(pg, model)
        elif page_type == "table_heavy":
            md = _extract_table_page(pg)
        else:
            md = _extract_text_page(pg)
            if page_type in ("image_heavy", "mixed") and not vision_available:
                for i, r in enumerate(pg.regions):
                    md += f"\n\n<!-- image region {i}: {r.label} (vision unavailable) -->"

        parts.append(md)

    return "\n\n---\n\n".join(parts)


# ── Phase 4: FORMAT step ──────────────────────────────────────────────────────

_FORMAT_SYSTEM = """\
You are a document formatter. Convert the raw extracted text below into clean, well-structured markdown.
Preserve ALL content — do not remove, summarise, or paraphrase any information.
Add appropriate headings, lists, tables, and code blocks where they improve readability.
Fix spacing and paragraph breaks. Output ONLY the formatted markdown, nothing else."""


def _run_format_session(raw_content: str) -> str:
    """
    Phase 4: qwen3:8b restructures raw extraction into clean markdown (D20).
    Long documents are chunked at the context budget; the unformatted tail is appended
    so no content is silently dropped.
    Falls back to raw_content on failure, timeout, or content-loss (D5).
    """
    char_cap = MODEL_NUM_CTX * 3   # rough chars that fit within token budget
    content_in = raw_content[:char_cap]
    truncated = len(raw_content) > char_cap

    result_q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            resp = ollama.chat(
                model=ORCHESTRATOR_MODEL,
                messages=[
                    {"role": "system", "content": _FORMAT_SYSTEM},
                    {"role": "user", "content": f"Format this document:\n\n{content_in}"},
                ],
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
        console.print("  [yellow]FORMAT timeout — using raw content[/yellow]")
        return raw_content

    if kind == "err":
        console.print(f"  [yellow]FORMAT error: {value} — using raw content[/yellow]")
        return raw_content

    formatted = (value.message.content or "").strip()
    if not formatted:
        return raw_content

    if not _content_loss_ok(content_in, formatted):
        console.print("  [yellow]FORMAT content-loss guard triggered — reverting to raw[/yellow]")
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

def parse(pdf_path: Path | str) -> str:
    """
    Full 8-phase agentic parse pipeline. Returns best-scoring markdown string.
    Writes final.md and confidence_report.md to data/markdown/{specialty}/.
    """
    pdf_path = Path(pdf_path)
    console.print(Panel.fit(
        f"[bold cyan]cloak[/bold cyan] — parsing [green]{pdf_path.name}[/green]",
        border_style="cyan",
    ))

    # Phase 0: Load
    console.print("\n[bold]Phase 0[/bold]  Loading ...")
    pages = load_pages(pdf_path)
    console.print(f"  {len(pages)} page(s) loaded")

    # Phase 1: Profile (heuristic, no model)
    console.print("\n[bold]Phase 1[/bold]  Profiling ...")
    profiles = profile_all(pages)
    route_map = build_route_map(profiles)
    counts = summarise_profiles(profiles)
    console.print(f"  {counts}")

    # Phase 2: Routing display
    console.print("\n[bold]Phase 2[/bold]  Routing plan:")
    for ptype, cnt in sorted(counts.items()):
        console.print(f"    {cnt}x {ptype}")

    model_router.reset()
    vision_available = _probe_vision()
    if not vision_available:
        console.print(
            "[yellow]Vision model unavailable — image/diagram regions will be skipped.[/yellow]"
        )

    # Phase 3: Selective extraction — extract-once, D19/D23
    console.print("\n[bold]Phase 3[/bold]  Extracting ...")
    if vision_available:
        model_router.before_vision_phase()

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        task = p.add_task("Extracting pages ...", total=None)
        raw_content = _extract_by_route(pages, route_map, vision_available)
        p.update(task, description=f"Extracted {len(raw_content)} chars")

    # Switch to orchestrator for Phase 4 (unloads vision if active)
    model_router.before_orchestrator_phase()

    # Phase 4: FORMAT once — D20
    console.print("\n[bold]Phase 4[/bold]  Formatting ...")
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
        task = p.add_task("Formatting ...", total=None)
        markdown = _run_format_session(raw_content)
        p.update(task, description=f"Formatted: {len(markdown)} chars")

    console.print(f"  {len(raw_content)} → {len(markdown)} chars")

    # Text-only path: skip judge-patch loop (no vision for judging)
    if not vision_available:
        out_path = _output_path(pdf_path)
        out_path.write_text(markdown, encoding="utf-8")
        console.print(
            f"\n[bold green]Done (text-only).[/bold green] Output: [cyan]{out_path}[/cyan]"
        )
        model_router.teardown_pdf()
        return markdown

    # Phases 5–6: Judge + Patch loop — no re-extraction (D19)
    best = RoundResult(
        round_num=0, markdown=markdown, score=0.0, gaps=[], action="patch", page_scores=[]
    )
    messages: list[dict] = [{"role": "system", "content": _PATCH_SYSTEM}]

    for round_num in range(1, MAX_ROUNDS + 1):
        console.print(f"\n[bold]Round {round_num}/{MAX_ROUNDS}[/bold]  Judge + Patch")

        # Phase 5: Judge (vision model)
        model_router.before_vision_phase()

        with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
            task = p.add_task("Judging quality ...", total=None)
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
            p.update(task, description=f"Score: {avg_score:.1f}/10  action={action}")

        console.print(
            f"  Score: [bold]{avg_score:.1f}/10[/bold]  action=[yellow]{action}[/yellow]"
        )

        if avg_score > best.score:
            best = RoundResult(round_num, markdown, avg_score, all_gaps, action, page_scores)
            console.print(f"  [green]New best: round {round_num}  score {avg_score:.1f}[/green]")

        if best.score >= QUALITY_THRESHOLD:
            console.print(
                f"  [green]Quality threshold {QUALITY_THRESHOLD} reached — stopping early[/green]"
            )
            break

        if action == "accept" or round_num == MAX_ROUNDS:
            break

        # Phase 6: Patch (orchestrator model)
        model_router.before_orchestrator_phase()

        console.print(f"  Patching {len(all_gaps)} gap(s) ...")
        messages = context_manager.compress_history(messages)
        updated = _run_patch_loop(pages, markdown, all_gaps, messages)

        if not _content_loss_ok(markdown, updated):
            console.print(
                f"  [red]Content-loss guard triggered "
                f"({len(markdown)}→{len(updated)} chars) — reverting[/red]"
            )
        else:
            markdown = updated

    # Phase 8: Write output
    out_path = _output_path(pdf_path)
    out_path.write_text(best.markdown, encoding="utf-8")

    conf_path = _confidence_path(out_path)
    conf_path.write_text(
        _build_confidence_report(best.page_scores, pdf_path.name),
        encoding="utf-8",
    )

    console.print(
        f"\n[bold green]Done.[/bold green] Best round: {best.round_num}  "
        f"Score: {best.score:.1f}/10"
    )
    console.print(f"Output:     [cyan]{out_path}[/cyan]")
    console.print(f"Confidence: [cyan]{conf_path}[/cyan]")

    model_router.teardown_pdf()
    return best.markdown


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("[red]Usage: python -m cloak.orchestration.parser_agent <pdf_path>[/red]")
        sys.exit(1)

    result = parse(sys.argv[1])
    console.print(f"\nMarkdown length: {len(result)} chars")
