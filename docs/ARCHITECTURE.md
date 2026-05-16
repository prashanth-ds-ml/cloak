---
type: architecture
updated: 2026-05-16
---

# Architecture — cloak PDF Parser

> Related: [[CLAUDE.md]] · [[docs/MODULES.md]] · [[docs/MODELS.md]] · [[docs/DECISIONS.md]]

General-purpose local PDF → structured markdown. Any document type — research papers, legal, medical, technical manuals, reports, scanned documents, forms, textbooks. No cloud API. No data leaves the machine. See [[docs/DECISIONS.md]] §D16.

---

## CLI startup flow

```mermaid
flowchart TD
    A([cloak invoked]) --> B["system_check.show_startup_screen()\nhardware table · model status table"]
    B --> C{"RAM gate\n≥ MIN_FREE_RAM_GB?"}
    C -->|no| D["warn: insufficient RAM\nlist what to close"]
    C -->|yes| E{command?}
    D --> E

    E -->|"cloak (no args)"| F["startup screen only — exit"]
    E -->|"cloak status"| G["hardware + model status — exit"]
    E -->|"cloak list"| H["list data/markdown/ contents — exit"]
    E -->|"cloak parse <pdf|dir>"| I["for each PDF → parse()"]
    I --> J(["data/markdown/{specialty}/{stem}.md\ndata/markdown/{specialty}/{stem}_confidence.md"])
```

**CLI commands:**
```
cloak                    → startup screen (hardware + model status)
cloak parse <pdf|dir>    → parse PDF(s), startup screen shown first
cloak status             → hardware + model status only
cloak list               → list parsed documents in data/markdown/
```

See [[docs/MODULES.md]] §CLI · [[docs/DECISIONS.md]] §D17

---

## Full pipeline — 8 phases

```mermaid
flowchart TD
    A([PDF file]) --> P0

    subgraph P0["Phase 0 · INTAKE  (no model)"]
        I1["load PDF · hash file\ncount pages · create output dir"]
    end

    P0 --> P1

    subgraph P1["Phase 1 · PAGE PROFILER  (no model — heuristic)"]
        PR1["for each page:\ntext_length · image_area_ratio · table_block_count\nblank detection"]
        PR1 --> PR2["classify → text_rich | table_heavy | image_heavy | scanned | mixed"]
        PR2 --> PR3["RouteMap: page_num → extraction strategy"]
    end

    P1 --> P2

    subgraph P2["Phase 2 · ROUTING  (deterministic)"]
        R1["text_rich   → PyMuPDF spatial + pdfplumber tables"]
        R2["table_heavy → pdfplumber (vision fallback for complex)"]
        R3["scanned     → render → Tesseract OCR"]
        R4["image_heavy → qwen2.5vl:7b full-page vision"]
        R5["mixed       → text + vision for image regions only"]
    end

    P2 --> P3

    subgraph P3["Phase 3 · EXTRACTION  (vision only for image_heavy/mixed)"]
        E1["execute strategy per page from RouteMap\nvision model loaded only where needed\noutput: raw_content[] with page citations"]
    end

    P3 --> P4

    subgraph P4["Phase 4 · FORMAT SESSION  (qwen3:8b — once)"]
        F1["raw_content[] → structured markdown\nheadings · tables · lists\ncontent-loss guard (D5)"]
    end

    P4 --> P5

    subgraph P5["Phase 5 · JUDGE SESSION  (qwen2.5vl:7b — per page)"]
        J1["score each page section vs source image\noutput: PageScore[] with gaps per page"]
    end

    P5 --> CHK{best_score ≥ 8.0\nor round == MAX_ROUNDS?}
    CHK -->|no| P6

    subgraph P6["Phase 6 · PATCH SESSION  (qwen3:8b)"]
        PA1["targeted patches for pages with gaps\ncontent-loss guard (D5)"]
    end

    P6 --> P5

    CHK -->|yes| P8

    subgraph P8["Phase 8 · OUTPUT"]
        O1["final.md  (best round — D2)"]
        O2["confidence_report.md  (per-page High/Medium/Low — D24)"]
    end
```

---

## Phase-based model routing (D14)

Each quality round (Phases 5–6) splits into two hard phases. Models loaded and unloaded at phase boundary — never mid-round.

```mermaid
sequenceDiagram
    participant A as parser_agent
    participant R as model_router
    participant V1 as qwen2.5vl:7b
    participant V2 as llama3.2-vision:11b
    participant O as qwen3:8b

    A->>R: reset()
    Note over A,O: Phase 3 — selective extraction
    A->>R: before_vision_phase()
    A->>V1: full_page_extract() for image_heavy/mixed pages only
    A->>R: before_orchestrator_phase()
    A->>O: Phase 4 FORMAT session

    loop Rounds 1..MAX_ROUNDS (Phases 5–6)
        Note over A,O: Phase 5 — judge every page
        A->>R: before_vision_phase()
        A->>V1: judge_quality() per page [or V2 if sticky]
        A->>R: before_orchestrator_phase()
        A->>O: Phase 6 PATCH session (fill gaps)
    end

    A->>R: teardown_pdf()
    R->>V1: unload [or V2 if sticky]
```

