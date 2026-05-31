---
type: model-reference
updated: 2026-05-31 (Session 26)
---

# Model Reference — cloak

> Related: [[ARCHITECTURE.md]] · [[MODULES.md]] · [[DECISIONS.md]]

All models run locally via Ollama. No cloud API calls.

---

## Model roster (Session 26 — D49)

| Model | Role | Size | Timeout |
|---|---|---|---|
| `qwen3-vl:8b` | Vision LLM — figure crops, image pages, L4 judge | 6.1 GB (GPU) | VISION_TIMEOUT=1800s |
| `qwen3-vl:4b` | Vision fallback — probed if 8b fails to load | 3.3 GB (GPU) | VISION_TIMEOUT=1800s |
| `qwen3:14b` | Text LLM — FORMAT, PATCH, deep review | 9.0 GB (GPU+RAM) | AGENT_TIMEOUT=600s / FORMAT_TIMEOUT=900s |
| `glm-ocr` | Scanned OCR + L3 cross-check — always-resident | 2.2 GB (GPU or RAM) | GLM_OCR_TIMEOUT=60s |
| `pix2tex` (LatexOCR) | Math OCR — FormulaItem bbox crops → LaTeX | ~100 MB (CPU) | MATH_OCR_TIMEOUT=30s |

---

## VRAM budget by phase (RTX 5050, 8 GB VRAM + 24 GB RAM)

VLM and LLM are mutually exclusive in VRAM — loading both simultaneously would be 15.1 GB, forcing LLM entirely to slow RAM. Phase boundaries use `unload_and_wait()` (D50).

| Phase | Models active | VRAM | RAM |
|---|---|---|---|
| Phase 3 scanned pages | glm-ocr 2.2 GB | 2.2 GB | — |
| Phase 3 figure crops / image pages | qwen3-vl:8b 6.1 GB + glm-ocr 2.2 GB | 8.0 GB | 0.3 GB spill |
| Phase 4 FORMAT | qwen3:14b 9.0 GB + glm-ocr 2.2 GB | 8.0 GB | 3.2 GB |
| Phase 5 L4 judge (image pages) | qwen3-vl:8b 6.1 GB + glm-ocr 2.2 GB | 8.0 GB | 0.3 GB spill |
| Phase 6 PATCH | qwen3:14b 9.0 GB + glm-ocr 2.2 GB | 8.0 GB | 3.2 GB |
| Phase 9 deep review | qwen3:14b (reuse — already loaded from Phase 6) | 8.0 GB | 1.0 GB |
| Peak at any point | ~11.2 GB across GPU+RAM | — | — |

---

## Model suitability (RTX 5050, confirmed Session 26)

| Model | Weight | Placement | Display |
|---|---|---|---|
| `qwen3-vl:8b` | 6.1 GB | Full GPU (8 GB VRAM) | **ready (GPU)** — green |
| `qwen3-vl:4b` | 3.3 GB | Full GPU | **ready (GPU)** — green |
| `qwen3:14b` | 9.0 GB | ~8 GB VRAM + ~1 GB RAM | **ready (auto-split)** — cyan |
| `glm-ocr` | 2.2 GB | GPU when alone; RAM when alongside VLM | **ready (GPU/RAM)** — green/cyan |

---

## Task routing

| Task | Model | Fallback |
|---|---|---|
| Figure description / image pages | `qwen3-vl:8b` (or fallback 4b) | caption placeholder |
| Poster/flowchart full-page (D51) | `qwen3-vl:8b` | pdfplumber text |
| Quality judge L4 (image/scanned pages) | `qwen3-vl:8b` (or fallback 4b) | score=5.0 neutral |
| FORMAT cleanup (Phase 4) | `qwen3:14b` | raw content unchanged |
| PATCH gap-filling (Phase 6) | `qwen3:14b` | skip round |
| Deep review (Phase 9) | `qwen3:14b` (reused, no reload) | skipped |
| Context summarisation | `qwen3:14b` | `"[Summary unavailable]"` |
| Scanned page OCR | `glm-ocr` → `surya` → `tesseract` | raw PyMuPDF text |
| L3 cross-check judge | `glm-ocr` | skip L3 |
| Math formula OCR | `pix2tex` (inline Python, not Ollama) | docling text as inline code |

