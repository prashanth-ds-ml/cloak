# cloak — PDF → Markdown

General-purpose. Local-only. No data leaves the machine.
Any PDF type: research papers, legal documents, technical manuals, medical guidelines, textbooks.

## Start here every session

1. Read **[[docs/PROGRESS.md]]** — current state, what's working, what's blocked, next steps
2. Check **[[docs/MODULES.md]]** for the spec + known issues of whatever you're working on
3. Check **[[docs/MODELS.md]]** for model VRAM state, routing behaviour, confirmed limits
4. Check **[[docs/DECISIONS.md]]** before changing any design parameter — especially D14–D32

## Doc map

| File | Purpose |
|---|---|
| [[docs/PROGRESS.md]] | Session log, module status, hardware state, next steps |
| [[docs/ARCHITECTURE.md]] | System design, data flow, correct pipeline, CLI design |
| [[docs/MODULES.md]] | Per-module specs: functions, data types, known issues |
| [[docs/MODELS.md]] | Model roster, VRAM observations, suitability table, prompts |
| [[docs/DECISIONS.md]] | Why things are the way they are — D1–D31 |

## Current status (end of 2026-05-20, Session 11)

**Total-memory routing implemented. keep_alive=-1 phase model lifecycle locked in.**

Session 11: total-memory (VRAM + RAM) replaces VRAM-only model routing everywhere. Ollama auto-splits any model across GPU + CPU RAM — a model is viable when `total_free >= model_weight`. `MODEL_KEEP_ALIVE=-1` keeps models loaded within a phase; explicit unloads at phase boundaries free memory for the next phase. `run_startup_cleanup()` added to `cloak parse`. `get_page_elements` tool added to patch loop.

Next: end-to-end parse run to validate docling extraction quality.

## Stack

| Layer | Tool |
|---|---|
| Orchestrator | `qwen3:8b` via Ollama |
| Vision primary | `qwen2.5vl:7b` via Ollama — figure description + judge + patches (D29) |
| Vision fallback | `qwen3-vl:4b` — 3.3 GB, GPU-only, same VL family (D15) |
| Deep review | `gemma4:latest` — Phase 9 only, CPU+GPU split after teardown (D27) |
| Layout + structure | `docling` — element map, heading hierarchy, reading order (D29) |
| PDF → data | pymupdf, pdfplumber, pillow |
| OCR primary | `surya` — reading-order-aware, GPU-accelerated (D30) |
| OCR fallback | tesseract + pytesseract (D22) |
| System check | psutil, nvidia-smi |
| UI | rich, typer |

## CLI commands

```powershell
cloak                              # startup screen — hardware + model status
cloak parse <pdf>                  # parse a single PDF (includes Phase 9 deep review)
cloak parse <pdf> --no-review      # parse without Phase 9
cloak parse <dir>                  # parse all PDFs in a directory
cloak status                       # hardware + model status only
cloak list                         # list parsed documents in data/markdown/
```

## Correct pipeline (locked — see [[docs/ARCHITECTURE.md]] for full diagram)

```
Phase 0  intake         →  load PDF, page count, create output dirs
Phase 1  doc intelligence → docling layout pass → element map per page
                             → DocProfile → ParsePlan (D28, D29)
Phase 2  model staging  →  load only what ParsePlan.model_tier requires (D28)
Phase 3  extraction     →  element-aware tool cascade (D29):
                             SectionHeaderItem → ## headings at correct level
                             TextItem          → pdfplumber chars
                             TableItem         → pdfplumber (simple) / docling (complex)
                             FigureItem        → vision region_describe + caption
                             FootnoteItem      → collected, appended at section end
                             Scanned page      → surya OCR → tesseract fallback (D30)
                             PageHeader/Footer → DISCARDED
Phase 4  format         →  qwen3:8b light cleanup (structure already from docling)
Phase 5  judge          →  vision scores sampled pages (ParsePlan.judge_sample_rate)
                             content score + structural fidelity score (D31)
Phase 6  patch          →  qwen3:8b fills remaining gaps
         repeat 5–6 up to ParsePlan.max_rounds → best round wins (D2)
Phase 8  output         →  final.md + confidence_report.md + flagged.md + images
         teardown_pdf() — all pipeline models unloaded
Phase 9  deep review    →  gemma4:latest compares pdfplumber text vs final.md → review.md (D27)
```

