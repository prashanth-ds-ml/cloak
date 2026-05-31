# cloak — PDF → Markdown

General-purpose. Local-only. No data leaves the machine.

---

## Next session — start here

**Sprint 1 · ICMR Standard Treatment Workflows · Session 27**

**First task:** run `pytest tests/ -q` — confirm still 60/60.

**Core problem to solve (identified Session 27):**

`qwen3-vl:8b` is the wrong model for the quality judge. It takes **700s+ per judge call** (vs 132s for extraction) because evaluation requires reasoning while extraction is just transcription. With 4 rounds max, the quality loop takes 47+ min per doc — unusable.

**Fix in this order:**

1. **Redesign the quality judge for poster_mode pages** — for pages extracted by poster_mode, skip the VLM judge (L4) entirely. Use L1 + L2 only:
   - L1: docling coverage (deterministic, instant)
   - L2: pdfplumber word recall (no model, instant)
   - These are reliable for poster docs because pdfplumber HAS the text (just in wrong spatial order)
   - Only use L4 VLM judge for genuinely scanned/image-only pages with no pdfplumber text
   - File to change: `cloak/quality/quality_judge.py` and `cloak/orchestration/parser_agent.py`

2. **Fix poster_mode detection for AF** — `_detect_poster()` threshold is `< 8 docling text elements` but AF has 63 elements with only 33.9% text coverage. Change signal to: `docling_text_coverage < 0.50`. AF: 33.9% fires ✓. Stroke: 84.7% doesn't fire ✓.
   - File: `cloak/orchestration/parser_agent.py` — `_detect_poster()`

3. **Investigate patch loop** — every doc stops with "Patch produced no changes". qwen3:14b tool-calling may not be working. Run a single patch manually with debug output to see what the model returns.
   - File: `cloak/orchestration/parser_agent.py` — `_run_patch_loop()`

4. **Once judge is fast: run 5 ICMR STWs sequentially** and collect quality scores.

**Key work from Session 26–27 (committed in Session 26, Session 27 not yet committed):**

Session 26 (committed):
- Model stack: gemma4:26b → qwen3-vl:8b (VLM) + qwen3:14b (LLM) — D49, D50
- `poster_mode`: `_detect_poster()`, `_extract_poster_page()`, `poster_page()`, `_POSTER_PROMPT` — D51
- Dengue: 75%→97% completeness, 1→21 headings, critical clinical errors fixed

Session 27 (NOT YET COMMITTED — need to commit):
- `format="json"` tried and reverted — causes 819s stall, model buffers entire response
- Improved judge prompts (`_JUDGE_PROMPT`, `_JUDGE_GROUNDED_PROMPT`) — concrete JSON example
- Robust JSON extraction (embedded JSON search) — attempt 2 in `judge_quality()`
- Extended `_strip_hallucination` — catches VLM rewrite/correction patterns
- Strengthened `_POSTER_PROMPT` — "COPY ONLY" rules, explicit anti-rewrite instructions
- `json_format` param added to `_call_timed()` (not used for judge, available for future)

**Tests baseline:** 60/60 passing (Session 27). Run `pytest tests/ -q` before writing any code.

**Root cause summary (for context):**
- qwen3-vl:8b: fast at transcription (132s), slow at evaluation (700s+) — wrong model for judge
- Judge returns 0 streaming tokens for hundreds of seconds — batch generation, not streaming
- Quality loop only works when judge is fast — VLM judge makes it unusable for poster docs
- Heuristic judge (L1/L2) is the right approach for poster pages — instant, reliable, no model needed

---

## Sprint tracker

