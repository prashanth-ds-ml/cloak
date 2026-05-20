---
type: session-log
updated: 2026-05-20 (Session 11)
---

# Progress — cloak

> Read this first every session. | [[CLAUDE.md]] · [[ARCHITECTURE.md]] · [[MODULES.md]] · [[MODELS.md]] · [[DECISIONS.md]]

---

## Current state — end of 2026-05-20 (Session 11)

**Total-memory routing (D32) and keep_alive=-1 (D11) locked in. All modules consistent.**

Session 11 focused on model routing correctness and memory lifecycle:

- **D32 (new)**: Total-memory routing — model viability = `free_vram + free_ram >= model_weight`. Ollama auto-splits any model across GPU + CPU RAM. Old VRAM-only check was incorrectly routing to fallback when primary would fit via auto-split. Fixed in `model_router.py`, `system_check.py`, `doc_profiler.py`, `parser_agent.py`.
- **D11 (updated)**: `MODEL_KEEP_ALIVE = -1` — models stay loaded within a phase; explicit phase-boundary unloads handle lifecycle. Old `keep_alive=0` caused up to 10 cold reloads per judge round (one per page). Phase boundaries (`before_vision_phase` / `before_orchestrator_phase`) now always fire unconditionally.
- **`get_page_elements` tool**: Added to parser agent's patch loop — agent can inspect docling structural element map for any page while patching.
- **`run_startup_cleanup()` in parse**: Added to `cloak parse` command so idle Ollama models are freed before every parse run.
- **Bug fixed**: `parser_agent.py` line 1083 referenced `model_router._VISION_PRIMARY_VRAM_GB` (removed in Session 10) → AttributeError crash. Fixed by using `model_router._MODEL_SIZE_GB.get(VISION_PRIMARY, 7.3)`.
- **`build_parse_plan()` param rename**: `gpu_available` → `primary_viable` to reflect total-memory semantics.
- **Suitability display**: startup screen now shows `ready (auto-split)` (cyan) for models spanning GPU+RAM; removed misleading 85% `marginal (GPU)` band.

**Next session:** end-to-end parse run — `cloak parse data/raw/cardiology/stemi.pdf`

---

## Current state — end of 2026-05-19 (Session 10)

**D28–D31 fully implemented. Docling + Surya integrated. All modules working.**

Session 10 implemented all four decisions from the Session 9 design:

- **D28**: `profiling/doc_profiler.py` built — `DocProfile` + `ParsePlan`. `build_doc_profile()` aggregates page type distribution + picture counts from docling for vision_dependency. `build_parse_plan()` produces adaptive max_rounds, judge_sample_rate, model_tier. `model_router.set_parse_plan()` stores plan; `vision_models_to_try()` now respects model_tier ("none"/"fallback"/"primary").
- **D29**: `run_docling_pass()` runs docling layout analysis (CPU, do_ocr=False). Produces `DoclingPageMap` per-page. `_extract_docling_page()` in parser_agent uses element map for structured extraction: title/section_header → heading hierarchy, table → export_to_markdown(), picture → vision region crop + describe, footnote → collected + appended. PageHeader/PageFooter discarded. `update_vision_from_docling()` refines `needs_vision` to only pages with actual picture elements.
- **D30**: `extraction/ocr_tools.py` updated — `_ocr_page_surya()` uses surya 0.17.1 with lazy-loaded `RecognitionPredictor` + `DetectionPredictor`. `ocr_page()` dispatches: surya first, tesseract fallback. `is_surya_available()` added. `is_available()` returns True if either engine is ready.
- **D31**: `quality/quality_judge.py` updated — `structure_score: float = 0.0` added to `PageScore`. `_compute_structure_score()` heuristic checks heading presence, table separator rows, page-header pollution. `judge()` combines: `final = 0.7 * content + 0.3 * structure`.

