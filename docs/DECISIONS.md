---
type: decision-log
updated: 2026-05-16 (Session 8)
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

**Updated (Session 7):** `MODEL_KEEP_ALIVE = 0` — explicit phase-based management via `model_router` handles lifecycle. `before_vision_phase()` and `before_orchestrator_phase()` unload models at phase boundaries. `teardown_pdf()` cleans up at end of PDF.

**Rationale for change:** With phase-based unloads, keep_alive warmth is no longer needed to prevent mid-parse ejections. Setting 0 allows Ollama to reclaim memory immediately after each call rather than holding it for 10 minutes.

**Known trade-off:** `keep_alive=0` causes the model to unload after **every** `ollama.chat()` call, including within the Phase 5 judge loop (multiple pages). For a 10-page document this means up to 10 cold reloads per judge round. If parse speed is poor, consider raising to `3600` (1 hour) which keeps the model warm within a phase while still allowing explicit unloads via `model_router.unload()` to work normally between phases.

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

**Session 8 update:** With `qwen3-vl:4b` as `VISION_FALLBACK` (3.3 GB), both vision models coexist freely with `qwen3:8b`. `before_vision_phase()` and `before_orchestrator_phase()` are now no-ops for both fallback scenarios — they exist to enforce the rule structurally, not because any model pair actually conflicts.

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

**Suitability priority (VRAM-aware):**
1. GPU — model fits fully in VRAM → `ready (GPU)`
2. CPU+GPU — VRAM + RAM together cover model → `ready (CPU+GPU)`
3. CPU — no GPU but RAM sufficient → `ready (CPU)`
4. Marginal (≥ 85% of needed) or unavailable

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

## D13 — MAX_IMAGE_PX=1024 long-edge cap before sending to VLM

**Decision:** All images (full pages, region crops) are resized so their long edge ≤ 1024px before encoding as PNG and sending to the vision model.

**Why:** PDFs render at 1754×3404px at 150 DPI. Sending that to a VLM uses excessive image tokens (more memory + slower inference). 1024px long edge preserves text readability while halving the token count.

**Trade-off:** Very small text in footnotes may become harder to read. Acceptable for documents where the main content is in large-font body text.

**Adjust in:** `config.MAX_IMAGE_PX`