## Hard rules

- **Profile before model load** — DocProfile computed at zero cost before any Ollama call ([[docs/DECISIONS.md]] §D28)
- **ParsePlan drives everything** — round budget, model tier, sample rate from plan, not fixed constants ([[docs/DECISIONS.md]] §D28)
- **Docling owns structure** — headings, reading order, footnotes, element types from docling layout model ([[docs/DECISIONS.md]] §D29)
- **Vision for figures and judging only** — not for text layout or heading extraction ([[docs/DECISIONS.md]] §D29)
- **Total-memory routing** — VRAM + RAM combined determines viability; Ollama auto-splits any model across GPU + CPU RAM ([[docs/DECISIONS.md]] §D32)
- **Best round wins** — return highest-scoring round, not last ([[docs/DECISIONS.md]] §D2)
- **Quality threshold 8.0** — stop loop early when reached ([[docs/DECISIONS.md]] §D3)
- **Content-loss guard** — revert patch if >35% chars removed ([[docs/DECISIONS.md]] §D5)
- **Extract once** — extraction runs once (Phase 3); rounds 2+ judge+patch only ([[docs/DECISIONS.md]] §D19)
- **FORMAT before PATCH** — Phase 4 cleans up first, then Phase 6 fills gaps ([[docs/DECISIONS.md]] §D20)
- **teardown before Phase 9** — all pipeline models must be unloaded before gemma4 loads ([[docs/DECISIONS.md]] §D27)
- **Context cap** — compress history above 8K tokens ([[docs/DECISIONS.md]] §D6)
- **Legacy files are read-only** — `pdf_extractor.py`, `pdf_classifier.py`, `vision.py`, `markdown_builder.py`

## Config knobs (all in config.py)

| Constant | Value | What it controls |
|---|---|---|
| `QUALITY_THRESHOLD` | 8.0 | Stop loop early |
| `MAX_ROUNDS` | 4 | Ceiling — ParsePlan.max_rounds overrides per doc |
| `VISION_TIMEOUT` | 400s | Per vision call |
| `AGENT_TIMEOUT` | 150s | Per orchestrator call |
| `MODEL_NUM_CTX` | 4096 | Ollama context window (standard calls) |
| `FORMAT_NUM_CTX` | 8192 | Ollama context window for Phase 4 FORMAT |
| `MODEL_KEEP_ALIVE` | -1 | Model stays loaded until explicit phase-boundary unload |
| `MAX_IMAGE_PX` | 1024 | Long-edge cap before sending image to VLM |
| `CONTENT_LOSS_LIMIT` | 0.35 | Revert threshold |
| `PAGE_DPI` | 150 | Page render resolution |
| `MIN_FREE_RAM_GB` | 9.0 | RAM gate threshold for vision model |
| `OCR_PRIMARY` | `"surya"` | Primary OCR engine for scanned pages |
| `OCR_LANG` | `"eng"` | Tesseract fallback language code |
| `SCANNED_TEXT_THRESHOLD` | 100 | Chars below which a page is considered scanned |
| `IMAGE_AREA_THRESHOLD` | 0.4 | Image area ratio above which a page is image_heavy |
| `JUDGE_SKIP_THRESHOLD` | 9.0 | Pages at or above this score are skipped in subsequent rounds |
| `DEEP_REVIEW_MODEL` | `"gemma4:latest"` | Phase 9 review model |
| `DEEP_REVIEW_TIMEOUT` | 600s | Phase 9 timeout (CPU+GPU split is slower) |

## Activate env

```powershell
.\.venv\Scripts\Activate.ps1
cloak parse data/raw/cardiology/stemi.pdf
```

## Hardware

- GPU: RTX 5050 8 GB VRAM | RAM: 24 GB
- See [[docs/MODELS.md]] §VRAM observations and §Model suitability table