**Key bug fixed:** `run_docling_pass` was using string `"pdf"` as `format_options` key instead of `InputFormat.PDF` enum. Docling silently ignored the options dict, ran with `do_ocr=True`, and item iteration failed. Fix: use `InputFormat.PDF` enum key, split item extraction into `_add_item()` helper (isolates per-item errors), remove `del converter, result` from finally block (was triggering pypdfium2 destructor warnings).

**Packages installed:** `docling 2.94.0`, `surya-ocr 0.17.1`

**Verified:** `run_docling_pass(stemi.pdf)` → 44 elements, 14 section_headers, 5 pictures, 20 list_items. DocProfile + ParsePlan built correctly.

---

## Current state — end of 2026-05-18 (Session 9)

**Design session. No code changes. Four new decisions locked in (D28–D31).**

Session 9 was a design discussion focused on the root causes of speed and quality problems observed in the real 11-PDF + 828-page batch run. Key outcomes:

- **D28**: Two-level profiler — DocProfile + ParsePlan. Before loading any model, aggregate page profiles into a doc-level signal that drives model tier selection, round budget, and judge sampling rate adaptively. Fixes: 30s vision probe per PDF, fixed MAX_ROUNDS regardless of doc size.
- **D29**: Docling as structural extraction foundation. Docling's layout model (DocLayNet, 258 MB) runs first and produces a structured element map — headings with hierarchy levels, tables, figures, footnotes, reading order. Fixes: lost headings, wrong reading order, page header pollution, orphaned footnotes. Vision narrowed to: figure description + quality judge + patches only.
- **D30**: Surya replaces Tesseract as primary OCR for scanned pages (Tesseract kept as fallback). Better reading order detection, 90+ language support, GPU-accelerated on RTX 5050.
- **D31**: Markdown output standard defined — heading hierarchy, figure captions, footnote linking. Structural fidelity added as second judge scoring axis (30% weight alongside 70% content completeness).

**Next session:** install docling + surya, implement DocProfile + ParsePlan in `profiling/`, wire into `parser_agent.py`.

---

## Current state — end of 2026-05-16 (Session 8)

**9-phase pipeline. 11 modules. CLI working. Deep review integrated.**

All prior 8 phases are in production. Session 8 added Phase 9 (post-pipeline deep quality review), replaced the vision fallback model, fixed heading extraction, and fixed CLI startup behavior.

### What is working
- **Full 9-phase pipeline**: profiler → vision extraction (headings from layout) → FORMAT → judge+patch loop → confidence output → deep quality review
- **Page profiler**: heuristic classification into `text_rich | table_heavy | image_heavy | scanned | mixed`
- **Vision for all page types (D23 updated)**: ALL pages use `full_page_extract()` when vision is available — headings come from visual layout, not pdfplumber flat text
- **Region image persistence**: ECG/figure/diagram crops saved to `{stem}_images/`, embedded as `![label](path)` in markdown
- **OCR tools**: Tesseract wrapper with graceful fallback — raises `OCRError` if binary missing
- **FORMAT step (D20)**: qwen3:8b restructures raw content once before judge loop; uses `/no_think` prefix to suppress thinking chain
- **Extract-once (D19)**: Phases 5–6 judge+patch only, no re-extraction
- **Phase 9 deep review (D27)**: gemma4:latest (9.6 GB, CPU+GPU split) compares pdfplumber text vs final markdown, writes `{stem}_review.md`
- **Confidence report (D24)**: `{stem}_confidence.md` with per-page High/Medium/Low
- **typer CLI**: `cloak parse`, `cloak status`, `cloak list` all functional
- **Startup screen**: shown only on bare `cloak` and `cloak status` (D17 updated)
- **VRAM-aware suitability check**: all three pipeline models show `ready (GPU)` on RTX 5050