| # | Sprint | Doc type | Status | Sessions | Exit gate |
|---|---|---|---|---|---|
| 0 | Foundation | all | ✅ done | 24 | 55/55 tests · postprocess.py · 4-level judge |
| 1 | ICMR STW | ICMR Standard Treatment Workflows | 🔄 active | 25–27 | 9.0+ on 8/10 · clinician sign-off · stemi 9.6 ✓ |
| 2 | Exam Papers | JEE / GATE / ESE | ⏳ planned | 28–30 | 8.5+ on 4/5 · subject expert review |
| 3 | Research Papers | academic / arXiv | ⏳ planned | 31–32 | 9.0+ on academic papers |
| 4 | Legal / Financial | SCOTUS / Berkshire | ⏳ planned | 33 | 9.5+ consistently |
| 5 | Scanned / Image-heavy | Dumfries / NASA | ⏳ planned | 34+ | honest ceiling validated |
| 6 | Credibility | all 19 + external tools | ⏳ planned | 35+ | Marker / MinerU comparison published |

---

## Session protocol

**Start (2 min):**
1. Read `## Next session — start here` above — tells you exactly what to do
2. Run `pytest tests/ -q` — must be green before writing any code

**During:**
- Make a design decision? → Add to **DECISIONS.md first**, then write the code. Never after.
- No other doc updates until end of session.

**End (5 min — update these 4 things only):**
1. **CLAUDE.md** `## Next session — start here` → rewrite for the NEXT session's first task
2. **CLAUDE.md** `## Sprint tracker` → mark any completed sprints
3. **PROGRESS.md** → add 3–5 bullet session entry at the top (what was built, test results, decisions)
4. **GAPS.md** → mark completed gaps ✅ with session number

Only update ARCHITECTURE.md, MODELS.md, MODULES.md when those specific things actually changed.

---

## When to read each doc

| Doc | Read when |
|---|---|
| CLAUDE.md (this file) | Every session start — just the top 3 sections above |
| [[docs/PROGRESS.md]] | You need historical context on a bug or decision |
| [[docs/DECISIONS.md]] | Before changing any design parameter or architecture |
| [[docs/GAPS.md]] | Planning next sprint or checking what's still open |
| [[docs/ARCHITECTURE.md]] | Changing pipeline phases or data flow |
| [[docs/MODULES.md]] | Changing a specific module's API |
| [[docs/MODELS.md]] | Changing model config, timeouts, or routing |

---

## Stack

| Layer | Tool |
|---|---|
| Vision LLM | `qwen3-vl:8b` via Ollama — 6.1 GB, full GPU, figures + image pages + L4 judge (D49) |
| Vision fallback | `qwen3-vl:4b` via Ollama — 3.3 GB, full GPU (D49) |
| Text LLM | `qwen3:14b` via Ollama — 9.0 GB, ~8 GB GPU + 1 GB RAM, FORMAT + PATCH + deep review (D49) |
| OCR primary | `glm-ocr` via Ollama — 2.2 GB, #1 OmniDocBench V1.5, always-resident (D45) |
| OCR fallback | `surya` — reading-order-aware, GPU-accelerated (D30) |
| OCR last resort | tesseract + pytesseract (D22) |
| Layout + structure | `docling` — element map, heading hierarchy, reading order (D29, D36) |
| Math OCR | `pix2tex` — FormulaItem bbox crops → LaTeX `$$...$$` (D35) |
| PDF → data | pymupdf, pdfplumber, pillow |
| System check | psutil, nvidia-smi |
| UI | rich, typer |

## CLI commands

```powershell
cloak                              # startup screen — hardware + model status
cloak parse <pdf>                  # parse a single PDF (includes Phase 9 deep review)
cloak parse <pdf> --no-review      # parse without Phase 9
cloak parse <pdf> --dry-run        # list what would be parsed without running
cloak parse <dir>                  # parse all PDFs in a directory
cloak status                       # hardware + model status only
cloak list                         # list all tracked documents with scores + status (uses registry)
cloak clean                        # remove all parsed output from data/markdown/ (confirmation prompt)
cloak clean --yes                  # clean without confirmation
```

## Correct pipeline (see [[docs/ARCHITECTURE.md]] for full diagram)