---

## VRAM budget by phase

| Phase | qwen2.5vl sticky | llama3.2-vision sticky |
|---|---|---|
| **Phase 3 EXTRACT** (image_heavy only) | V1 ~5 GB GPU · O ~5 GB RAM/GPU (coexist) | V2 ~11 GB GPU+RAM · **O unloaded** |
| **Phase 4 FORMAT** | O ~5 GB GPU · V1 may stay warm | O ~5 GB GPU · **V2 unloaded** |
| **Phase 5 JUDGE** | V1 ~5 GB GPU · O ~5 GB RAM/GPU (coexist) | V2 ~11 GB GPU+RAM · **O unloaded** |
| **Phase 6 PATCH** | O ~5 GB GPU | O ~5 GB GPU |
| **Teardown** | V1 unloaded | V2 unloaded |

Hardware envelope: RTX 5050 8 GB VRAM + 24 GB RAM. See [[docs/MODELS.md]] §VRAM observations.

---

## Model routing decision tree

```mermaid
flowchart TD
    A([PDF start]) --> B["probe VISION_PRIMARY\nqwen2.5vl:7b — 30s timeout"]
    B --> C{result?}
    C -->|loads| D(["sticky = qwen2.5vl:7b\ncoexists with qwen3:8b"])
    C -->|VisionCallError = RAM fail| E["probe VISION_FALLBACK\nllama3.2-vision:11b"]
    E --> F{result?}
    F -->|loads| G(["sticky = llama3.2-vision:11b\nqwen3:8b MUST be unloaded\nbefore vision phase"])
    F -->|both fail| H(["vision_available = False\n→ text-only path\nall pages: PyMuPDF + OCR only"])

    D --> I["Phase 3 extraction → qwen2.5vl:7b (image_heavy/mixed only)\nPhase 5 judge     → qwen2.5vl:7b (all pages)\nPhase 6 patch     → qwen3:8b (coexist)"]
    G --> J["Phase 3 extraction → llama3.2-vision (image_heavy only — ⚠ slow)\nPhase 5 judge     → llama3.2-vision\nPhase 6 patch     → qwen3:8b (after unload V2)"]
    H --> K["All pages: PyMuPDF spatial + pdfplumber + Tesseract OCR where needed\nNo quality loop (no judge available)"]
```

---

## Extract strategy per page type (Phase 3)

Routing is set by the profiler (Phase 1). No mid-loop model switching.

```mermaid
flowchart LR
    A([page N]) --> B{RouteMap\nstrategy?}

    B -->|text_rich| C["PyMuPDF spatial sort\n+ pdfplumber tables\n→ raw text markdown"]
    B -->|table_heavy| D["pdfplumber tables\nif pdfplumber fails: → vision fallback\n→ raw table markdown"]
    B -->|scanned| E["render page image\n→ Tesseract OCR\n→ raw text"]
    B -->|image_heavy| F["qwen2.5vl:7b full-page vision\n→ markdown directly"]
    B -->|mixed| G["PyMuPDF text blocks\n+ vision for image regions only\n→ combined markdown"]

    C --> H([raw_content[N]])
    D --> H
    E --> H
    F --> H
    G --> H
```

---

## Quality loop — pseudocode (8-phase pipeline)

Reflects D14 (phase-based), D19 (extract once), D20 (FORMAT before PATCH), D21 (profiler routes extraction), D23 (selective vision).

```python
# Phase 0 — Intake
pages = pdf_tools.load_pages(pdf_path)
output_dir = create_output_dir(pdf_path)

# Phase 1 — Page profiler
page_profiles = page_profiler.profile_all(pages)
route_map = page_profiler.build_route_map(page_profiles)

# Phase 2 — Routing (implicit in route_map)

# Phase 3 — Selective extraction
model_router.reset()
_vision_available = _probe_vision()

model_router.before_vision_phase()
raw_content = _extract_by_route(pages, route_map, vision_available=_vision_available)
model_router.before_orchestrator_phase()

# Phase 4 — Format (once) — single qwen3:8b completion, no tools
formatted_md = _run_format_session(raw_content)
if _content_loss_ok(raw_content, formatted_md):        # D5
    current_md = formatted_md

best = RoundResult(score=0.0)

for round_num in 1..MAX_ROUNDS:

    # Phase 5 — Judge (per page, every round)
    model_router.before_vision_phase()
    page_scores = [quality_judge.judge(pg.image, current_md, round_num,
                       model=model_router.get_vision_model()) for pg in pages]
    avg_score, all_gaps = aggregate(page_scores)

    if avg_score > best.score:
        best = RoundResult(round_num, current_md, avg_score, page_scores)

    if best.score >= QUALITY_THRESHOLD:   # D3
        break
    if round_num == MAX_ROUNDS:
        break

    # Phase 6 — Patch
    model_router.before_orchestrator_phase()
    messages = context_manager.compress_history(messages)   # D6
    updated = _run_patch_loop(pages, current_md, all_gaps, messages)
    if _content_loss_ok(current_md, updated):               # D5
        current_md = updated

# Phase 8 — Output
write(best.markdown, output_dir / f"{stem}.md")                              # D2
write(_build_confidence_report(best.page_scores, pdf_name),
      output_dir / f"{stem}_confidence.md")                                  # D24
model_router.teardown_pdf()
```