### Hardware bottleneck status (Session 8)
| Model | VRAM | Status |
|---|---|---|
| `qwen2.5vl:7b` | 7.3 GB | Ready (GPU) — vision primary |
| `qwen3:8b` | 5.2 GB | Ready (GPU) — orchestrator |
| `qwen3-vl:4b` | 3.5 GB | Ready (GPU) — vision fallback (replaced llama3.2-vision) |
| `gemma4:latest` | 9.6 GB | Phase 9 only — CPU+GPU split after teardown |

### Run commands
```powershell
.\.venv\Scripts\Activate.ps1
cloak parse data/raw/cardiology/stemi.pdf          # single file (with Phase 9 deep review)
cloak parse data/raw/cardiology/stemi.pdf --no-review  # skip Phase 9
cloak parse data/raw/cardiology/              # whole directory
cloak status                                   # hardware + model status
cloak list                                     # show parsed docs
```

**Tesseract install (for scanned pages):**
```powershell
winget install UB-Mannheim.TesseractOCR
```

---

## Module status

| # | Module | Path | Status |
|---|---|---|---|
| 1 | PDF extractor | `extraction/pdf_tools.py` | **done + tested** |
| 2 | Vision model calls | `vision/vision_tools.py` | **done** — `full_page_extract`, `region_describe`, `judge_quality`; `layout_hints` removed (D23) |
| 3 | Quality judge | `quality/quality_judge.py` | **done** — structural fidelity scoring (D31) |
| 4 | Model router | `orchestration/model_router.py` | **done** — total-memory routing (D32); ParsePlan tier (D28) |
| 5 | Context compressor | `orchestration/context_manager.py` | **done** |
| 6 | Orchestrator | `orchestration/parser_agent.py` | **done** — ParsePlan wiring (D28/D29); get_page_elements tool |
| 7 | Page profiler | `profiling/page_profiler.py` | **done** — update_vision_from_docling (D29) |
| 8 | OCR tools | `extraction/ocr_tools.py` | **done** — Surya primary, Tesseract fallback (D30) |
| 9 | Hardware check | `cli/system_check.py` | **done** — total-memory suitability; auto-split display; startup cleanup (D32/D18) |
| 10 | CLI | `cli/main.py` | **done** — parse/status/list; run_startup_cleanup in parse (D17) |
| 11 | Deep review | `quality/deep_review.py` | **done** — Phase 9; gemma4:latest; CPU+GPU split (D27) |
| 12 | Doc profiler | `profiling/doc_profiler.py` | **done** — DocProfile + ParsePlan; primary_viable param (D28/D32) |
| — | Legacy reference | `ingestion/pdf_extractor.py` | read-only |
| — | Legacy reference | `ingestion/pdf_classifier.py` | read-only |
| — | Legacy reference | `ingestion/vision.py` | read-only |
| — | Legacy reference | `ingestion/markdown_builder.py` | read-only |

---

## Sessions

### 2026-05-20 — Session 11: Total-memory routing, keep_alive=-1, get_page_elements

**Done**

- **D32**: Total-memory routing — `free_vram + free_ram` replaces VRAM-only check everywhere. `model_router.vision_models_to_try()` uses combined pool. `system_check.check_model_suitability()` now shows `ready (auto-split)` for models spanning GPU+RAM (cyan, not yellow). Removed 85% marginal band.
- **D11 updated**: `MODEL_KEEP_ALIVE = -1` — models stay warm within a phase. Phase boundaries always fire unconditionally: `before_vision_phase()` always unloads orchestrator; `before_orchestrator_phase()` always unloads vision model. Sticky vision model preserved across phases.
- **`get_page_elements` tool**: Added to `_TOOLS` list and `_execute_tool()` handler in parser_agent. Agent can inspect the docling structural element map for any page while in the patch loop.
- **`run_startup_cleanup()` in parse**: Added to `cloak parse` command — frees idle Ollama models before every parse run.
- **Bug fix**: `parser_agent.py` crash at line 1083 — `model_router._VISION_PRIMARY_VRAM_GB` AttributeError (removed in Session 10). Fixed: `model_router._MODEL_SIZE_GB.get(VISION_PRIMARY, 7.3)`.
- **`build_parse_plan()` param rename**: `gpu_available: bool` → `primary_viable: bool` in `doc_profiler.py` + call site in `parser_agent.py`.
- **Doc sweep**: DECISIONS.md (D11, D14, D18 updated; D32 added), PROGRESS.md, MODELS.md, CLAUDE.md, memory all updated to match Session 11 state.

