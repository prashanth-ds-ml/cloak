# cloak — PDF → Markdown

General-purpose. Local-only. No data leaves the machine.
Any PDF type: research papers, legal documents, technical manuals, medical guidelines, textbooks.

## Start here every session

1. Read **[[docs/PROGRESS.md]]** — current state, what's working, what's blocked, next steps
2. Check **[[docs/MODULES.md]]** for the spec + known issues of whatever you're working on
3. Check **[[docs/MODELS.md]]** for model VRAM state, routing behaviour, confirmed limits
4. Check **[[docs/DECISIONS.md]]** before changing any design parameter — especially D7, D14–D20

## Doc map

| File | Purpose |
|---|---|
| [[docs/PROGRESS.md]] | Session log, module status, hardware state, next steps |
| [[docs/ARCHITECTURE.md]] | System design, data flow, correct pipeline, CLI design |
| [[docs/MODULES.md]] | Per-module specs: functions, data types, known issues |
| [[docs/MODELS.md]] | Model roster, VRAM observations, suitability table, prompts |
| [[docs/DECISIONS.md]] | Why things are the way they are — D1–D26 |

## Current status (end of 2026-05-16, Session 7)

**8-phase pipeline fully implemented and CLI working.**

All 10 modules done. The profiler-routed pipeline (D21–D26) is in production. `cloak parse`, `cloak status`, `cloak list` all functional.
Next: end-to-end test on diverse PDFs from `data/samples/`, install Tesseract for OCR pages.

## Stack

| Layer | Tool |
|---|---|
| Orchestrator | `qwen3:8b` via Ollama |
| Vision primary | `qwen2.5vl:7b` via Ollama (needs ≥ 9 GB free RAM) |
| Vision fallback | `llama3.2-vision:11b` — **not used in pipeline** (too slow on this hardware) |
| PDF → data | pymupdf, pdfplumber, pillow |
| OCR | tesseract + pytesseract |
| System check | psutil |
| UI | rich, typer |

## CLI commands

```powershell
cloak                    # startup screen — hardware + model status
cloak parse <pdf>        # parse a single PDF
cloak parse <dir>        # parse all PDFs in a directory
cloak status             # hardware + model status only
cloak list               # list parsed documents in data/markdown/
```

## Correct pipeline (locked — see [[docs/ARCHITECTURE.md]] for full diagram)

```
Phase 0  intake      →  load PDF, hash, page count
Phase 1  profiler    →  classify pages: text_rich | table_heavy | image_heavy | scanned | mixed
Phase 2  routing     →  build RouteMap (deterministic, no model)
Phase 3  extraction  →  per page by route — vision only for image_heavy/mixed
Phase 4  format      →  qwen3:8b restructures raw content into markdown (once)
Phase 5  judge       →  qwen2.5vl:7b scores each page vs source image (every round)
Phase 6  patch       →  qwen3:8b fills gaps identified by judge
         repeat 5–6 up to MAX_ROUNDS → best round wins (D2)
Phase 8  output      →  final.md + confidence_report.md
```

One model active at any point. Explicit unload after every session. Peak VRAM ≤ 6 GB.

## Hard rules

- **Profiler first** — classify all pages before any extraction; routing is deterministic from profiles ([[docs/DECISIONS.md]] §D21)
- **Selective vision** — vision extraction only for image_heavy/mixed pages; not for text_rich/table_heavy/scanned ([[docs/DECISIONS.md]] §D23)
- **Best round wins** — return highest-scoring round, not last ([[docs/DECISIONS.md]] §D2)
- **Quality threshold 8.0** — stop loop early when reached ([[docs/DECISIONS.md]] §D3)
- **Content-loss guard** — revert patch if >35% chars removed ([[docs/DECISIONS.md]] §D5)
- **One model at a time** — explicit unload after every model session ([[docs/DECISIONS.md]] §D19)
- **Extract once** — extraction runs once (Phase 3); rounds 2+ judge+patch only ([[docs/DECISIONS.md]] §D19)
- **FORMAT before PATCH** — Phase 4 restructures first, then Phase 6 fills gaps ([[docs/DECISIONS.md]] §D20)
- **Context cap** — compress history above 8K tokens ([[docs/DECISIONS.md]] §D6)
- **Spatial sort** — column order (bbox), not PDF draw order ([[docs/DECISIONS.md]] §D4)
- **Legacy files are read-only** — `pdf_extractor.py`, `pdf_classifier.py`, `vision.py`, `markdown_builder.py`

## Config knobs (all in config.py)

| Constant | Value | What it controls |
|---|---|---|
| `QUALITY_THRESHOLD` | 8.0 | Stop loop early |
| `MAX_ROUNDS` | 4 | Max judge→patch iterations |
| `VISION_TIMEOUT` | 400s | Per vision call (increased from 180s — gives slow GPU time to complete) |
| `AGENT_TIMEOUT` | 150s | Per orchestrator call |
| `MODEL_NUM_CTX` | 4096 | Ollama context window |
| `MODEL_KEEP_ALIVE` | 0s | Keep-alive within session only — explicit unload handles teardown |
| `MAX_IMAGE_PX` | 1024 | Long-edge cap before sending image to VLM |
| `CONTENT_LOSS_LIMIT` | 0.35 | Revert threshold |
| `PAGE_DPI` | 150 | Page render resolution |
| `MIN_FREE_RAM_GB` | 9.0 | RAM gate threshold for vision model |
| `OCR_LANG` | `"eng"` | Tesseract language code |
| `SCANNED_TEXT_THRESHOLD` | 100 | Chars below which a page is considered scanned |
| `IMAGE_AREA_THRESHOLD` | 0.4 | Image area ratio above which a page is image_heavy |

## Activate env

```powershell
.\.venv\Scripts\Activate.ps1
cloak parse data/raw/cardiology/stemi.pdf
```

## Hardware

- GPU: RTX 5050 8 GB VRAM | RAM: 24 GB
- See [[docs/MODELS.md]] §VRAM observations and §Model suitability table
