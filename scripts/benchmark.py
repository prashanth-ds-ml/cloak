"""
benchmark.py — run cloak parse on all representative PDFs and produce docs/BENCHMARK.md.

Usage:
    python scripts/benchmark.py [--no-review]

Runs sequentially. Each parse calls parse_pdf() directly (no subprocess).
Reads confidence_<stem>.md after each run to extract Judge Score.
Writes docs/BENCHMARK.md on completion (and after each run for live progress).
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Windows stdout must be UTF-8 or rich/print will crash on non-ASCII characters
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent

# ── Test suite ────────────────────────────────────────────────────────────────

@dataclass
class TestCase:
    pdf_type:    str    # display label
    pdf_path:    str    # relative to ROOT
    notes:       str = ""

SUITE: list[TestCase] = [
    # ── Text-dominant ────────────────────────────────────────────────────────
    TestCase("Research paper (two-column, academic)",
             "data/samples/research_paper/bert_devlin_2018.pdf",
             "BERT paper — dense academic, references, 2-col layout"),
    TestCase("Medical guideline (clinical protocol)",
             "data/raw/cardiology/stemi.pdf",
             "STEMI protocol — structured clinical text, decision trees"),
    TestCase("Legal document (court opinion)",
             "data/samples/legal_document/scotus_dobbs_opinion_sliced.pdf",
             "SCOTUS Dobbs — dense legal prose, footnotes, headings"),
    TestCase("Financial report (annual report)",
             "data/samples/financial_report/berkshire_hathaway_2023_annual_report_sliced.pdf",
             "Berkshire 2023 AR — narrative + financial tables"),
    TestCase("Technical manual (software docs)",
             "data/samples/technical_manual/postgresql_15_docs_sliced.pdf",
             "PostgreSQL 15 docs — code blocks, technical terminology"),
    TestCase("Bilingual legal document",
             "data/samples/bilingual_document/echr_judgment_en_fr.pdf",
             "ECHR judgment — English + French columns side-by-side"),
    # ── Tables ───────────────────────────────────────────────────────────────
    TestCase("Table-heavy (government codebook)",
             "data/samples/table_heavy/cdc_nchs_body_measurements_codebook_sliced.pdf",
             "NHANES codebook — dense tables, variable codes, definitions"),
    TestCase("Government report (text + tables)",
             "data/samples/government_document/irs_publication_17_sliced.pdf",
             "IRS Pub 17 — mixed text and complex tax tables"),
    TestCase("Invoice (structured form)",
             "data/samples/invoice/sample_invoice_sliced.pdf",
             "Sample invoice — line items, totals, addresses"),
    # ── Image / visual ───────────────────────────────────────────────────────
    TestCase("Slide deck (image-heavy)",
             "data/samples/slide_deck/mit_ocw_computational_biology_lecture1_sliced.pdf",
             "MIT OCW — slide images, diagrams, text-sparse slides; D38 slide_mode"),
    TestCase("Image-heavy annual report",
             "data/samples/image_heavy/nasa_esto_annual_report_sliced.pdf",
             "NASA ESTO 2024 — full-bleed images, captions, infographics"),
    TestCase("Medical poster (image + text layout)",
             "data/samples/poster/neurology_stroke.pdf",
             "Neurology poster — mixed image/text, single-page dense layout"),
    TestCase("Multi-column academic survey",
             "data/samples/multi_column/arxiv_survey_multi_column_sliced.pdf",
             "ArXiv survey — 2-col layout, citations, figures"),
    # ── Math / equations ─────────────────────────────────────────────────────
    TestCase("Textbook (engineering, math equations)",
             "data/samples/textbook/engineering_thermodynamics_pk_nag_sliced.pdf",
             "P K Nag thermodynamics — inline equations, figures, multi-section"),
    # ── Exam papers (D39 exam_mode) ───────────────────────────────────────────
    TestCase("Exam paper — JEE Advanced 2023",
             "data/samples/question_paper/jee_advanced_2023_paper1_sliced.pdf",
             "JEE 2023 P1 — fragmented Symbol-font math, multi-choice; D39"),
    TestCase("Exam paper — GATE CS 2024",
             "data/samples/question_paper/gate_cs_2024_sliced.pdf",
             "GATE 2024 CS — theory + logic + programming questions; D39"),
    TestCase("Exam paper — GATE EE 2024",
             "data/samples/question_paper/gate_ee_2024_sliced.pdf",
             "GATE 2024 EE — circuit problems, equations, diagrams; D39"),
    TestCase("Exam paper — ESE EE 2024 (UPSC)",
             "data/samples/question_paper/ese_ee_2024_sliced.pdf",
             "UPSC ESE 2024 EE — heavy diagrams, circuit problems, image-based; D39"),
    # ── Scanned / OCR ────────────────────────────────────────────────────────
    TestCase("Scanned historical document",
             "data/samples/scanned_pdf/history_dumfries_1800s_scanned_sliced.pdf",
             "1800s Dumfries history — low-res scan, no text layer; Surya OCR"),
]

# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class BenchResult:
    pdf_type:     str
    pdf_path:     str
    notes:        str
    score:        float | None = None
    coverage:     str   = ""
    completeness: str   = ""
    structure:    str   = ""
    flags:        list[str] = field(default_factory=list)   # exam_mode, slide_mode etc.
    elapsed_s:    float = 0.0
    error:        str   = ""


def _stem(pdf_path: str) -> str:
    return Path(pdf_path).stem


def _confidence_path(pdf_path: str) -> Path:
    """Mirror parser_agent._output_path: data/raw/cardiology/x.pdf -> data/markdown/cardiology/x_confidence.md"""
    p = Path(pdf_path)
    parts = p.parts
    try:
        raw_idx = next(i for i, part in enumerate(parts) if part == "raw")
        specialty = parts[raw_idx + 1]
        out_dir = ROOT / "data" / "markdown" / specialty
    except (StopIteration, IndexError):
        out_dir = ROOT / "data" / "markdown"
    return out_dir / f"{p.stem}_confidence.md"


def _parse_confidence(conf_path: Path) -> dict:
    """Extract Judge Score, Coverage, Completeness, Structure from confidence report."""
    if not conf_path.exists():
        return {}
    text = conf_path.read_text(encoding="utf-8", errors="replace")
    result: dict = {}

    m = re.search(r"Judge Score\s*\|\s*([\d.]+)\s*/\s*10", text)
    if m:
        result["score"] = float(m.group(1))

    m = re.search(r"Coverage\s*\|\s*([^\n|]+)", text)
    if m:
        result["coverage"] = m.group(1).strip()

    m = re.search(r"Completeness\s*\|\s*([^\n|]+)", text)
    if m:
        result["completeness"] = m.group(1).strip()

    m = re.search(r"Structure\s*\|\s*([^\n|]+)", text)
    if m:
        result["structure"] = m.group(1).strip()

    return result


def _run_parse(tc: TestCase, no_review: bool) -> BenchResult:
    pdf_abs = ROOT / tc.pdf_path
    result  = BenchResult(pdf_type=tc.pdf_type, pdf_path=tc.pdf_path, notes=tc.notes)

    if not pdf_abs.exists():
        result.error = "file not found"
        return result

    print(f"\n{'='*70}")
    print(f"  [{tc.pdf_type}]")
    print(f"  {tc.pdf_path}")
    print(f"{'='*70}", flush=True)

    from cloak.orchestration.parser_agent import parse as do_parse

    t0 = time.monotonic()
    try:
        do_parse(pdf_abs, deep_review=not no_review, workspace=ROOT)
    except SystemExit:
        pass   # typer/rich sometimes raises SystemExit on clean exit
    except Exception as exc:
        result.error = str(exc)[:200]
        result.elapsed_s = time.monotonic() - t0
        return result
    result.elapsed_s = time.monotonic() - t0

    conf_path = _confidence_path(tc.pdf_path)
    parsed = _parse_confidence(conf_path)
    result.score        = parsed.get("score")
    result.coverage     = parsed.get("coverage", "")
    result.completeness = parsed.get("completeness", "")
    result.structure    = parsed.get("structure", "")
    return result


# ── Markdown report ───────────────────────────────────────────────────────────

def _score_emoji(score: float | None) -> str:
    if score is None: return "—"
    if score >= 9.0: return "✅"
    if score >= 8.0: return "🟡"
    if score >= 6.0: return "🟠"
    return "🔴"


def write_benchmark_md(results: list[BenchResult], path: Path) -> None:
    lines: list[str] = [
        "# Cloak — Benchmark Results",
        "",
        f"> Generated: {time.strftime('%Y-%m-%d %H:%M')}  |  "
        f"Session 21  |  D38 slide_mode · D39 exam_mode · D35 pix2tex",
        "",
        "## Score legend",
        "",
        "✅ ≥ 9.0 (excellent)  |  🟡 ≥ 8.0 (good, meets threshold)  |  "
        "🟠 ≥ 6.0 (fair)  |  🔴 < 6.0 (poor)",
        "",
        "## Results summary",
        "",
        "| # | PDF Type | Score | Coverage | Completeness | Time | Notes |",
        "|---|----------|-------|----------|--------------|------|-------|",
    ]
    for i, r in enumerate(results, 1):
        score_str  = f"{r.score:.1f}" if r.score is not None else "ERR"
        emoji      = _score_emoji(r.score)
        time_str   = f"{r.elapsed_s/60:.1f} min" if r.elapsed_s > 0 else "—"
        coverage   = r.coverage.split("(")[0].strip() if r.coverage else "—"
        complete   = r.completeness if r.completeness else "—"
        notes_col  = r.error if r.error else r.notes[:60]
        lines.append(
            f"| {i} | {r.pdf_type} | {emoji} {score_str} | {coverage} | {complete} | {time_str} | {notes_col} |"
        )

    lines += [
        "",
        "## Detailed results",
        "",
    ]
    for i, r in enumerate(results, 1):
        score_str = f"{r.score:.1f}/10" if r.score is not None else "ERROR"
        emoji     = _score_emoji(r.score)
        lines += [
            f"### {i}. {r.pdf_type}",
            "",
            f"**File**: `{r.pdf_path}`  ",
            f"**Score**: {emoji} {score_str}  ",
            f"**Coverage**: {r.coverage or '—'}  ",
            f"**Completeness**: {r.completeness or '—'}  ",
            f"**Structure**: {r.structure or '—'}  ",
            f"**Time**: {r.elapsed_s/60:.1f} min  ",
            f"**Notes**: {r.notes}  ",
        ]
        if r.error:
            lines.append(f"**Error**: {r.error}  ")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[benchmark] Written -> {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    no_review = "--no-review" in sys.argv
    out_path  = ROOT / "docs" / "BENCHMARK.md"

    print(f"cloak benchmark — {len(SUITE)} PDFs  |  no_review={no_review}")
    print(f"Output: {out_path}")

    results: list[BenchResult] = []
    for tc in SUITE:
        r = _run_parse(tc, no_review)
        results.append(r)
        score_str = f"{r.score:.1f}" if r.score is not None else "ERR"
        status    = f"  -> {score_str}/10  ({r.elapsed_s/60:.1f} min)"
        print(status)
        # write incremental results after each run
        write_benchmark_md(results, out_path)

    print("\n" + "="*70)
    print("BENCHMARK COMPLETE")
    print("="*70)
    for r in results:
        s = f"{r.score:.1f}" if r.score is not None else "ERR"
        print(f"  {s:>5}  {r.pdf_type}")


if __name__ == "__main__":
    main()