---

### 2026-05-18 — Session 9: Design — DocProfile, ParsePlan, docling, surya

**Design only — no code changed this session.**

- Defined the missing planning layer: DocProfile aggregates page profiles before any model loads; ParsePlan drives adaptive round budget, model tier, judge sampling rate (D28)
- Defined docling as structural extraction foundation: element map with heading hierarchy, reading order, table/figure/footnote classification; vision role narrowed to figure description + judging + patches (D29)
- Defined surya as primary OCR upgrade over Tesseract for scanned pages; Tesseract kept as fallback (D30)
- Defined markdown output standard and structural fidelity as second judge scoring axis — 0.7 content + 0.3 structure (D31)
- Identified root causes of data loss: wrong reading order, lost heading hierarchy, page header pollution, orphaned footnotes — all structural problems that no judge→patch rounds can recover
- Identified root cause of speed/RAM issues: vision probe runs for every PDF regardless of doc content; fixed round budget regardless of doc size
- Added Module 12 (doc_profiler) to module list

**New decisions → see [[docs/DECISIONS.md]] §D28 §D29 §D30 §D31**

**Next session implementation order:**
1. `pip install docling surya` + verify GPU acceleration
2. Build `profiling/doc_profiler.py` — DocProfile + ParsePlan (D28)
3. Update `profiling/page_profiler.py` — integrate docling element map (D29)
4. Update `extraction/ocr_tools.py` — Surya primary, Tesseract fallback (D30)
5. Update `orchestration/model_router.py` — consume ParsePlan for model tier (D28)
6. Update `orchestration/parser_agent.py` — wire ParsePlan + docling extraction phase
7. Update `quality/quality_judge.py` — structural fidelity scoring (D31)

---

### 2026-05-16 — Session 8: Phase 9, fallback swap, heading fix, CLI cleanup

**Done**

- **Phase 9 Deep Review (D27)**: built `quality/deep_review.py` — loads `gemma4:latest` after `teardown_pdf()`, compares raw pdfplumber text vs final markdown, writes `{stem}_review.md` with structured report (Missing Content, Headings, Tables, Duplicates, Formatting, Quality Score, Priority Fixes)
- **VISION_FALLBACK swap (D15)**: replaced `llama3.2-vision:11b` (7.8 GB, CPU spill, timeout) with `qwen3-vl:4b` (3.3 GB, GPU-only, same VL family). All three pipeline models now show `ready (GPU)` at startup
- **Heading fix (D23 updated)**: `text_rich` pages now use `full_page_extract()` directly — vision reads visual layout and assigns headings in one pass. Removed `layout_hints()` function and `_build_layout_context()` from codebase entirely
- **Region image persistence**: `_images_dir()` and `_save_region()` save ECG/figure/diagram crops to `{stem}_images/`; images embedded in markdown as `![label](path)` for RAG; saved count shown in final Panel
- **CLI startup fix (D17)**: startup screen (`show_startup_screen`) now only called on bare `cloak` and `cloak status`. Not shown on `cloak parse` or `cloak list` — avoids hardware table cluttering parse output
- **CLI `--no-review` flag**: `cloak parse --no-review` skips Phase 9. Default is to run deep review
- **FORMAT prompt upgrade**: added `/no_think` prefix (qwen3-specific — suppresses thinking chain), updated to 6 explicit rules; `FORMAT_NUM_CTX = 8192` in config to prevent thinking tokens truncating output
- **VRAM-aware suitability check**: `check_model_suitability()` now takes `free_vram_gb` param; priority: GPU → CPU+GPU split → CPU → marginal → unavailable. All three models show `ready (GPU)` on RTX 5050
- **Startup memory cleanup**: `run_startup_cleanup()` unloads idle Ollama models at startup, reports freed RAM/VRAM; shows top memory consumers when headroom is tight
- **`sys.stdout.reconfigure()` fix**: removed `io.TextIOWrapper` stdout replacement (caused closed-file bugs on Windows); replaced with in-place `reconfigure(encoding="utf-8")`
- **`config.py` additions**: `DEEP_REVIEW_MODEL`, `DEEP_REVIEW_TIMEOUT`, `FORMAT_NUM_CTX`; removed `LAYOUT_HINTS_TIMEOUT`