---

## Module dependency graph

```mermaid
flowchart TD
    config --> pdf_tools
    config --> vision_tools
    config --> model_router
    config --> context_manager
    config --> ocr_tools

    pdf_tools --> page_profiler
    page_profiler --> parser_agent

    config --> system_check
    system_check --> cli

    model_router --> parser_agent
    pdf_tools --> parser_agent
    ocr_tools --> parser_agent
    vision_tools --> parser_agent
    vision_tools --> quality_judge
    quality_judge --> parser_agent
    context_manager --> parser_agent

    cli --> parser_agent
    parser_agent --> output[/"data/markdown/…/final.md\ndata/markdown/…/confidence_report.md"/]
```

---

## Key data types

```python
# profiling/page_profiler.py
@dataclass
class PageProfile:
    page_num: int
    text_length: int          # chars from PyMuPDF
    image_area_ratio: float   # image bbox area / page area
    table_count: int           # pdfplumber tables found on this page
    page_type: str            # "text_rich" | "table_heavy" | "image_heavy" | "scanned" | "mixed"
    needs_ocr: bool
    needs_vision: bool

RouteMap = dict[int, str]     # page_num → "text_rich" | "table_heavy" | "image_heavy" | "scanned" | "mixed"

# quality/quality_judge.py
@dataclass
class PageScore:
    page_num: int
    score: float              # 0.0 – 10.0
    confidence: str           # "High" (≥8.0) | "Medium" (≥5.0) | "Low" (<5.0)
    gaps: list[str]
    action: str               # "accept" | "patch" | "fallback"
    round_num: int
    model: str

# parser_agent.py — internal tracking
@dataclass
class RoundResult:
    round_num: int
    markdown: str
    score: float
    page_scores: list[PageScore]
    gaps: list[str]

# pdf_tools.py — unchanged
@dataclass
class PageData:
    page_num: int
    image: PIL.Image
    width: float
    height: float
    blocks: list[Block]
    regions: list[RegionCrop]
    tables: list[TableData]
```

---

## File I/O

| Input | Path |
|---|---|
| Source PDFs | `data/raw/{specialty}/{condition}.pdf` |
| Output markdown | `data/markdown/{specialty}/{condition}.md` |
| Output confidence report | `data/markdown/{specialty}/{stem}_confidence.md` |
| Page images | In-memory (PIL) — not written to disk |
| Region crops | In-memory only |

---

## Folder structure (post-restructure — D26)

```
cloak/
├── __init__.py
├── config.py
├── cli/
│   ├── __init__.py
│   ├── main.py              ← typer CLI
│   └── system_check.py      ← hardware probe + startup display
├── profiling/
│   ├── __init__.py
│   └── page_profiler.py     ← NEW: heuristic page classification + RouteMap
├── extraction/
│   ├── __init__.py
│   ├── pdf_tools.py         ← moved from ingestion/
│   └── ocr_tools.py         ← NEW: Tesseract OCR wrapper
├── vision/
│   ├── __init__.py
│   └── vision_tools.py      ← moved from ingestion/
├── quality/
│   ├── __init__.py
│   └── quality_judge.py     ← moved from ingestion/
├── orchestration/
│   ├── __init__.py
│   ├── model_router.py      ← moved from ingestion/
│   ├── context_manager.py   ← moved from ingestion/
│   └── parser_agent.py      ← moved + refactored to 8-phase orchestrator
└── ingestion/               ← legacy read-only files only
    ├── pdf_extractor.py
    ├── pdf_classifier.py
    ├── vision.py
    └── markdown_builder.py
```

---

## Hardware constraints

| Resource | Budget | Notes |
|---|---|---|
| GPU VRAM | 8 GB (RTX 5050) | qwen2.5vl:7b + qwen3:8b coexist (~10 GB, spills to RAM) |
| RAM | 24 GB | llama3.2-vision:11b (11 GB) consumes most when loaded |
| Phase rule | One heavy model at a time | enforced by before_vision_phase / before_orchestrator_phase |
| Max tokens per round | 8K | enforced by context_manager |
| Image long edge | 1024px | enforced by vision_tools._prepare_image |
| Min free RAM to start | 9 GB | checked by system_check.ram_gate() — see [[docs/DECISIONS.md]] §D18 |
