# cloak — PDF → Markdown

General-purpose. Local-only. No data leaves the machine.
Any PDF type: research papers, legal documents, technical manuals, medical guidelines, textbooks.

## Start here every session

1. Read **[[docs/PROGRESS.md]]** — current state, what's working, what's blocked, next steps
2. Check **[[docs/MODULES.md]]** for the spec + known issues of whatever you're working on
3. Check **[[docs/MODELS.md]]** for model VRAM state, routing behaviour, confirmed limits
4. Check **[[docs/DECISIONS.md]]** before changing any design parameter — especially D14–D23, D27

## Doc map

| File | Purpose |
|---|---|
| [[docs/PROGRESS.md]] | Session log, module status, hardware state, next steps |
| [[docs/ARCHITECTURE.md]] | System design, data flow, correct pipeline, CLI design |
| [[docs/MODULES.md]] | Per-module specs: functions, data types, known issues |
| [[docs/MODELS.md]] | Model roster, VRAM observations, suitability table, prompts |
| [[docs/DECISIONS.md]] | Why things are the way they are — D1–D27 |

## Current status (end of 2026-05-16, Session 8)

**9-phase pipeline. 11 modules. CLI working. Deep review integrated.**

All modules done. Phase 9 deep review added (D27). VISION_FALLBACK swapped to qwen3-vl:4b (D15). text_rich pages now use vision for heading extraction (D23). `cloak parse --no-review` to skip Phase 9.
Next: end-to-end test on diverse PDFs from `data/samples/`, install Tesseract for OCR pages, install gemma4 for deep review.

## Stack

| Layer | Tool |
|---|---|
| Orchestrator | `qwen3:8b` via Ollama |
| Vision primary | `qwen2.5vl:7b` via Ollama (needs ≥ 9 GB free RAM) |
| Vision fallback | `qwen3-vl:4b` — 3.3 GB, GPU-only, same VL family (D15) |
| Deep review | `gemma4:latest` — Phase 9 only, CPU+GPU split after teardown (D27) |
| PDF → data | pymupdf, pdfplumber, pillow |
| OCR | tesseract + pytesseract |
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
Phase 0  intake      →  load PDF, page count, create images_dir
Phase 1  profiler    →  classify pages: text_rich | table_heavy | image_heavy | scanned | mixed
Phase 2  routing     →  build RouteMap (deterministic, no model)
Phase 3  extraction  →  ALL page types use vision when available (headings from visual layout — D23)
                         table_heavy → pdfplumber; scanned → Tesseract
Phase 4  format      →  qwen3:8b restructures raw content (/no_think, FORMAT_NUM_CTX=8192)
Phase 5  judge       →  vision model scores each page vs source image (every round)
Phase 6  patch       →  qwen3:8b fills gaps identified by judge
         repeat 5–6 up to MAX_ROUNDS → best round wins (D2)
Phase 8  output      →  final.md + confidence_report.md + images logged
         teardown_pdf() — all pipeline models unloaded
Phase 9  deep review →  gemma4:latest compares pdfplumber text vs final.md → review.md (D27)
```

## Hard rules

- **Profiler first** — classify all pages before any extraction; routing is deterministic from profiles ([[docs/DECISIONS.md]] §D21)
- **Vision for headings** — text_rich pages use full_page_extract for heading structure ([[docs/DECISIONS.md]] §D23)
- **Best round wins** — return highest-scoring round, not last ([[docs/DECISIONS.md]] §D2)
- **Quality threshold 8.0** — stop loop early when reached ([[docs/DECISIONS.md]] §D3)
- **Content-loss guard** — revert patch if >35% chars removed ([[docs/DECISIONS.md]] §D5)
- **Extract once** — extraction runs once (Phase 3); rounds 2+ judge+patch only ([[docs/DECISIONS.md]] §D19)
- **FORMAT before PATCH** — Phase 4 restructures first, then Phase 6 fills gaps ([[docs/DECISIONS.md]] §D20)
- **teardown before Phase 9** — all pipeline models must be unloaded before gemma4 loads ([[docs/DECISIONS.md]] §D27)
- **Context cap** — compress history above 8K tokens ([[docs/DECISIONS.md]] §D6)
- **Spatial sort** — column order (bbox), not PDF draw order ([[docs/DECISIONS.md]] §D4)
- **Legacy files are read-only** — `pdf_extractor.py`, `pdf_classifier.py`, `vision.py`, `markdown_builder.py`

## Config knobs (all in config.py)

| Constant | Value | What it controls |
|---|---|---|
| `QUALITY_THRESHOLD` | 8.0 | Stop loop early |
| `MAX_ROUNDS` | 4 | Max judge→patch iterations |
| `VISION_TIMEOUT` | 400s | Per vision call |
| `AGENT_TIMEOUT` | 150s | Per orchestrator call |
| `MODEL_NUM_CTX` | 4096 | Ollama context window (standard calls) |
| `FORMAT_NUM_CTX` | 8192 | Ollama context window for Phase 4 FORMAT |
| `MODEL_KEEP_ALIVE` | 0s | Explicit phase-based unloads handle lifecycle |
| `MAX_IMAGE_PX` | 1024 | Long-edge cap before sending image to VLM |
| `CONTENT_LOSS_LIMIT` | 0.35 | Revert threshold |
| `PAGE_DPI` | 150 | Page render resolution |
| `MIN_FREE_RAM_GB` | 9.0 | RAM gate threshold for vision model |
| `OCR_LANG` | `"eng"` | Tesseract language code |
| `SCANNED_TEXT_THRESHOLD` | 100 | Chars below which a page is considered scanned |
| `IMAGE_AREA_THRESHOLD` | 0.4 | Image area ratio above which a page is image_heavy |
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