**Known issues / follow-up**
- `gemma4:latest` not-installed case gives raw exception — should show friendly message
- `/no_think` prefix in FORMAT prompt is qwen3-specific — should be conditional on model name
- `qwen3-vl:4b` not yet tested as `VISION_PRIMARY` — worth benchmarking on 1–2 PDFs
- Content-loss guard (35%) may trigger on legitimate FORMAT cleanup now that input includes vision-extracted content + image refs; consider raising `CONTENT_LOSS_LIMIT` to 0.50
- Region image paths in markdown are relative — only work correctly when markdown is opened from `data/markdown/{specialty}/` directory
- Docs not yet tested against actual PDF parse run (end-to-end integration test pending)

---

### 2026-05-16 — Session 7: Full 8-phase implementation

**Done**
- Step 1: Folder restructure (D26) — moved modules to profiling/, extraction/, vision/, quality/, orchestration/; stub re-exports in ingestion/ keep old imports working
- Step 2: config.py fixes — VISION_TIMEOUT 180→400s, MODEL_KEEP_ALIVE 600→0, added MIN_FREE_RAM_GB / SCANNED_TEXT_THRESHOLD / IMAGE_AREA_THRESHOLD / OCR_LANG
- Step 3: Generalised all prompts in vision_tools.py — removed "medical document parser" language (D16)
- Step 4: Built `profiling/page_profiler.py` — heuristic 5-type classification + RouteMap (D21)
- Step 5: Built `extraction/ocr_tools.py` — Tesseract OCR, graceful OCRError fallback, Windows path auto-detect (D22)
- Step 6: Refactored `orchestration/parser_agent.py` — full 8-phase orchestrator with `_extract_by_route`, `_run_format_session`, extract-once loop (D19/D20/D23)
- Step 7: Built `cli/system_check.py` — hardware probe (psutil + nvidia-smi), model suitability, startup screen (D17/D18)
- Step 8: Rewrote `cli/main.py` — typer CLI with `parse`, `status`, `list` commands (D17)
- Added psutil and typer to pyproject.toml dependencies

**Known issues / follow-up**
- `MODEL_KEEP_ALIVE=0` causes cold reloads per call within the judge loop — see [[MODELS.md]] §Ollama config and [[DECISIONS.md]] §D11. Consider raising to a large value (e.g. 3600) if parse is slow.
- Tesseract binary not yet installed on dev machine — `ocr_tools.ocr_page()` will raise `OCRError` and fall back to raw text until installed (`winget install UB-Mannheim.TesseractOCR`)
- `cloak parse` not yet tested end-to-end with vision model loaded (RAM constraints) — text-only path tested

---

### 2026-05-16 — Session 6: Production plan + doc update

