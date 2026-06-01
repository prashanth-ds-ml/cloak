---
type: decision-log
updated: 2026-05-30 (Session 26)
---

# Decision Log — cloak

> Related: [[ARCHITECTURE.md]] · [[MODULES.md]] · [[MODELS.md]]

Why things are the way they are. Read before changing any design parameter.

---

## D1 — Iterative quality loop (not one-pass)

**Decision:** Extract → judge → patch, up to 4 rounds.

**Why:** Complex PDFs have multi-column layouts, embedded images, scanned pages, and hand-drawn flowcharts. A single extraction pass always misses something. Scoring against the rendered page image catches gaps that text heuristics cannot.

**Trade-off:** 4× longer than a one-pass pipeline on a bad PDF. Acceptable — these are batch-processed documents, not real-time.

**See:** [[ARCHITECTURE.md]] §Quality loop

---

## D2 — Best round wins (not last round)

**Decision:** Keep the highest-scoring round's output, not the final round.

**Why:** Patching can sometimes degrade quality — an overly aggressive patch might remove context or introduce hallucinated content. Keeping all round scores and returning the peak protects against this.

**Rule:** `RoundResult` list accumulated; `max(rounds, key=lambda r: r.score)` is returned.

---

## D3 — Quality threshold at 8.0 / 10

**Decision:** Stop the loop early when score ≥ 8.0.

**Why:** 8.0 means the judge sees ≤20% of content missing or wrong. For clinical reference documents this is acceptable. 10.0 is unattainable for scanned pages.

**Adjust in:** `config.QUALITY_THRESHOLD`

---

## D4 — Spatial sort by bbox, not PDF draw order

**Decision:** Sort text blocks by bounding box position (column order: left→right, top→bottom within column), not the order pymupdf returns them.

**Why:** Multi-column PDFs are drawn left-column then right-column in the PDF stream, but blocks within a column are not always in reading order. A naive sort breaks table captions, step-numbered lines, and footnote references.

**How:** Two-pass sort — detect column boundary (page midpoint x), then sort within each column by y0. Spanning blocks (width > 55% page width) are interleaved into the left-column stream by their y0.

**See:** [[MODULES.md]] §pdf_tools §spatial_sort

---

## D5 — Content-loss guard at 35%

**Decision:** After any patch round, if `len(new_markdown) < len(original_markdown) * 0.65`, revert to original.

**Why:** qwen3:8b occasionally over-compresses when filling gaps — rewriting a section rather than augmenting it. 35% loss is the threshold where document content is likely being dropped, not just reformatted.

**Adjust in:** `config.CONTENT_LOSS_LIMIT`

**See:** [[MODULES.md]] §parser_agent §Content-loss guard

---

## D6 — Context compression at 8K tokens between rounds

**Decision:** Summarise message history above 8,000 tokens before each new round.

**Why:** qwen3:8b has a large context window, but sending full history of 3–4 rounds balloons the prompt and slows inference. 8K keeps each round's prompt snappy while preserving enough history for coherent patching.

**How:** Keep system prompt + last 2 exchanges intact; summarise everything else into a single message via qwen3:8b.

**See:** [[MODULES.md]] §context_manager

---

## D7 — VRAM rule: llama3.2-vision never coexists with qwen3:8b

**Decision (original):** Before loading `llama3.2-vision:11b`, unload `qwen3:8b`.

**Status (Session 8): superseded.** `llama3.2-vision:11b` has been replaced by `qwen3-vl:4b` (3.3 GB, GPU-only) as `VISION_FALLBACK`. `qwen3-vl:4b` coexists freely with `qwen3:8b` — the coexistence constraint no longer applies to the fallback model.

**Original rationale (preserved for history):** Together they exceeded 8 GB VRAM. Ollama CPU+GPU split caused both models to time out simultaneously. Confirmed in testing 2026-05-15.

**D7 is now a historical record, not an active constraint.** The new VRAM rule is: both `qwen2.5vl:7b` and `qwen3-vl:4b` coexist with `qwen3:8b` safely (both fit in 8 GB VRAM).

**See:** D15 · [[MODELS.md]] §VRAM rules · [[MODULES.md]] §model_router

---

## D8 — New parser replaces old pipeline entirely

**Decision:** `pipeline.py`, `agentic_parser.py`, `vision_extractor.py`, `text_cleaner.py`, `llm_reviewer.py` are deleted.

**Why:** The old pipeline was one-pass with no scoring loop. Running both in parallel would create two maintenance surfaces. Clean break is simpler.

**Legacy files kept read-only:** `pdf_extractor.py`, `pdf_classifier.py`, `vision.py`, `markdown_builder.py`

---

## D9 — RAG, API, FastAPI out of scope

**Decision:** `cloak/rag/`, `cloak/api/`, all LangChain/OpenAI/Weaviate dependencies removed.

**Why:** The primary value is a high-quality local parser. RAG adds infrastructure complexity and cloud dependency. Descoped until parser quality is validated.

**If reintroduced:** Parser output (structured markdown in `data/markdown/`) is already the right RAG chunk format.

---

## D10 — Probe cascade: try both vision models before declaring unavailable

**Decision:** `_probe_vision()` tries `VISION_PRIMARY` first, then `VISION_FALLBACK`. On success, calls `model_router.mark_success()` to set the sticky model for the whole PDF.

**Why:** The original probe tried only `VISION_PRIMARY` and bypassed `model_router` entirely. If primary fails but fallback loads, the system should still enter the quality loop. Discovered as a bug on 2026-05-15.

**See:** `parser_agent._probe_vision()`

---

## D11 — keep_alive on all Ollama chat calls

**Original decision (Session 3):** `MODEL_KEEP_ALIVE = 600` — keep models warm for 600s between rounds to avoid cold reloads.

**Updated (Session 7):** `MODEL_KEEP_ALIVE = 0` — explicit phase-based management via `model_router` handles lifecycle.

**Updated (Session 11):** `MODEL_KEEP_ALIVE = -1` — model stays loaded indefinitely until an explicit unload call. Phase boundaries (`before_vision_phase` / `before_orchestrator_phase`) always fire and always unload the inactive model before the active phase starts.

**Rationale for -1:** `keep_alive=0` unloads the model after every `ollama.chat()` call — including within the Phase 5 judge loop. For a 10-page document this means up to 10 cold reloads per judge round, each taking ~5–10s on a warm GPU. With `-1`, the model stays loaded across all calls within a phase. Explicit phase-boundary unloads (`before_vision_phase` / `before_orchestrator_phase`) still fire to free memory for the next phase. Net effect: zero cold reloads within a phase, predictable memory handoff between phases.

**Unload mechanism:** Ollama accepts `keep_alive=0` in a `/api/generate` POST to forcibly unload regardless of the session keep_alive. `model_router.unload()` uses this for all phase-boundary unloads.

**Adjust in:** `config.MODEL_KEEP_ALIVE`

---

## D12 — num_ctx=4096 on all Ollama calls

**Decision:** Every Ollama call sets `num_ctx=4096` (down from the model default of 8192+).

**Why:** KV cache is proportional to context window. Reducing from 8192 to 4096 roughly halves the cache RAM footprint. For PDF extraction the prompt + response fits easily in 4096 tokens.

**Trade-off:** Limits agent patch loop to ~3000 tokens of history per call — acceptable given context_manager compresses between rounds.

**Adjust in:** `config.MODEL_NUM_CTX`

---

## D14 — Phase-based sequential model routing

**Decision:** Each quality round is split into two explicit phases with hard model boundaries:
- **VISION PHASE** (extract + judge): `before_vision_phase()` called at start.
- **ORCHESTRATOR PHASE** (patch): `before_orchestrator_phase()` called before patch loop.

**Why:** The previous approach switched models reactively per-page. Predictable phase boundaries eliminate hidden latency and make VRAM state auditable at every point in the loop.

**Session 8 update:** With `qwen3-vl:4b` as `VISION_FALLBACK` (3.3 GB), both vision models fit alongside `qwen3:8b` within the 8 GB VRAM pool.

**Session 11 update:** Phase boundaries are now always-fire unconditional unloads. `before_vision_phase()` always unloads the orchestrator; `before_orchestrator_phase()` always unloads the vision model — even when the model pair would technically coexist. This maximises available memory for the active model's auto-split (D32). The sticky vision model is preserved across the orchestrator phase so the next vision phase resumes without re-probing.

**See:** [[ARCHITECTURE.md]] §Phase-based model routing · [[MODELS.md]] §VRAM rules · [[MODULES.md]] §model_router

---

## D15 — llama3.2-vision:11b replaced by qwen3-vl:4b as VISION_FALLBACK

**Original decision (Session 4–7):** `llama3.2-vision:11b` excluded from full-page OCR — times out at 400s. Used only for region crops.

**Session 8 decision:** `VISION_FALLBACK` changed from `llama3.2-vision:11b` to `qwen3-vl:4b`.