```
Phase 0    intake              →  load PDF, page count, create output dirs, init staging file
Phase 1    doc intelligence    →  docling layout pass → DoclingPageMap (D29, D36)
                                   → ElementInventory per page (expected tables/headings/figures/formulas)
                                   → DocProfile (formula_count — D35) → ParsePlan (D28)
                                   → TOC detection → expected_heading_list
                                   → continuation table scan → merge_pairs
Phase 2    model staging       →  unload orchestrator FIRST (D43), then probe vision (D28)
Phase 3    extraction          →  explicit strategy sequence per page, incremental staging writes:
                                   exam_mode (D39)   → exam_page() → GLM-OCR fallback → pdfplumber
                                   slide_mode (D38)  → slide_page() → full_page_extract() → pdfplumber
                                   docling path      → SectionHeader/Text/Table/Figure/Formula/Footnote
                                   scanned           → glm-ocr → surya → tesseract (D45, D30)
                                   page marker <!-- page N --> appended after each page
Phase 3.5  structural merge    →  merge continuation table pairs detected in Phase 1 (D47)
                                   TOC heading validation → missing sections → targeted_gaps list
Phase 4    format              →  gemma4 light cleanup, only when _content_needs_format() (D20, D37)
Phase 4.5  pre-judge inventory →  ElementInventory vs extracted markdown → deterministic gap list (D47)
                                   feeds directly into Phase 6 patch — loop knows gaps before round 1
           ┌─ quality loop (rounds 1..ParsePlan.max_rounds) ──────────────────────┐
Phase 5    │  judge (4-level, D47):                                               │
           │    L1 docling coverage   — expected vs found per element type        │
           │    L2 word recall + hallucination rate — pdfplumber independent      │
           │    L3 GLM-OCR cross-check — only when L1/L2 flag a problem           │
           │    L4 gemma4 constrained — image/scanned only, with docling checklist│
           │  structural signature tracked: {headings, tables, paragraphs}        │
Phase 6    │  patch — adaptive targeting: critical gaps first, grouped by page    │
           │  structural regression check → revert if headings/tables drop        │
           │  content-loss guard (D5) · best round wins (D2)                      │
           └──────────────────────────────────────────────────────────────────────┘
Phase 7    structural validation →  final completeness check vs ElementInventory (D47)
                                    one targeted pass for still-missing sections
Phase 8    output              →  write from staging + best_round.markdown
Phase 8.5  post-process        →  strip_html_comments, clean_latex_encoding,       (D47)
                                   strip_exam_headers, deduplicate_lines,
                                   add_page_markers, validate_table_columns
                                   → write clean final.md + confidence_report.md + flagged.md
           teardown_pdf()      →  all pipeline models unloaded
Phase 9    deep review         →  gemma4 verifies against ElementInventory + judge findings (D27)
                                   grounded prompt: "docling found X, verify Y is present"
```

## Hard rules