**Done**
- Reviewed master design doc against current codebase — identified gaps and alignment
- Agreed new production plan: 8-phase profiler-routed pipeline for any PDF type
- Key additions vs old plan: page_profiler (heuristic, D21), Tesseract OCR (D22), selective vision extraction (D23), per-page confidence output (D24)
- Key exclusions: no Camelot (D25), no JSON output files yet (D24), no PaddleOCR (D22)
- Agreed folder restructure (D26): ingestion/ splits into profiling/, extraction/, vision/, quality/, orchestration/
- Added D21–D26 to DECISIONS.md
- Updated ARCHITECTURE.md: new 8-phase pipeline, new data types, updated module dependency graph
- Updated MODULES.md: page_profiler spec, ocr_tools spec, parser_agent 8-phase spec, updated module paths
- Updated PROGRESS.md and CLAUDE.md to reflect new plan

**New decisions → see [[docs/DECISIONS.md]] §D21 §D22 §D23 §D24 §D25 §D26**

---

### 2026-05-15 — Session 5: Scope expansion + CLI design + doc update

**Done**
- Expanded scope: cloak is a general-purpose PDF parser (not ICMR-specific) — D16
- Designed CLI: `cloak`, `cloak parse`, `cloak status`, `cloak list` — D17
- Designed startup screen: hardware table + model suitability table — D18
- Defined extract-once design: round 1 extracts, rounds 2+ judge+patch — D19
- Defined FORMAT step: qwen3:8b restructures raw content in round 1 before patching — D20
- Rewrote `CLAUDE.md` to reflect all of the above
- Added D16–D20 to `DECISIONS.md`
- Updated `ARCHITECTURE.md`: CLI flow, correct pipeline with extract-once + FORMAT, system_check in dep graph
- Updated `MODULES.md`: §7 system_check spec, §8 CLI spec, §6 parser_agent correct loop
- Updated `MODELS.md`: generalised prompts, model suitability table, VISION_TIMEOUT/MODEL_KEEP_ALIVE corrections
- Updated `PROGRESS.md`: this session entry

**Observed from live run (session 4 output)**
- `data/markdown/cardiology/stemi.md` — no headings, broken tables, flowchart lost → FORMAT step needed
- `data/markdown/cardiology/bradyarrhythmia.md` — ECG placeholders only, broken table cells merged → FORMAT step needed
- Root cause: pdfplumber table dumps multi-column content into single cells; text-only extraction has no structure
- Judge scores content completeness (8.0/10) but not formatting — FORMAT step fills this gap

**New decisions → see [[DECISIONS.md]] §D16 §D17 §D18 §D19 §D20**

---

### 2026-05-15 — Session 4: Phase-based sequential model routing

**Done**
- Redesigned `model_router.py`: added `before_vision_phase()` and `before_orchestrator_phase()` as explicit phase boundary calls (D14)
- Refactored `parser_agent._extract_all_pages()`: removed per-page `switch_to_fallback()` — sticky model → raw text only. No more mid-round VRAM surprises (D15)
- Refactored `parser_agent.parse()`: vision phase and orchestrator phase now bracketed by explicit boundary calls. Removed reactive `switch_to_fallback()` + `restore_orchestrator()` from main loop
- Rewrote `ARCHITECTURE.md` with 5 mermaid diagrams: full pipeline flowchart, phase sequence diagram, VRAM budget table, model routing decision tree, extract cascade flowchart
- Added D14 (phase-based routing) and D15 (llama3.2-vision excluded from full-page OCR) to `DECISIONS.md`
- Updated `MODULES.md` §4 and §6 to reflect new call sequence

**Key invariants enforced**
- `qwen3:8b` is never unloaded mid-extract — it's only managed at explicit phase boundaries
- `llama3.2-vision:11b` is never loaded for full-page OCR (times out) — only available for region crops via patch loop tools
- `qwen2.5vl:7b` + `qwen3:8b` coexist freely — no boundary actions for that pairing

**New decisions → see [[DECISIONS.md]] §D14 §D15**

---

### 2026-05-15 — Session 3: Model routing + memory fixes