**Why the swap:**
- `llama3.2-vision:11b` at 7.8 GB barely fit in 7.6 GB free VRAM and required CPU spill — causing consistent timeouts even for region crops.
- `qwen3-vl:4b` at 3.3 GB loads fully in GPU VRAM. Shows `ready (GPU)` in startup screen. Same VL model family as primary — consistent output format.
- All three pipeline models (`qwen2.5vl:7b`, `qwen3:8b`, `qwen3-vl:4b`) now fit in 8 GB VRAM. No CPU spill.

**What changed in code:**
- `config.py`: `VISION_FALLBACK = "qwen3-vl:4b"`
- `system_check.py`: `_MODEL_VRAM_GB[VISION_FALLBACK]` = 3.5 GB; `_MODEL_RAM_GB[VISION_FALLBACK]` = 4.5 GB
- D7 coexistence constraint no longer applies (see D7).

**See:** [[MODELS.md]] §VRAM observations · [[MODULES.md]] §model_router

---

## D16 — cloak is a general-purpose PDF parser

**Decision:** cloak parses any PDF type — research papers, legal documents, technical manuals, medical guidelines, textbooks, reports. It is not specific to ICMR or medical documents.

**Why:** The core pipeline (extract → judge → patch) is document-agnostic. The vision model extracts whatever is on the page; the judge scores completeness against the rendered image; the orchestrator patches gaps. None of these steps depend on document domain.