---

## Phase boundary loading rules (D49, D50)

1. `before_vision_phase()` — if LLM (`qwen3:14b`) is loaded: `unload_and_wait()` → VLM loads lazily on first call.
2. `before_orchestrator_phase()` — if VLM is loaded: `unload_and_wait()` → LLM loads lazily on first call.
3. `unload_and_wait(model, timeout=30)` — POSTs `keep_alive=0`, then polls `/api/ps` until model disappears, then waits 0.5s for CUDA allocator. Falls through on timeout with a warning.
4. `teardown_pdf()` — called AFTER Phase 9. Unloads VLM → LLM → glm-ocr in order, each confirmed.
5. `keep_alive=-1` on all calls — model stays loaded until explicit phase-boundary unload.

---

## Ollama config applied to every call

| Parameter | Value | Config key | Effect |
|---|---|---|---|
| `num_ctx` (LLM standard) | 16384 | `MODEL_NUM_CTX` | qwen3:14b patch/review context |
| `num_ctx` (FORMAT) | 32768 | `FORMAT_NUM_CTX` | Full-doc FORMAT pass |
| `num_ctx` (vision) | 8192 | `VISION_NUM_CTX` | VLM calls — KV cache sized for images |
| `num_ctx` (deep review) | 8192 | `DEEP_REVIEW_NUM_CTX` | template + 10K raw + 10K md |
| `keep_alive` | -1 | `MODEL_KEEP_ALIVE` | Model stays loaded until explicit unload |
| `temperature` | 0.1 | hardcoded | Deterministic extraction |
| `think` | True/False | per-call | qwen3 and gemma4 families |

---

## think mode by phase

| Phase / call | think | Reason |
|---|---|---|
| `full_page_extract`, `region_describe` | False | Transcription task |
| `poster_page`, `slide_page`, `exam_page` | False | Transcription task |
| `judge_quality` (L4) | False | Completeness check — think=True causes timeout (D48) |
| FORMAT pass | False | Pure transformation |
| PATCH loop | True | Gap-filling needs deliberate reasoning |
| Phase 9 deep review | True | Holistic audit benefits from reasoning chain |

---

## Known hardware limits (RTX 5050, 8 GB VRAM, 24 GB RAM)

- `qwen3-vl:8b` — 6.1 GB, always full GPU. 1.9 GB headroom when alone; 0.3 GB RAM spill when coexisting with glm-ocr.
- `qwen3-vl:4b` — 3.3 GB, always full GPU. Can coexist with LLM simultaneously (3.3+9=12.3 GB — possible in future if phase isolation relaxed).
- `qwen3:14b` — 9.0 GB: ~8 GB VRAM + ~1 GB RAM. Fully unloads VLM first (D50).
- `glm-ocr` — 2.2 GB: GPU when alone, RAM when VLM is loaded. 2.2 GB on RAM is fast enough for OCR.
- **Never load VLM + LLM simultaneously** — 15.1 GB forces LLM entirely to CPU RAM (~2 tok/s).
- **Always parse sequentially** — parallel parses compete for VRAM, causing 10x slower extraction.

---

## Legacy models (kept for .cloak_local.json overrides)

| Model | Sessions used | Replaced by | Reason |
|---|---|---|---|
| `gemma4:26b` | Sessions 11–26 | qwen3-vl:8b + qwen3:14b | MoE with only 3.8B active params; 17 GB forces CPU split at ~2 tok/s |
| `qwen3.6:27b` | Sessions 3–11 | qwen3:14b | Superseded; still in setup.py catalog for high-RAM machines |
| `qwen2.5vl:7b` | Sessions 8–11 | qwen3-vl:8b | Superseded; still in setup.py catalog |
| `qwen3-vl:4b` | Sessions 8–11 | qwen3-vl:8b (primary) | Now VISION_FALLBACK |