**Done**
- Fixed `_probe_vision`: now tries both `VISION_PRIMARY` then `VISION_FALLBACK`, calls `model_router.mark_success()` on winner
- Fixed `_extract_all_pages`: proper 3-step cascade — sticky model → VISION_FALLBACK (with VRAM swap) → raw text. No longer skips fallback model.
- Added `keep_alive=MODEL_KEEP_ALIVE` (600s) to ALL Ollama chat calls — models stay warm between rounds
- Added `MODEL_KEEP_ALIVE = 600` and `MAX_IMAGE_PX = 1024` to `config.py` — all Ollama knobs now centralised
- Confirmed `model_router` fully wired into parse loop: `reset()`, `get_vision_model()`, `mark_success()`, `switch_to_fallback()`, `restore_orchestrator()`, `teardown_pdf()` all called correctly

**Observed in live run**
- Probe: `qwen2.5vl:7b` → fail (RAM) → `llama3.2-vision:11b` → loaded (slow), marked sticky ✓
- Extract cascade: `llama3.2-vision:11b` timed out (180s) → raw text fallback ✓
- Judge: vision unavailable → graceful 5.0 score, action=patch ✓
- Patch: `qwen3:8b` couldn't load (11B model occupies all memory) → agent timeout ✓
- 3 rounds all at 5.0 → text-only output written to disk ✓
- `ollama ps` confirmed: `CONTEXT 4096`, `UNTIL 10 minutes` → `MODEL_NUM_CTX` + `MODEL_KEEP_ALIVE` working ✓

**New decisions made → see [[DECISIONS.md]] §D10 §D11 §D12**

---

### 2026-05-15 — Session 2: All 6 modules built

**Done**
- Built all 6 new modules from scratch
- Windows cp1252 crash fixed (force UTF-8 stdout at startup)
- Image resize: long edge capped at `MAX_IMAGE_PX=1024px` before sending to VLM
- `MODEL_NUM_CTX=4096` on all Ollama calls
- `pdf_tools.py` tested: 2 ECG regions correctly extracted from `bradyarrhythmia.pdf`
- End-to-end text-only run confirmed

---

### 2026-05-15 — Session 1: Project reset + docs

**Done**
- Removed RAG, API, legacy ingestion, empty CLI stubs
- Rewrote `config.py`, `pyproject.toml` — local-only, parser-only
- Created `.venv`, installed all deps
- Created doc system: [[ARCHITECTURE.md]], [[MODULES.md]], [[MODELS.md]], [[DECISIONS.md]]

**Models confirmed locally available**
- `qwen3:8b` — orchestrator
- `qwen2.5vl:7b` — vision primary (needs 8.6 GB free RAM)
- `llama3.2-vision:11b` — vision fallback (loads but slow on CPU/GPU split)
- `gemma4`, `mistral:7b` — present but not used

---

## Rules that must never be broken

| Rule | Detail |
|---|---|
| Best round wins | Return highest-scoring round, not last — [[DECISIONS.md]] §D2 |
| Quality threshold | Stop at 8.0/10 — [[DECISIONS.md]] §D3 |
| Content-loss guard | Revert if >35% chars removed — [[DECISIONS.md]] §D5 |
| Context cap | Compress history above 8K tokens — [[DECISIONS.md]] §D6 |
| Spatial sort | Column order (bbox), not PDF draw order — [[DECISIONS.md]] §D4 |
| Extract once | Vision extraction runs only in round 1 — [[DECISIONS.md]] §D19 |
| FORMAT before PATCH | Round 1 formats first, then patches gaps — [[DECISIONS.md]] §D20 |
| General-purpose | No domain-specific assumptions in prompts — [[DECISIONS.md]] §D16 |
| Vision for headings | text_rich pages use full_page_extract for layout — [[DECISIONS.md]] §D23 |
| One model at a time | teardown_pdf() before Phase 9 loads gemma4 — [[DECISIONS.md]] §D27 |
| Legacy files | Read-only — do not modify `pdf_extractor`, `pdf_classifier`, `vision`, `markdown_builder` |