**What changes:** All prompts in `vision_tools.py` are domain-neutral. "Medical document parser" → "document parser". Region description prompts (ECG, diagram, figure) are triggered by visual heuristics, not document type — they still work on non-medical PDFs (ECG-shaped aspect ratio images simply won't appear in most non-medical docs).

**What stays:** ICMR PDFs remain the primary test corpus. Prompt tuning may be needed for specific domains as new PDF types are added.

---

## D17 — CLI-first: startup shows hardware + model status

**Decision:** `cloak` (no arguments) shows a startup screen with hardware status and model suitability. `cloak status` also shows it explicitly.

**Session 8 update:** The startup screen is NOT shown on `cloak parse`, `cloak list`, or `--help`. It was removed from `parse` to avoid overhead on batch parsing and to prevent the hardware table from cluttering parse output.

**Why:** The user needs to know whether vision parsing is available — but only when explicitly asking. During a parse run the hardware table is noise. `cloak status` remains the explicit check.

**CLI commands:**
```
cloak                    → startup screen (hardware + model status) + command list
cloak parse <pdf|dir>    → parse PDF(s) — no startup screen
cloak status             → hardware + model status only
cloak list               → list parsed documents in data/markdown/
```

**See:** [[MODULES.md]] §CLI · [[ARCHITECTURE.md]] §Startup screen

---

## D18 — Model suitability check at startup (replaces runtime-only probe)

**Decision:** At startup, cloak queries Ollama for all installed models and checks each against current free RAM/VRAM. This is displayed as a table. The runtime probe (`_probe_vision`) still runs before each PDF parse to confirm the model loads at that moment.

**Why:** Two separate concerns:
1. **Startup display**: "what could potentially work on this machine right now" — informational, runs against installed model list
2. **Runtime probe**: "does the model actually load right now" — authoritative, runs before each parse

The startup display is built from `GET /api/tags` (installed models) + `psutil` (free RAM) + nvidia-smi (VRAM) + known model requirements. It gives the user actionable info before they commit to a parse.

**Model requirements (Session 8 — updated for qwen3-vl:4b fallback):**

| Model | VRAM needed | RAM needed | Role |
|---|---|---|---|
| `qwen2.5vl:7b` | ~7.3 GB | 9.0 GB | Vision primary |
| `qwen3:8b` | ~5.2 GB | 5.5 GB | Orchestrator |
| `qwen3-vl:4b` | ~3.5 GB | 4.5 GB | Vision fallback |

**Suitability priority (total-memory-aware, Session 11):**
1. GPU — model fits fully in VRAM → `ready (GPU)`
2. auto-split — any VRAM present and (VRAM + RAM) ≥ model weight → `ready (auto-split)` — Ollama handles the GPU/RAM split automatically
3. CPU — no GPU but RAM ≥ RAM requirement → `ready (CPU)`
4. unavailable — total memory < model weight

**See:** [[MODULES.md]] §system_check · [[MODELS.md]] §Model suitability table

---

## D19 — Extract once per PDF; rounds 2+ are judge-patch only

**Decision:** Vision extraction (`full_page_extract`) runs only in round 1. Rounds 2 through MAX_ROUNDS judge the improving draft and patch gaps — they do not re-extract from the PDF.

**Why:** The original code re-extracted every round (overwriting the patched draft). This means:
1. Patch work from round N was discarded at the start of round N+1
2. Time was wasted on identical extractions (model is deterministic at temp=0.1)

With extract-once, each round's patch genuinely improves the draft that the next round's judge evaluates. The quality loop is now truly iterative.

**Trade-off:** If the vision model produces a bad round-1 extraction, there is no second extraction attempt. The content-loss guard (D5) and best-round tracking (D2) protect against this.

**Replaces:** The re-extract behaviour in the original `parse()` loop.

**See:** [[ARCHITECTURE.md]] §Correct pipeline · [[MODULES.md]] §parser_agent

---

## D20 — FORMAT step integrated into round 1 patch session

**Decision:** The first `qwen3:8b` patch session (end of round 1) runs two tasks in sequence:
1. **FORMAT**: restructure raw extracted content into clean markdown — proper `##` headings, readable tables, decision flows with `→`, lists for enumerations
2. **PATCH**: fill the content gaps identified by the round 1 judge

**Why:** The live run showed that raw pymupdf text + broken pdfplumber tables produce unreadable output even at 8.0/10 judge score. The judge scores *content completeness*, not *formatting quality*. Without an explicit format step, well-extracted content is unreadable.

Rounds 2+ patch sessions do NOT re-format — they only fill remaining content gaps. The structure is set in round 1 and refined from there.

**What FORMAT does:**
- Identifies document sections and adds `##` headings
- Converts broken multi-cell table dumps into readable markdown tables
- Represents decision flowcharts as structured text with `→`
- Formats enumerated items (drug dosages, numbered steps) as proper lists
- Moves footnotes/references to the bottom

**See:** [[MODULES.md]] §parser_agent §FORMAT prompt · [[ARCHITECTURE.md]] §Round 1

---

## D21 — Page profiler is the routing backbone (heuristic, zero models)

**Decision:** Before any extraction, every page is classified heuristically into one of five types: `text_rich`, `table_heavy`, `image_heavy`, `scanned`, `mixed`. This classification drives all subsequent routing decisions.

**Why:** The previous pipeline applied the same extract→judge→patch loop uniformly to every page regardless of content. For "any PDF" production accuracy this is wrong — a scanned page needs OCR, a table-heavy page needs pdfplumber, an image-heavy page needs vision. Without a profiler, the pipeline either wastes time running vision on clean-text pages or silently fails on scanned pages (returning empty blocks). The profiler costs nothing — it runs on data already available from `pdf_tools.load_pages()`.

**Classification signals:**
- `text_length` (chars from PyMuPDF) — low → likely scanned
- `image_area_ratio` (image bbox area / page area) — high → image_heavy
- `table_block_count` (pdfplumber word position density) — high → table_heavy
- `text_length < 100 AND image_area_ratio > 0.4` → scanned
- Everything else → text_rich or mixed by combination

**Output:** `PageProfile[]` and a `RouteMap` (page_num → extraction strategy).

**See:** [[docs/MODULES.md]] §page_profiler

---

## D22 — Tesseract OCR only for scanned pages (PaddleOCR deferred)

**Decision:** Use `pytesseract` (Tesseract backend) as the OCR engine for scanned pages. PaddleOCR is not added at this stage.

**Why:** Tesseract is lightweight, well-maintained, and handles the majority of scanned PDF cases (English text, clean scans, rotated pages). PaddleOCR offers better multilingual accuracy and table OCR but adds ~500 MB of model weight and heavier Python dependencies. Given that the current test corpus is English-language documents, Tesseract is sufficient. PaddleOCR can be added as a high-quality OCR path later.

**What Tesseract handles:** clean scans, mildly rotated pages, greyscale documents, standard fonts.
**What it doesn't handle well:** handwriting, very low contrast, mixed-script documents.

**Adjust if:** multilingual PDFs become a primary use case — add PaddleOCR then.

**See:** [[docs/MODULES.md]] §ocr_tools

---

## D23 — Vision extraction strategy for text_rich pages

**Original decision (Session 7):** In Phase 3, vision is called only for `image_heavy` or `mixed` pages. `text_rich` pages use pdfplumber flat text + a separate `layout_hints` vision call (heading detection as JSON) which FORMAT then applied.

**Session 8 update:** `text_rich` pages now call `full_page_extract()` directly (same as `image_heavy`). The separate `layout_hints` vision call has been removed entirely.

**Why the change:** The `layout_hints` approach was fragile:
- Vision returns heading JSON → FORMAT reads flat text + applies headings → two-step with mismatch potential
- FORMAT was often ignoring or misapplying the layout hints because the raw text had no structure to anchor them to
- Headings were being lost consistently in test runs

**New approach (`_extract_text_page_vision`):**
- `full_page_extract()` reads the visual layout directly → assigns `##`/`###` headings in one pass
- Same function now handles `text_rich`, `image_heavy`, and `mixed` pages when vision is available
- `layout_hints()` and `_build_layout_context()` functions deleted entirely

**Routing in practice:**
- If vision available: ALL pages use `_extract_text_page_vision()` — vision reads layout and headings
- If vision unavailable: `text_rich`/`table_heavy`/`scanned` fall back to pdfplumber/OCR

**Phase 5 (Judge) still runs vision on every page** — this is unchanged.

**See:** [[docs/ARCHITECTURE.md]] §Phase 3 extraction · [[docs/MODULES.md]] §page_profiler · [[docs/MODULES.md]] §parser_agent

---

## D24 — Per-page confidence scores in output (confidence_report.md)

**Decision:** The final output includes a `confidence_report.md` alongside `final.md`. It lists each page's confidence level (High / Medium / Low) based on the final judge score, plus any unresolved gaps.

**Why:** For production use, the user needs to know which pages to manually review. A single aggregate quality score for the whole document does not surface page-level problems. A low-confidence flag on page 12 is actionable; a document-level score of 6.5 is not.

**Format:** Human-readable markdown, not JSON (JSON output deferred to a later milestone).

```
## Confidence Report

| Page | Confidence | Notes |
|---|---|---|
| 1 | High | — |
| 7 | Medium | table structure uncertain |
| 12 | Low | scanned, OCR confidence low — review recommended |
```

**Thresholds:** score ≥ 8.0 → High, ≥ 5.0 → Medium, < 5.0 → Low.

**See:** [[docs/MODULES.md]] §parser_agent §Output

---

## D25 — No Camelot: vision fallback for complex tables

**Decision:** Camelot is not added to the dependency stack. Complex tables (borderless, merged cells, multi-page) that pdfplumber fails on are handled by sending the page to `qwen2.5vl:7b` vision extraction instead.

**Why:** Camelot requires Ghostscript as a non-Python system dependency. This adds an installation requirement outside `pip install`, complicates setup on Windows and Linux alike, and creates a failure mode that is hard to debug (Ghostscript version mismatches, PATH issues). The vision fallback for complex tables is slower but produces acceptable quality and requires no additional system dependency.

**What pdfplumber handles:** bordered tables, simple grids, most well-formed PDF tables.
**What vision handles:** borderless tables, merged cells, rotated tables, form-like layouts.

**Add Camelot if:** pdfplumber + vision proves insufficient for a specific table-heavy corpus in production testing.

---

## D26 — Folder structure: restructure before adding new modules

**Decision:** Before implementing page_profiler and ocr_tools, restructure `cloak/ingestion/` into purpose-named subpackages. New structure:

```
cloak/
  profiling/    ← page_profiler.py (new)
  extraction/   ← pdf_tools.py (moved), ocr_tools.py (new)
  vision/       ← vision_tools.py (moved)
  quality/      ← quality_judge.py (moved)
  orchestration/← model_router.py, context_manager.py, parser_agent.py (moved)
  ingestion/    ← legacy read-only files only
  cli/          ← unchanged
  system_check.py (new, top-level)
```

**Why:** Adding page_profiler and ocr_tools to the flat `ingestion/` directory alongside 4 legacy read-only files would make the module purpose unclear and imports confusing. Restructuring first gives each concern a clean home and matches the design doc's intended layout.

**What moves:** `pdf_tools`, `vision_tools`, `quality_judge`, `model_router`, `context_manager`, `parser_agent` — imports updated in all files after the move.
**What stays in `ingestion/`:** legacy files `pdf_extractor.py`, `pdf_classifier.py`, `vision.py`, `markdown_builder.py` — read-only, untouched.

**Implementation order:** restructure → verify imports → then add new modules.

---

## D27 — Phase 9: post-pipeline deep quality review (gemma4:latest)

**Decision:** After the pipeline completes and all models are unloaded via `teardown_pdf()`, a larger review model (`gemma4:latest`, 9.6 GB) is loaded to compare the raw pdfplumber text (ground truth) against the final markdown and write an actionable quality improvement report.

**Why:** The pipeline's judge (qwen2.5vl:7b) scores completeness per page during extraction. But after all rounds complete, there is no final holistic check comparing what pdfplumber actually saw in the text layer vs what ended up in the markdown. A post-pipeline auditor with a larger context and no VRAM constraints catches structural gaps, missing headings, and table issues that the per-page judge scores in aggregate may not surface.

**Memory strategy:** `teardown_pdf()` unloads all pipeline models (~6 GB VRAM freed). `gemma4:latest` at 9.6 GB exceeds available VRAM but Ollama automatically places it across GPU VRAM + CPU RAM (CPU+GPU split). No code change needed for the split.

**Output:** `{stem}_review.md` in same directory as `final.md`. Structured report with: Missing Content, Wrong/Missing Headings, Table Issues, Duplicate Content, Formatting Problems, Overall Assessment, Quality Score (0–10), Priority Fixes.

**Config:**
```python
DEEP_REVIEW_MODEL   = "gemma4:latest"  # 9.6 GB
DEEP_REVIEW_TIMEOUT = 600              # 10 min — CPU+GPU split is slower
```

**Opt-out:** `cloak parse --no-review` skips Phase 9. Default is to run it.

**Implementation:** `cloak/quality/deep_review.py` — `run(pdf_path, pages, final_markdown, review_out, console) -> Path | None`. Always calls `_unload()` in finally block regardless of success.

**See:** [[docs/MODULES.md]] §11 · [[ARCHITECTURE.md]] §Full pipeline

---

## D28 — Two-level profiler: DocProfile + ParsePlan

**Decision:** Before loading any model, run two profiling steps:
1. **Page-level** (extends D21) — heuristic or docling element map per page
2. **Document-level** — aggregate page profiles into a `DocProfile`; use it to generate a `ParsePlan`

**DocProfile fields:**
```python
@dataclass
class DocProfile:
    page_count:         int
    type_distribution:  dict[str, float]   # {"text_rich": 0.82, "image_heavy": 0.12, ...}
    vision_dependency:  str                # "none" | "low" | "medium" | "high"
    complexity_score:   float              # 0.0–1.0; drives round budget
    size_tier:          str                # "small" | "medium" | "large" | "huge"
```

**ParsePlan fields:**
```python
@dataclass
class ParsePlan:
    model_tier:         str                # "none" | "fallback" | "primary"
    max_rounds:         int                # adaptive — see table below
    judge_sample_rate:  float              # fraction of pages to judge per round
    use_docling:        bool               # True when docling is installed
```

**Adaptive round budget (from size_tier + complexity_score):**

| Size tier | Pages | Base rounds | Judge sample |
|---|---|---|---|
| small | < 50 | 4 | 100% |
| medium | 50–200 | 3 | 60% |
| large | 200–500 | 2 | 30% |
| huge | > 500 | 1 | 10% |

Complexity adds/subtracts: `complexity_score > 0.6` → +1 round; `< 0.3` → −1 round (min 1).

**Vision dependency routing:**
- `none` (< 5% image/mixed/scanned) → skip vision probe entirely; pdfplumber + docling only
- `low` (5–20%) → load `VISION_FALLBACK` only
- `medium` (20–50%) → try `VISION_PRIMARY`, fallback to `VISION_FALLBACK`
- `high` (> 50%) → always try `VISION_PRIMARY` first

**Why:** The probe runs 30s per PDF even when 95% of pages are clean text. DocProfile computed at zero model cost eliminates blind model loading decisions. ParsePlan is the agent's contract with itself — all downstream phases execute the plan, not fixed constants.

**See:** D21 (page profiler) · D29 (docling) · [[MODULES.md]] §page_profiler

---

## D29 — Docling as structural extraction foundation

**Decision:** Docling runs as a layout analysis pass in Phase 1, before any model is loaded. It produces a structured element map per page that drives all extraction decisions.

**What docling classifies (per page):**
```
SectionHeaderItem (level=1,2,3)  →  ## / ### / #### headings
TextItem                          →  body paragraph
TableItem (with cell structure)   →  markdown table
FigureItem (with caption)         →  figure bbox + caption text
ListItem                          →  bullet or numbered item
FootnoteItem                      →  collected, appended at section end
FormulaItem                       →  equation (described by vision or LaTeX)
PageHeader / PageFooter           →  DISCARDED — never pollutes content
```

Reading order across columns is reconstructed by docling's layout model (trained on DocLayNet, 80K+ diverse document pages). This is more reliable than our current spatial_sort heuristic.

**What this changes in the pipeline:**
- Headings are extracted at the correct H1/H2/H3 level — no more font-size guessing
- Page headers/footers are excluded — no more "Chapter 3" repeated 40 times in output
- Footnotes are collected and linked — not orphaned in body text
- Multi-column reading order is correct from day one
- FORMAT step becomes light cleanup only — structure is already correct from docling

**Vision model's new focused role** (narrowed from current):
- Figure/diagram description (`region_describe`) — docling finds the bbox; vision describes it
- Quality judging (`judge_quality`) — comparing markdown vs source image
- Patches for complex visual content that docling + pdfplumber miss
- Vision is NOT called for heading extraction, text layout, or column ordering on text_rich pages

**Fallback when docling not installed:**
- Phase 1 reverts to D21 heuristic page_profiler
- Extraction falls back to current vision-for-all-pages approach (D23)
- Docling status shown at `cloak status` / `cloak doctor` (planned)

**Why:** The core data-loss problems (wrong reading order, lost headings, page header pollution, orphaned footnotes) are all structural problems. No amount of judge→patch rounds recovers wrong reading order — the structural information must be captured at extraction time.

**Dependency:** `pip install docling` — downloads 258 MB layout model on first run, cached afterward.

**See:** D21 · D28 · [[MODULES.md]] §page_profiler · [[MODULES.md]] §extraction

---

## D30 — Surya as primary OCR for scanned pages

**Decision:** Replace Tesseract as the primary OCR engine for scanned pages with Surya. Tesseract kept as fallback.

**Why Surya over Tesseract:**
- Surya detects reading order and layout in addition to recognising characters — critical for scanned multi-column documents
- 90+ language support without separate language pack installation
- Better accuracy on low-contrast, mildly rotated, and mixed-font scans
- GPU-accelerated on RTX 5050 — fast in practice

**Fallback chain for scanned pages:**
```
surya OCR → tesseract fallback → raw PyMuPDF text blocks (last resort)
```

**CPU note:** Surya requires GPU for acceptable speed. On CPU it is slower than Tesseract. On RTX 5050 (8 GB VRAM) it runs fast. The ParsePlan (D28) accounts for this: pages classified `scanned` in the DocProfile flag `use_surya = True` only when GPU is available.

**Config:**
```python
OCR_PRIMARY  = "surya"      # preferred OCR engine
OCR_FALLBACK = "tesseract"  # fallback if surya not installed or GPU unavailable
```

**Status: Implemented (Session 18)** — `cloak/extraction/ocr_tools.py`. Two fixes required:
1. API change: `RecognitionPredictor` now requires `FoundationPredictor` as first arg. Updated `_load_surya()` to create `FoundationPredictor`, `DetectionPredictor`, then `RecognitionPredictor(foundation)`.
2. `transformers>=5.0` broke `SuryaDecoderConfig` — `pad_token_id` access now raises `AttributeError` instead of returning `None`. Pinned `transformers>=4.56.1,<5.0` in `pyproject.toml`. Confirmed `4.57.6` works with both surya and docling.

**See:** D22 (Tesseract decision) · [[MODULES.md]] §ocr_tools

---

## D31 — Markdown output standard and structural fidelity scoring

**Decision:** Define a concrete markdown output standard that all extraction, FORMAT, and patch steps must preserve. Add structural fidelity as a second scoring axis alongside content completeness.

**Markdown output standard:**
```markdown
# Document Title

## 1. Section Heading

Body text with correct reading order. Multi-column text flows
correctly — no mid-sentence breaks.

### 1.1 Sub-section

> **Table 1: Caption**
| Col A | Col B |
|-------|-------|
| val   | val   |

![Figure 1](stem_images/figure_1.png)
*Figure 1: Caption text extracted from document*

---
**Footnotes**
[^1]: Full footnote text linked to its reference
```

**Structural fidelity signals (added to judge scoring):**
- Headings present and at correct hierarchy levels (H1 > H2 > H3)
- Tables complete — all rows, all columns, header row present
- Figure captions attached to the correct figure
- Footnotes present and linked (not orphaned)
- No page header/footer text polluting body content

**Combined quality score:**
```
content_score     = judge completeness vs source image  (existing)
structure_score   = structural fidelity signals above   (new)
final_score       = 0.7 * content_score + 0.3 * structure_score
```

**Why:** A page can score 9/10 on content completeness but be unreadable because headings are missing or reading order is broken. The current judge only sees content gaps. Structural fidelity makes quality scoring honest.

**See:** D24 (confidence report) · D29 (docling structure) · [[MODULES.md]] §quality_judge

---

## D32 — Total-memory model routing (VRAM + RAM pool)

**Decision (Session 11):** Model viability is determined by `total_free = free_vram + free_ram`, not VRAM alone. A model is viable when `total_free >= model_weight_gb`. Ollama automatically places as many layers as possible on GPU and spills the remainder to CPU RAM (auto-split) — no code change needed.

**Why:** With 8 GB VRAM and 24 GB RAM, `qwen2.5vl:7b` (7.3 GB) requires only ~0.3 GB from RAM when ~7.0 GB VRAM is free. The old VRAM-only check was incorrectly routing to the 4b fallback when qwen2.5vl:7b would have loaded fine via Ollama's auto-split. The new check correctly classifies this as `ready (auto-split)`.

**Hardware tiers served:**
- Entry (16 GB RAM / 6 GB VRAM): total ~22 GB — covers all three pipeline models
- Mid (24 GB RAM / 8 GB VRAM): total ~32 GB — primary model fully in VRAM
- High (32 GB RAM / 12+ GB VRAM): total ~44 GB — gemma4 may also fit entirely in VRAM

**Model weights used:**
```python
_MODEL_SIZE_GB = {
    VISION_PRIMARY:     7.3,   # qwen2.5vl:7b
    VISION_FALLBACK:    3.5,   # qwen3-vl:4b
    ORCHESTRATOR_MODEL: 5.2,   # qwen3:8b
}
```

**What changed in code (Session 11):**
- `model_router.py`: replaced `_VISION_PRIMARY_VRAM_GB` with `_MODEL_SIZE_GB` dict; `vision_models_to_try()` uses `free_vram + free_ram >= model_weight`
- `system_check.py`: `check_model_suitability()` now shows `ready (auto-split)` for models that span GPU+RAM; removed 85% marginal band; `run_startup_cleanup()` warning threshold uses total memory
- `profiling/doc_profiler.py`: `build_parse_plan()` param renamed `gpu_available` → `primary_viable`; viability = total memory check, not GPU-only
- `orchestration/parser_agent.py`: `_gpu_est` uses `(free_vram + free_ram) >= primary_model_size`

**See:** D11 (keep_alive -1) · D14 (phase boundaries) · [[MODELS.md]] §Model suitability table

---

## D13 — MAX_IMAGE_PX=1024 long-edge cap before sending to VLM

**Decision:** All images (full pages, region crops) are resized so their long edge ≤ 1024px before encoding as PNG and sending to the vision model.

**Why:** PDFs render at 1754×3404px at 150 DPI. Sending that to a VLM uses excessive image tokens (more memory + slower inference). 1024px long edge preserves text readability while halving the token count.

**Trade-off:** Very small text in footnotes may become harder to read. Acceptable for documents where the main content is in large-font body text.

**Adjust in:** `config.MAX_IMAGE_PX`

---

## D33 — Per-page judge routing: heuristic scoring for text_rich pages

**Decision:** In Phase 5 (judge), branch on `route_map` + `needs_vision` per page:
- `text_rich` or `table_heavy` with `needs_vision=False` → **heuristic judge** (word overlap + structural score, no model call)
- `image_heavy`, `mixed`, `scanned` → **vision judge** (full VLM call, as before)

**Why:** The profiler (Phase 1) already knows which pages have no figure elements — `update_vision_from_docling()` sets `needs_vision=False` for pages with zero `PictureItem` elements. But the judge loop was ignoring this and calling the vision model for every page regardless of type.

Observed impact: engineering_thermo (10 pages, all `text_rich`, 0 figures) spent 2077s in the judge phase for zero information gain. A word-overlap heuristic gives equivalent scoring in < 1s.

**Heuristic judge logic:**
```python
def heuristic_judge(page_num, page_text, page_md, round_num) -> PageScore:
    raw_words = _word_set(page_text)      # pdfplumber ground truth
    md_words  = _word_set(page_md)        # extracted markdown for this page
    completeness = len(raw_words & md_words) / len(raw_words) if raw_words else 1.0
    content_score = round(completeness * 10, 1)
    structure_score = _compute_structure_score(page_md)
    score = round(0.7 * content_score + 0.3 * structure_score, 1)
    ...
```

**Expected speedup:** 5–10× on Phase 5 for typical documents (most pages are `text_rich`). Image-heavy documents (posters, slides, figures) are unchanged — they still get full vision judging.

**Implementation:** `quality_judge.heuristic_judge()` + routing branch in `parser_agent.py` judge loop.

**Status: Implemented (Session 17)** — `cloak/quality/quality_judge.py` + `cloak/orchestration/parser_agent.py`

**See:** D21 (page profiler) · D28 (ParsePlan) · D29 (docling `needs_vision`)

---

## D34 — JSON parse failure fallback in quality_judge

**Decision:** When the vision model returns non-JSON in `judge_quality()`, do not crash to a default score of 0.0/2.7. Instead, attempt regex extraction of numeric scores from the free-form response, then fall back to structural scoring only.

**Why:** The audit of 34 parsed documents found 13 pages across 2 documents (engineering_thermo, irs_pub17) scored 2.7/10 due to `json.loads()` failure. The vision model returned explanatory text ("The page shows a dense tax table...") instead of the required JSON on complex worksheet pages. A 2.7 floor cascades: those pages hit "fallback" action, patch agent gives up, final score is dragged down to 4.0/4.4.

**Fallback chain:**
```
1. json.loads(raw)                           → preferred
2. re.search(r'"score":\s*([\d.]+)', raw)    → extract numeric score from prose
3. re.search(r'\b(\d+(?:\.\d+)?)\s*/\s*10', raw)  → look for "X/10" pattern
4. structure_score only (no content penalty) → last resort, logs warning
```

**Implementation:** In `vision_tools.judge_quality()` — replace bare `json.loads()` with the fallback chain.

**Status: Implemented (Session 17)** — `cloak/vision/vision_tools.py`

**See:** D31 (quality scoring) · [[MODULES.md]] §quality_judge

---

## D35 — Math equation extraction: pix2tex + nougat integration

**Decision:** Add a math OCR layer for pages classified as `math_heavy`. Two engines:

| Engine | Use case | Model size | Speed |
|---|---|---|---|
| **pix2tex** | Individual equation crops (docling `FormulaItem` bbox) | ~100 MB | Fast (GPU) |
| **nougat** | Full academic pages when >50% content is math | ~350 MB | Medium (GPU) |

**Why:** The audit found a hard ceiling at 6.2/10 for math-heavy documents (JEE question papers, engineering textbooks, academic papers with derivations). Vision models describe equations in prose ("a quadratic equation...") rather than extracting the LaTeX. pdfplumber returns nothing for embedded equation images. Without a math OCR layer, question papers are unsolvable by cloak.

**New page type: `math_heavy`**
Added to the `page_profiler` and `RouteMap`. Detected when docling finds ≥ 3 `FormulaItem` elements on a page, or when `image_area_ratio > 0.3` AND equation-like aspect ratio (wide, short bounding boxes).

**Extraction flow for `math_heavy` pages:**
```
Phase 1: docling → FormulaItem list with bbox_norm per page
Phase 3 extract:
  for each FormulaItem:
    crop = _crop_normalized(page_image, el.bbox_norm)
    latex = math_ocr.pix2tex_equation(crop)
    replace placeholder in markdown: $latex$

  If page is nougat-mode (>50% math):
    nougat_md = math_ocr.nougat_page(page_image)
    use nougat_md as primary; pdfplumber text as fallback
```

**Output format:**
- Inline equations: `$E = mc^2$`
- Block equations: `$$\int_0^\infty e^{-x} dx = 1$$`
- Fallback (pix2tex timeout): `[EQUATION]` placeholder with figure image saved

**ParsePlan additions:**
```python
@dataclass
class ParsePlan:
    ...
    use_math_ocr:    bool   # True when math_heavy pages detected
    math_ocr_engine: str    # "pix2tex" | "nougat" | "none"
```

**Config:**
```python
MATH_OCR_ENGINE   = "pix2tex"   # primary engine; "nougat" for full-page academic mode
MATH_OCR_TIMEOUT  = 30          # per equation crop (pix2tex is fast)
NOUGAT_TIMEOUT    = 120         # per page (nougat is heavier)
```

**Installation:**
```
pip install pix2tex        # ~100 MB download
pip install nougat-ocr     # ~350 MB download (optional — for full-page academic mode)
```

**Fallback when not installed:** `math_ocr.py` returns `[EQUATION]` placeholder — pipeline continues unchanged. No crash.

**Expected quality impact:** JEE question papers: 6.2 → ~8.0. Engineering textbooks: 3.9 → ~7.0. Research papers with derivations: small improvement (docling already handles most text).

**New module:** `cloak/extraction/math_ocr.py` — see [[MODULES.md]] §math_ocr

**Status: Implemented (Session 19)** — pix2tex only (nougat deferred). Detection is doc-level: `formula_count ≥ MATH_FORMULA_THRESHOLD (3)` across all pages, set in `build_doc_profile()`. Output: `$$\n{latex}\n$$` display blocks; falls back to `` `text` `` when pix2tex returns empty. No `math_heavy` page type added — detection lives in DocProfile/ParsePlan, not RouteMap. End-to-end test on engineering_thermo pending.

**See:** D22 (OCR stack) · D29 (docling FormulaItem) · [[MODULES.md]] §math_ocr

---

## D36 — Docling reading order fix: spatial sort per page

**Decision:** After `run_docling_pass()`, sort each page's `DoclingElement` list by `(bbox_norm.y, bbox_norm.x)` before extraction. Applies within each page only — cross-page order is always correct (page 0 → 1 → 2…).

**Why:** The audit found engineering_thermo and irs_pub17 had pages extracted out of sequence within their content — title page content mixed with Chapter 2 text, TOC appearing after body paragraphs. Docling's `iterate_items()` returns elements in PDF object order, which for complex multi-column or multi-section layouts does not match visual reading order.

**Sort key:** `(bbox_norm.y, bbox_norm.x)` — top-to-bottom primary, left-to-right secondary. This correctly handles:
- Single-column text: top → bottom within column
- Multi-column: left column above right column when rows are at different y-levels
- Title + body: title (y≈0.05) always before body (y≈0.15+)

**Where to apply:** In `doc_profiler._add_item()` — collect per page, then sort before returning `element_map`.

**Trade-off:** Doesn't handle true multi-column interleaving (where left and right column paragraphs alternate at the same y-level). That requires column detection (D4 spatial_sort logic). For now, pure y-sort is a large improvement over unsorted PDF object order.

**Status: Implemented (Session 17)** — `cloak/profiling/doc_profiler.py`

**See:** D4 (spatial sort for pdfplumber) · D29 (docling) · [[MODULES.md]] §doc_profiler

---

## D37 — Phase boundary: skip `before_orchestrator_phase()` when format is not needed

**Decision:** Only call `model_router.before_orchestrator_phase()` (which unloads the vision model) if format will actually run (`needs_fmt=True`). When format is skipped, the vision model stays loaded and can be used immediately for Phase 5 judge without a cold reload.

**Why:** The current code calls `before_orchestrator_phase()` unconditionally after Phase 3 extract, even when `_content_needs_format()` returns False. This unloads the vision model. If format is then skipped, nothing loads to replace it. Phase 5 judge must cold-reload the vision model from disk (6.1 GB = ~30–60s delay per round).

Observed: engineering_thermo (format skipped) — 2 unnecessary model reloads per round.

**Change:**
```python
# Before (unconditional):
model_router.before_orchestrator_phase()
if needs_fmt:
    markdown = _run_format_session(raw_content)

# After (conditional):
if needs_fmt:
    model_router.before_orchestrator_phase()
    markdown = _run_format_session(raw_content)
else:
    markdown = raw_content
# vision model stays loaded for Phase 5
```

Phase 6 patch already has its own `before_orchestrator_phase()` call — that contract is unchanged.

**Status: Implemented (Session 17)** — `cloak/orchestration/parser_agent.py`

**See:** D11 (keep_alive) · D14 (phase boundaries) · D20 (FORMAT step)

---

## D38 — Slide deck per-slide VLM mode

**Decision:** When the DocProfile shows `image_heavy` fraction > 70% AND `page_count` is consistent with a slide deck (typically 1–3 figures per page, minimal pdfplumber text), switch to **per-slide VLM extraction mode**: render each page and send it to the vision model as a single full-page image with the slide description prompt.

**Why:** The current pipeline sends full slide pages to `full_page_extract()`, which merges all slide text and images into prose. Slide structure (title → bullets → figure) is lost. The audit found MIT OCW biology lecture stuck at 6.2/10 because 10 slides were collapsed into flowing text paragraphs.

**Per-slide mode:**
- Each page is a complete "document" for extraction purposes
- Use `full_page_extract()` with a **slide-specific prompt** (preserve title, bullet hierarchy, figure captions)
- No FORMAT step — slide structure is set by the VLM directly
- Judge still runs per page

**New slide prompt:**
```
You are extracting a presentation slide into structured markdown.
Preserve:
- Slide title as ## heading
- Bullet points at correct indent level (-, --, ---)
- Figure descriptions as [Figure: caption]
- Speaker notes (if visible) as > blockquote
Output only the markdown. No preamble.
```

**DocProfile signal:** `size_tier = "small"` + `type_distribution["image_heavy"] > 0.70` → `ParsePlan.slide_mode = True`

**Expected impact:** MIT OCW: 6.2 → ~7.5–8.0. Slide decks in general: extraction preserves per-slide structure.

**Config:** No new constants needed — uses existing `VISION_TIMEOUT` and `MAX_IMAGE_PX`.

**See:** D21 (page profiler) · D28 (ParsePlan) · D29 (docling)

---

## D39 — Exam mode for JEE / GATE / ESE papers

**Decision:** Detect exam question papers at parse time using a regex scan of the first 5 pages. When detected, bypass docling text extraction for 	ext_rich and mixed pages and instead send each page to the VLM with an exam-specific prompt that preserves question numbers, answer options, and LaTeX equations.

**Why:** JEE/GATE/ESE papers store equations using Symbol font with positional layout rendering. PyMuPDF and pdfplumber extract these as garbled glyph codes (\n2\n4\ny\nax\n) that are unreadable. Docling does not detect them as FormulaItems. The only reliable extraction path is full-page vision with a prompt tuned for exam structure.

**Detection regex (first 5 pages):**
`python
r'Q\.?\s*\d+\b'                           # Q.1 / Q1
r'Maximum\s+Marks'
r'GATE\s+\d{4}'
r'JEE.{0,20}(?:Advanced|Main)'
r'ESE\s+\d{4}|IES\s+\d{4}|UPSC\s+(?:ESE|IES)'
`

**Exam prompt rules:** question numbers → preserve as-is, equations → LaTeX \$...\$, answer options → separate lines, diagrams → [Figure: description].

**Known issue (Session 21):** Original regex also included SECTION\s+[0-9IVX]+ which falsely triggered on any document with section numbers. Removed in D41 fix.

**Config:** ParsePlan.exam_mode · no new config constants.

**See:** D38 (slide_mode) · D35 (FormulaItem math OCR) · D28 (ParsePlan)

---

## D40 — Mathpix cloud math OCR (opt-in infrastructure)

**Decision:** Add Mathpix as a dual-backend to math_ocr.py alongside pix2tex. Default remains MATH_OCR_ENGINE="pix2tex" (fully local). Mathpix activates only when MATHPIX_APP_ID and MATHPIX_APP_KEY are set in .cloak_local.json.

**Why:** Mathpix produces LlamaParse-quality LaTeX for complex equations (hand-drawn, multi-line, non-standard notation). pix2tex handles standard printed equations well but degrades on complex notation. Mathpix is cloud-based and paid — not suitable as a default for a local-only tool.

**Not used by default.** The Mathpix code is present and tested but intentionally disabled. If a user has Mathpix keys and needs maximum math quality, they can activate it without code changes.

**API:** math_ocr.ocr_equation(image) dispatches via _active_engine(). math_ocr.ocr_page(image) for full-page Mathpix (used by exam_mode fallback path, inactive without keys).

**See:** D35 (pix2tex FormulaItem OCR) · D39 (exam_mode)

---

## D41 — Fix: exam_mode false positive (tighten detection regex)

**Decision (IMPLEMENTED Session 22):** Remove `SECTION\s+[0-9IVX]+` from `_detect_exam_paper()`. This pattern fired on any structured document with section headings ("Section 3 Methodology", "Section IV Background"). Replaced with stricter definitive exam-only markers: `Q\.?\s*\d+\b`, `Maximum\s+Marks`, `GATE\s+\d{4}`, `JEE.{0,20}(?:Advanced|Main)`, `ESE\s+\d{4}|IES\s+\d{4}`, `UPSC\s+(?:ESE|IES)`, `(?:PART|PAPER)\s+[A-Z0-9]\b.*Marks`.

**Verified:** "Section 3 discusses methodology" → False. "GATE 2024 Computer Science" → True. "JEE Advanced 2023 — Maximum Marks: 180" → True. BERT paper no longer triggers exam_mode; score improved from 7.0 → 8.4.

**Why:** Session 21 benchmark found BERT research paper and ECHR legal judgment both triggered exam_mode due to this pattern. BERT's word capture dropped from ~90% to 50% (exam prompt restructured academic prose). Any document with section numbers is at risk.

**Impact:** All text-heavy documents (legal, financial, academic, technical) were potentially affected. Fix is a one-line regex change in _detect_exam_paper().

**See:** D39 (exam_mode)

---

## D42 — Fix: heuristic judge for text-only documents

**Decision (IMPLEMENTED Session 22):** When `vision_available=False` (model_tier="none"), run `heuristic_judge` on all pages before writing the confidence report. The heuristic judge uses word-overlap scoring — no model needed. `compute_metrics(heur_scores, ...)` produces a `QualityMetrics` with `judged=True`, so the `Judge Score` row appears in the confidence report. The registry also now receives the actual judge_score instead of None.

**Why:** Session 21 benchmark found that legal documents (SCOTUS), financial reports (Berkshire), and technical manuals (PostgreSQL) all showed only Completeness in their confidence reports — no Judge Score. This made it impossible to evaluate or track quality for the most common document types in production use.

**Result:** SCOTUS 9.2, Berkshire 9.1, PostgreSQL 8.7, ECHR 9.9, CDC NCHS 9.6 — all previously ERR, now fully scored.

**Files:** `cloak/orchestration/parser_agent.py` — text-only path; `scripts/benchmark.py` — `_confidence_path()` subdirectory fix.

**Implementation:** In parse_pdf(), after Phase 4 FORMAT, always enter the judge loop. Gate only the vision-judge calls on ision_available; the heuristic path runs unconditionally. Write Judge Score to confidence report regardless.

**See:** D33 (heuristic_judge) · D31 (quality judge design)

---

## D43 — Unload orchestrator before vision probe (fix 4b fallback regression)

**Decision (IMPLEMENTED Session 22):** In Phase 2 (model staging), unload the orchestrator model BEFORE calling `_probe_vision()`, not after. Gate this on `plan.model_tier != "none"` to avoid unnecessary teardown for text-only documents.

**Why:** `teardown_pdf()` keeps `qwen3.6:27b` warm between PDFs (intentional — avoids cold-load cost per document). But `vision_models_to_try()` checks `free_vram + free_ram >= model_size` at probe time. With orchestrator still loaded (~8 GB VRAM consumed), free VRAM ≈ 0. Total free = ~6 GB RAM. `qwen3-vl:8b` needs 6.1 GB → fails the check and is excluded. `qwen3-vl:4b` needs 3.5 GB → passes. Result: every doc after the first always loads 4b, regardless of actual VRAM availability after unload.

**Observed in benchmark (Session 22):** BERT and STEMI ran with 8b (fresh start, orchestrator not yet warmed). All subsequent docs (7→19) ran with 4b — causing agent timeouts on dense single-page docs and score regressions of 0.6–2.1 points on Invoice, NASA, slides.

**Fix:** Move `before_vision_phase()` call to Phase 2 before the probe. The probe now sees accurate free VRAM (orchestrator unloaded), picks 8b when it fits, and Phase 3 extraction proceeds with the correct model already loaded.

**Files:** `cloak/orchestration/parser_agent.py` — Phase 2 / Phase 3 boundary.

**See:** D14 (phase boundaries) · D32 (total-memory routing)

---

## D44 — Single-model mode: gemma4:26b as unified orchestrator + vision

**Decision (IMPLEMENTED Session 23):** Set `ORCHESTRATOR_MODEL = VISION_PRIMARY = VISION_FALLBACK = "gemma4:26b"`. Replaces the previous 3-model stack (qwen3.6:27b text + qwen2.5vl:7b vision + qwen3-vl:4b fallback).

**Why gemma4:26b:**
- Mixture of Experts — 26B total but only 3.8B active parameters → faster than dense 27B
- Natively multimodal — handles text and image inputs in the same model
- 256K context window — covers full-document FORMAT passes
- Configurable thinking mode via `think: True/False` in Ollama options
- At 17 GB, fits across 8 GB VRAM + 16 GB RAM via Ollama auto-split
- Eliminates all D43-class VRAM racing bugs by design — one model, no phase-boundary swaps

**Note:** qwen3.6:27b (Session 22 D44) was found to be TEXT-ONLY — image input only available in the 35b variant. That attempt was corrected by switching to gemma4:26b in Session 23.

**Thinking mode per phase (gemma4-specific — `think` key in options dict):**
- Extraction (exam_page, slide_page, full_page_extract, region_describe): `think=False` — transcription, not reasoning
- Quality (judge_quality, PATCH loop, deep_review): `think=True` — deliberate gap-filling

**Single-model mode mechanics:**
- `_single_model_mode()` returns True when `VISION_PRIMARY == ORCHESTRATOR_MODEL`
- `before_vision_phase()` and `before_orchestrator_phase()` are no-ops — no model to unload
- Model stays loaded continuously for the full pipeline (keep_alive=-1)
- No cold-load latency between phases — model already warm at every call site

**Changes (Session 23):**
- `config.py`: all three model constants set to `"gemma4:26b"`; `VISION_TIMEOUT` raised to 1800s; `AGENT_TIMEOUT` to 600s; `FORMAT_TIMEOUT` 900s; `STALL_SECONDS=90`; `EXAM_MAX_IMAGE_PX=1536`
- `.cloak_local.json`: updated with gemma4:26b values
- `vision_tools.py`: `_thinking_options()` injects `think=` only for gemma4 models
- `parser_agent.py`: `_orchestrator_options()` same pattern for orchestrator calls
- `system_check.py`: de-duplicated display (was showing 3 rows for same model)
- `deep_review.py`: Phase 9 uses `think=True` for gemma4; `_unload()` is no-op when DEEP_REVIEW_MODEL == ORCHESTRATOR_MODEL

**See:** D14 (phase boundaries) · D32 (total-memory routing) · D43 (probe ordering fix) · D45 (GLM-OCR)

---

## D45 — GLM-OCR as primary OCR engine

**Decision (IMPLEMENTED Session 23):** Replace surya as primary OCR engine with GLM-OCR (`glm-ocr` via Ollama, 2.2 GB). New fallback chain: `glm-ocr → surya → tesseract`.

**Why GLM-OCR:**
- #1 on OmniDocBench V1.5 (score 94.62) — beats all open-source OCR tools on document benchmark
- Document-specialised: handles text, tables, formulas, and complex layouts in one model call
- 2.2 GB — always fits in GPU VRAM alongside gemma4:26b (Ollama manages both)
- Returns structured markdown directly — no post-processing needed

**Two uses:**
1. **Scanned page OCR** (`ocr_tools.ocr_page()`): glm-ocr → surya → tesseract chain for full scanned pages
2. **Complex table extraction** (`ocr_tools.extract_table_glm()`): crops `TableItem` bbox from page image, runs GLM-OCR; wired in `_extract_docling_page()` — result replaces docling's `el.table_md` when longer

**Config:**
```python
GLM_OCR_MODEL   = "glm-ocr"
GLM_OCR_TIMEOUT = 60          # GLM-OCR is fast at 2.2 GB
OCR_PRIMARY     = "glm-ocr"
OCR_FALLBACK    = "surya"
OCR_LAST_RESORT = "tesseract"
```

**Installation:** `ollama pull glm-ocr`

**Expected quality impact:** Scanned documents, form-heavy pages, and complex tables get structured markdown instead of raw OCR text. Addresses IRS Pub 17 table ceiling (7.9 → expected 9.0+).

**See:** D22 (Tesseract decision) · D30 (surya) · D44 (gemma4:26b single-model)

---

## D46 — Doc-type focused quality strategy

**Decision (Session 24):** Stop building breadth across all doc types simultaneously. Focus on one doc type at a time: build, validate with domain sign-off, then move to the next.

**Order:** ICMR Standard Treatment Workflows → Exam Papers (JEE/GATE/ESE) → Research Papers → Legal/Financial → Scanned/Image-heavy

**Success gate per type:** 9.0+ on a held-out test corpus with an independent judge (not self-scored), plus domain expert review of at least 3 outputs.

**Why:** 23 sessions of broad-coverage development produced self-scored benchmarks with a circular judge. "8.4 on BERT" means gemma4 thinks it scored 8.4 — not externally validated. Adding features (slide_mode, exam_mode, pix2tex) on top of an unvalidated foundation compounded quality debt instead of paying it down.

Proof of quality on a narrow target is worth more than mediocre coverage across 19 types. One doc type with trustworthy scores and domain sign-off is a credible claim. Nineteen doc types with self-scores are not.

**What does NOT change:** The general pipeline (phases, docling, GLM-OCR, quality loop) serves all doc types. The doc-type-specific work is in three places only: the judge checklist, the success criteria, and the test corpus. Extraction improvements (post-processing, 4-level judge, structural validation) are general and built once in Sprint 0.

**See:** D47 (foundation first) · [[docs/GAPS.md]] §Prioritised roadmap

---

## D47 — Foundation stability before feature expansion

**Decision (Session 24):** Before any doc-type work, fix the three foundation problems that affect every output today:

1. **No post-processing phase** — G1 (HTML comment artifacts), G2 (exam header repetition), G3 (LaTeX encoding corruption) are in every `final.md`. New `cloak/quality/postprocess.py` module as Phase 8.5.
2. **Circular judge** — gemma4:26b scores its own extraction. Redesign `quality_judge.py` as a 4-level escalating system: Level 1 docling coverage check (deterministic), Level 2 word recall + hallucination rate (deterministic), Level 3 GLM-OCR cross-check (independent model, conditional), Level 4 grounded gemma4 judge (last resort, with docling checklist).
3. **No tests** — every regression found by expensive benchmark re-run. Five pure functions need pytest coverage minimum: `_detect_exam_paper`, `heuristic_judge`, `_content_loss_ok`, `_is_garbled`, `_clean_output_artifacts`.

**New phases added to pipeline:**
- Phase 3.5: Structural merge (continuation table detection + merge, TOC heading validation)
- Phase 4.5: Pre-judge inventory gap (deterministic element count before quality loop)
- Phase 7: Structural validation (final completeness check before write)
- Phase 8.5: Post-process (deterministic cleanup — artifacts, LaTeX, headers, whitespace)

**Why now:** Every doc type inherits these problems. Fixing them once in Sprint 0 benefits all subsequent doc-type sprints. Adding ICMR-specific features on top of broken output and a circular judge would make ICMR quality claims as untrustworthy as the current benchmark.

**Exit gate for Sprint 0:** `cloak parse stemi.pdf` and `cloak parse bert_devlin_2018.pdf`. Both `final.md` files must contain zero HTML artifacts, zero exam header repetition, zero LaTeX with non-ASCII inside `$...$`. Judge scores must cite source (L1/L2/L3/L4) in confidence report.

**See:** D46 (doc-type strategy) · [[docs/GAPS.md]] §Sprint 0

---

## D48 — judge_quality uses think=False (Session 25)

**Decision:** `vision_tools.judge_quality()` calls gemma4:26b with `think=False`, not `think=True`.

**Why:** Session 25 observed that `think=True` on a 7,553 char dense ICMR document caused gemma4:26b to spend its entire thinking chain internally, generating zero visible tokens for 1800s (the full VISION_TIMEOUT). The judge then returned the neutral fallback score (6.2) instead of a real quality assessment. Round 2 judge on the same document with a warm context took 239s — confirming the model CAN judge quickly when not forced into deep reasoning mode.

Quality judging is a **completeness check**, not a reasoning task. The model needs to compare extracted text against the page image and identify gaps — not reason deeply about clinical implications. `think=False` produces correct, fast judgments. `think=True` produces timeout.

**Updated thinking mode table:**

| Phase | think | Reason |
|---|---|---|
| `full_page_extract`, `region_describe` | False | Transcription |
| `slide_page`, `exam_page` | False | Transcription |
| `judge_quality` | **False** | Completeness check — think=True causes timeout on dense docs |
| PATCH loop | True | Gap-filling needs deliberate analysis |
| Phase 9 `deep_review` | True | Holistic audit needs reasoning |
| FORMAT | False | Pure transformation |

**File:** `cloak/vision/vision_tools.py` — `judge_quality()` call to `_call_timed()`

**See:** D44 (gemma4:26b single-model, think mode design)


## D49 — Two-model split: qwen3-vl:8b (VLM) + qwen3:14b (LLM) replacing gemma4:26b single-model (Session 26)

**Decision:** Replace gemma4:26b single-model mode (D44) with two purpose-built specialists:
- `qwen3-vl:8b` (6.1 GB) — dedicated VLM: figure crops, image-heavy pages, L4 quality judge
- `qwen3:14b` (9.0 GB) — dedicated LLM: FORMAT pass, PATCH loop, Phase 9 deep review
- `glm-ocr` (2.2 GB) — stays always-resident during parse, coexists with both (D45)

VLM and LLM are mutually exclusive in VRAM. Phase boundaries enforce the swap:
- `before_vision_phase()` — unloads LLM before VLM is needed
- `before_orchestrator_phase()` — unloads VLM before LLM is needed
Both use confirmed unload (D50).

**Why:** gemma4:26b (17 GB MoE) has only 3.8B active parameters per forward pass — comparable to a dense 3.8B model despite its 26B total weight. qwen3:14b (dense) fires all 14B parameters on every token — significantly better for FORMAT and PATCH tasks. qwen3-vl:8b (6.1 GB) fits fully in 8 GB VRAM with no CPU spill, making extraction and judging substantially faster than gemma4:26b forced CPU+GPU split at ~2 tok/s.

**Hardware fit on RTX 5050 (8 GB VRAM, 24 GB RAM):**

| Phase | Models active | VRAM | RAM |
|---|---|---|---|
| Phase 3 scanned | glm-ocr 2.2 GB | 2.2 GB | -- |
| Phase 3 figures | qwen3-vl:8b 6.1 GB + glm-ocr 2.2 GB | 8.0 GB | 0.3 GB spill |
| Phase 4 FORMAT | qwen3:14b 9.0 GB + glm-ocr 2.2 GB | 8.0 GB | 3.2 GB |
| Phase 5 L4 judge | qwen3-vl:8b 6.1 GB + glm-ocr 2.2 GB | 8.0 GB | 0.3 GB spill |
| Phase 6 PATCH | qwen3:14b 9.0 GB + glm-ocr 2.2 GB | 8.0 GB | 3.2 GB |
| Phase 9 review | qwen3:14b (reuse -- already loaded) | 8.0 GB | 1.0 GB |

**Key win -- Phase 9 zero-cost:** LLM is already loaded from Phase 6. Phase 9 deep review reuses it directly. `teardown_pdf()` is called AFTER Phase 9, not before.

**Trade-off:** Each VLM-LLM phase boundary swap adds ~15-30s. Mitigated by:
1. Text-only documents: L4 never fires, VLM never loaded in judge loop, zero swaps after Phase 3
2. JUDGE_SKIP_THRESHOLD: pages scoring >= 9.0 not re-judged, fewer rounds, fewer swaps
3. Phase 9 reuse: saves ~20-30s reload that old setup required

**Fallback:** `qwen3-vl:4b` (3.3 GB) is `VISION_FALLBACK` -- probed if `qwen3-vl:8b` fails to load.

**See:** D44 (superseded single-model mode) D50 (confirmed unload) [[docs/MODELS.md]]

---

## D50 — Confirmed unload at phase boundaries (Session 26)

**Decision:** `unload_and_wait(model, timeout=30)` polls `/api/ps` after firing `keep_alive=0` until Ollama confirms the model is gone, then waits an additional 0.5s for the CUDA allocator to return pages. All phase boundary calls (`before_vision_phase`, `before_orchestrator_phase`, `teardown_pdf`) use this instead of the old fire-and-forget `unload()`.

**Why:** The old `unload()` posted `keep_alive=0` and returned immediately. With two large models (6.1 GB VLM + 9.0 GB LLM), if the incoming model starts loading before the outgoing one has released VRAM, both compete for 8 GB simultaneously. Ollama has no atomic swap -- it will try to fit both into VRAM + RAM, forcing the incoming model entirely onto slow CPU RAM. The confirmed-wait ensures VRAM is actually free before the next model loads.

**Implementation:** 1s polling interval, 30s timeout. Falls through on timeout with a warning rather than blocking forever -- if unload hangs, the next model will be slower (RAM-only) but the parse continues. The 0.5s post-confirmation pause gives the CUDA allocator time to return pages to the GPU memory pool before the next allocation request arrives.

**Scope:** Only phase boundary transitions use `unload_and_wait`. Intra-phase calls (e.g., fire-and-forget unloads in `switch_to_fallback`) may still use the instant `unload()` where timing is not critical.

**See:** D49 (two-model split context) `model_router.unload_and_wait()`

---

## D51 — Poster mode: clinical flowchart VLM extraction (Session 26)

**Decision:** Single-page clinical flowcharts and poster-format PDFs are detected by a mismatch between pdfplumber text density and docling element count. When detected, all pages are routed to full-page VLM extraction using a specialized transcription prompt (`_POSTER_PROMPT`) instead of the docling text path.

**Detection signal (`_detect_poster()`):**
- Document is short (<= 5 pages), AND
- Any page has `text_length > 800` (substantial pdfplumber text), AND
- That page has fewer than 8 docling text-type elements (text, section_header, list_item)

This mismatch occurs when flowchart boxes and arrows are rendered as PDF vector art: pdfplumber reads the embedded text but in stream order (wrong spatial sequence), while docling's layout model cannot parse box-and-arrow diagrams into structured elements.

**Why not pdfplumber:** A single-page ICMR dengue STW has 4,277 pdfplumber chars across 6 docling elements. The parallel columns (Compensated Shock / Hypotensive Shock) get spatially interleaved, causing critical clinical errors: `PLT <10,000` extracted as `>10,000`, `DSS` as `SSD`, `albumin` replaced by `abdomen` from an adjacent column. The VLM reads the rendered page image and follows visual flow as a clinician would.

**Prompt design (`_POSTER_PROMPT`):** 10 explicit transcription rules covering: verbatim text extraction, ## headings for sections, indented branching structure, exact number/sign preservation (>, <, >=, <=), markdown table syntax, and inclusion of bottom-of-page disclaimers/dates. Deliberately avoids description — "transcribe content verbatim" not "describe the flowchart".

**Resolution:** `EXAM_MAX_IMAGE_PX = 1536` (same as exam mode) — higher than the default 1024 to read dense box text clearly.

**VLM judge:** Poster pages force `needs_vision=True` on all pages so the L4 VLM judge evaluates extracted content against the actual page image, not just the heuristic word-overlap check that previously scored the flawed dengue extraction at 9.3/10.

**Affected documents:** ICMR Standard Treatment Workflows (all 19 single-page flowchart posters), medical algorithm posters, clinical pathway documents.

**See:** D38 (slide_mode pattern this follows) D39 (exam_mode pattern)

---

## D52 — Ground-truth-first pipeline: GLM-OCR baseline + heuristic judge (Session 28)

**Decision:** Replace the VLM-as-judge approach with a ground-truth-first pipeline. GLM-OCR runs on every page in Phase 1 (not just scanned pages), producing an accurate text baseline. The quality judge compares extracted markdown against this baseline using text recall and element coverage — no model calls required for text-content pages. VLM judging is reserved for genuinely image-only content where no text baseline exists.

**Why the previous approach failed:**
- qwen3-vl:8b takes 700s+ per judge call (evaluation requires reasoning, extraction is transcription)
- format="json" caused 819s stall — model buffers entire response for grammar validation with vision input
- Patch loop never worked — structural gaps cannot be fixed by text patching; re-extraction is better
- The VLM judge was used for poster pages that have full pdfplumber/GLM-OCR text — wasteful

**New pipeline phases:**

Phase 1 — Deep profiling (CPU only, no Ollama VLM):
  - docling: ElementInventory, TableFormer-enabled table structure, picture captions, reading order
  - GLM-OCR (2.2 GB, stays loaded): full-page text extraction on ALL pages — ground_truth_text[page]
  - pdfplumber: raw text + tables (cross-check and supplement)
  - camelot (optional): lattice algorithm for grid-line tables (CHA2DS2-VASc type)
  - Output: GroundTruthMap = {structure, ground_truth_text, tables, reading_order} per page

Phase 2 — Classify (no model):
  - page types from GroundTruthMap
  - poster detection uses docling coverage ratio (GLM-OCR coverage vs pdfplumber text)

Phase 3 — Extract (qwen3-vl:8b, loaded then UNLOADED):
  - docling path: text/table pages
  - poster_mode: qwen3-vl:8b with _POSTER_PROMPT
  - image_heavy: qwen3-vl:8b with _EXTRACT_PROMPT
  - scanned: glm-ocr (already loaded)

Phase 4 — Heuristic judge (NO MODEL, instant):
  - Compare extracted markdown vs ground_truth_text (GLM-OCR):
    word_recall = (GLM-OCR words found in markdown) / (total GLM-OCR words)
  - Compare vs ElementInventory:
    element_coverage = (docling elements found in markdown) / (total docling elements)
  - Hallucination rate = (markdown words NOT in GLM-OCR) / (total markdown words)
  - gap_report: specific missing sections, wrong values, structural gaps
  - score: 0.6 * word_recall + 0.3 * element_coverage + 0.1 * (1 - hallucination_rate)

Phase 5 — Re-extract (conditional, qwen3-vl:8b reloaded):
  - Only if score < 8.0
  - Gap-informed prompt: "Previous extraction missed: [X, Y, Z]. Include these."
  - Re-runs extraction on problem pages only
  - UNLOAD after

Phase 6 — VLM judge (optional, qwen3-vl:4b):
  - Only for pages with genuine image-only content (no GLM-OCR text baseline)
  - qwen3-vl:4b (3.3 GB, faster than 8b) for evaluation
  - UNLOAD after

Phase 7 — Deep review (optional, --review flag):
  - qwen3:14b, grounded against GroundTruthMap
  - UNLOAD after

**DoclingPipelineOptions changes:**
  - do_table_structure=True + TableStructureOptions(do_cell_matching=True) — enables TableFormer
  - populate caption field from docling item for pictures and tables
  - Surya reading order for poster pages (detect multi-column layout before extraction)

**New library:**
  - camelot-py[cv] for PDF grid tables (lattice algorithm, no neural network)
  - All other improvements use existing installed libraries

**Model roles:**
  Ground-truth text:   glm-ocr (2.2 GB, always loaded during Phase 1)
  VLM extraction:      qwen3-vl:8b (6.1 GB, Phase 3 only)
  VLM judge (images):  qwen3-vl:4b (3.3 GB, Phase 6 only, rare)
  Deep review:         qwen3:14b (9 GB, Phase 7, optional)
  Text judge:          none — instant heuristic using GLM-OCR baseline

**See:** D45 (GLM-OCR design) D47 (4-level judge this replaces) D49 (two-model split) D51 (poster_mode)

---