- **Profile before model load** — DocProfile computed at zero cost before any Ollama call ([[docs/DECISIONS.md]] §D28)
- **ParsePlan drives everything** — round budget, model tier, sample rate, math OCR from plan ([[docs/DECISIONS.md]] §D28)
- **Docling owns structure** — headings, reading order, footnotes, element types from docling layout model ([[docs/DECISIONS.md]] §D29)
- **Docling ElementInventory is the judge's checklist** — never ask gemma4 to evaluate itself open-ended; always ground against what docling found ([[docs/DECISIONS.md]] §D47)
- **Judge is 4-level, escalating** — L1 docling → L2 pdfplumber → L3 GLM-OCR → L4 gemma4 constrained. Model called last and only when needed ([[docs/DECISIONS.md]] §D47)
- **Post-processing before write** — Phase 8.5 runs on every output; final.md must be artifact-free ([[docs/DECISIONS.md]] §D47)
- **Total-memory routing** — VRAM + RAM combined determines viability; Ollama auto-splits any model ([[docs/DECISIONS.md]] §D32)
- **Best round wins** — return highest-scoring round, not last ([[docs/DECISIONS.md]] §D2)
- **Quality threshold 8.0** — stop loop early when reached ([[docs/DECISIONS.md]] §D3)
- **Content-loss guard** — revert patch if >35% chars removed ([[docs/DECISIONS.md]] §D5)
- **Structural regression guard** — revert if heading or table count drops >20% between rounds ([[docs/DECISIONS.md]] §D47)
- **Extract once** — extraction runs once (Phase 3); rounds 2+ judge+patch only ([[docs/DECISIONS.md]] §D19)
- **FORMAT before PATCH** — Phase 4 cleans up first, then Phase 6 fills gaps ([[docs/DECISIONS.md]] §D20)
- **teardown before Phase 9** — all pipeline models must be unloaded before gemma4 loads ([[docs/DECISIONS.md]] §D27)
- **Context cap** — compress history above 8K tokens ([[docs/DECISIONS.md]] §D6)
- **Doc-type focused** — one doc type at a time; success gate before next type ([[docs/DECISIONS.md]] §D46)
- **Legacy files are read-only** — `pdf_extractor.py`, `pdf_classifier.py`, `vision.py`, `markdown_builder.py`

## Config knobs (all in config.py)

| Constant | Value | What it controls |
|---|---|---|
| `QUALITY_THRESHOLD` | 8.0 | Stop loop early |
| `MAX_ROUNDS` | 4 | Ceiling — ParsePlan.max_rounds overrides per doc |
| `VISION_TIMEOUT` | 400s | Per vision call |
| `AGENT_TIMEOUT` | 400s | Per orchestrator call (qwen3.6:27b at 2.5 tok/s) |
| `FORMAT_TIMEOUT` | 1800s | Phase 4 FORMAT pass |
| `MODEL_NUM_CTX` | 16384 | Ollama context window (qwen3.6:27b standard) |
| `FORMAT_NUM_CTX` | 32768 | Ollama context window for Phase 4 FORMAT |
| `VISION_NUM_CTX` | 4096 | Vision models — small KV cache to fit 8 GB VRAM |
| `MODEL_KEEP_ALIVE` | -1 | Model stays loaded until explicit phase-boundary unload |
| `MAX_IMAGE_PX` | 1024 | Long-edge cap before sending image to VLM |
| `JUDGE_MAX_IMAGE_PX` | 512 | Judge images — smaller cap to reduce visual tokens |
| `CONTENT_LOSS_LIMIT` | 0.35 | Revert threshold |
| `PAGE_DPI` | 150 | Page render resolution |
| `MIN_FREE_RAM_GB` | 9.0 | RAM gate threshold for vision model |
| `OCR_PRIMARY` | `"surya"` | Primary OCR engine for scanned pages |
| `OCR_LANG` | `"eng"` | Tesseract fallback language code |
| `SCANNED_TEXT_THRESHOLD` | 100 | Chars below which a page is considered scanned |
| `IMAGE_AREA_THRESHOLD` | 0.4 | Image area ratio above which a page is image_heavy |
| `JUDGE_SKIP_THRESHOLD` | 9.0 | Pages at or above this score are skipped in subsequent rounds |
| `DEEP_REVIEW_MODEL` | `"gemma4:latest"` | Phase 9 review model |
| `DEEP_REVIEW_TIMEOUT` | 1200s | Phase 9 timeout (CPU+GPU split is slower) |
| `MATH_OCR_ENGINE` | `"pix2tex"` | Math OCR engine (D35) |
| `MATH_OCR_TIMEOUT` | 30s | Per formula crop |
| `MATH_FORMULA_THRESHOLD` | 3 | Min FormulaItems to activate math OCR |

## Activate env

```powershell
.\.venv\Scripts\Activate.ps1
cloak parse data/raw/cardiology/stemi.pdf
```

## Hardware

- GPU: RTX 5050 8 GB VRAM | RAM: 24 GB
- See [[docs/MODELS.md]] §VRAM observations and §Model suitability table
