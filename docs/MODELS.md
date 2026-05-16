---
type: model-reference
updated: 2026-05-15
---

# Model Reference — cloak

> Related: [[ARCHITECTURE.md]] · [[MODULES.md]] · [[DECISIONS.md]]

All models run locally via Ollama. No cloud API calls.

---

## Model roster

| Model | Role | VRAM | Timeout |
|---|---|---|---|
| `qwen3:8b` | Orchestrator — format, patch, tool-calling, context summarise | ~5 GB | 150s |
| `qwen2.5vl:7b` | Vision primary — OCR, layout, quality judge | ~5 GB | 400s |
| `llama3.2-vision:11b` | Vision fallback — region crops only (excluded from full-page OCR, D15) | ~11 GB (GPU+RAM) | 400s |
| `gemma4`, `mistral:7b` | Available locally but **not used** in parser | — | — |

---

## Model suitability table (D18)

Checked at startup via `system_check.check_model_suitability()`. Confirmed on RTX 5050 / 24 GB RAM.

| Model | GPU VRAM | System RAM | Min free RAM needed | Coexists with |
|---|---|---|---|---|
| `qwen2.5vl:7b` | ~5 GB GPU | ~3.6 GB RAM | **9.0 GB** | `qwen3:8b` (safe) |
| `qwen3:8b` | ~5 GB GPU | ~0.5 GB RAM | **5.5 GB** | `qwen2.5vl:7b` (safe) |
| `llama3.2-vision:11b` | ~4.6 GB GPU | ~6.4 GB RAM | **11.0 GB** | alone only — `qwen3:8b` must be unloaded first (D7) |

`MIN_FREE_RAM_GB = 9.0` in `config.py` — minimum to start a parse with vision enabled.

---

## VRAM observations (RTX 5050, 8 GB VRAM, 24 GB RAM)

Confirmed in live testing on 2026-05-15:

```
qwen2.5vl:7b   — needs 8.6 GB system memory to load.
                  On this machine: only ~8.6 GB free → fails at the boundary.
                  FIX: close Chrome/heavy apps to free ≥ 9 GB before parsing.
                  FIX: VISION_TIMEOUT raised to 400s for slow CPU/GPU split.

llama3.2-vision:11b — loads at 11 GB total: 58% GPU (4.6 GB) + 42% CPU (4.7 GB via RAM).
                       Confirmed via ollama ps: CONTEXT 4096, UNTIL 10 minutes.
                       EXCLUDED from full-page OCR (times out at 180s — D15).
                       Used ONLY for region crops (smaller images, faster inference).
                       qwen3:8b CANNOT load alongside it — no memory left.

qwen3:8b       — loads at ~5 GB. Works fine alone.
                  Cannot coexist with llama3.2-vision:11b (D7).
```

### Loading rules enforced by `model_router.py`

1. `_probe_vision()` runs at start of each PDF — tries VISION_PRIMARY then VISION_FALLBACK.
2. Whichever model passes the probe is set as the **sticky model** for the whole PDF via `mark_success()`.
3. Phase boundaries (`before_vision_phase` / `before_orchestrator_phase`) handle unload/reload — no mid-round switching.
4. `teardown_pdf()` — at end of each PDF: unloads vision model, resets sticky state.
5. **Sticky model:** once a vision model succeeds, all calls reuse it for that PDF. No repeated load/unload churn.

---

## Ollama config applied to every call

| Parameter | Value | Config key | Effect |
|---|---|---|---|
| `num_ctx` | 4096 | `MODEL_NUM_CTX` | Smaller KV cache → less RAM |
| `keep_alive` | 0 | `MODEL_KEEP_ALIVE` | Model unloads immediately after call — explicit teardown handles lifecycle |
| `temperature` | 0.1 | hardcoded | Deterministic extraction |

> `MODEL_KEEP_ALIVE = 0` replaces the previous 600s value. Explicit phase-based unloads (via `model_router`) make the keep_alive warm-up unnecessary and wasteful.

---

## Task routing

| Task | Model | Fallback |
|---|---|---|
| Tool-calling / format / patch | `qwen3:8b` | none — skip round gracefully |
| Full-page OCR (round 1 only) | `qwen2.5vl:7b` (probe winner) | raw text (no llama3.2-vision for full page — D15) |
| Region description (ECG, diagram) | sticky vision model | raw text placeholder |
| Quality judge (all rounds) | sticky vision model | score=5.0 (neutral) |
| Context summarisation | `qwen3:8b` | `"[Summary unavailable]"` |

---

## Ollama API calls used

| Operation | Method | Notes |
|---|---|---|
| Chat / tool-calling | `ollama.chat()` | Python client |
| Vision call (image) | `ollama.chat()` with `images=` | PNG bytes, resized to ≤ `MAX_IMAGE_PX` |
| Check installed models | GET `/api/tags` | used by `system_check.get_installed_models()` |
| Check loaded models | GET `/api/ps` | used by `model_router.loaded_models()` |
| Unload model | POST `/api/generate` with `keep_alive: 0` | used by `model_router.unload()` |

Base URL: `http://localhost:11434` (`config.OLLAMA_BASE_URL`)

---

## Prompts (stored in vision_tools.py)

All prompts are domain-neutral — cloak parses any PDF type (D16).

### Full-page extraction
```
You are a document parser. Extract ALL content from this page into structured markdown.
Include every heading, section title, body text, table, list, figure caption,
footnote, and abbreviation visible on the page.
Do NOT summarise — extract verbatim where possible.
Use ## for major headings and ### for sub-headings.
Reproduce tables in markdown table format.
Output only the markdown. No preamble or closing remarks.
```

### Quality judge
```
You are a document QA reviewer. Score how completely the markdown captures
EVERYTHING visible on the page (0.0 to 10.0). List missing content as gaps.
Decide action: "accept" (≥8.0), "patch" (≥5.0), "fallback" (<5.0).
Respond ONLY with valid JSON: {"score": float, "gaps": [str], "action": str}
```

### FORMAT prompt (qwen3:8b — Phase 4, D20)
Single completion call — no tool loop. Input: raw extracted content. Output: structured markdown.

```
You are a document formatter. Convert the raw extracted text below into clean, well-structured markdown.
Preserve ALL content — do not remove, summarise, or paraphrase any information.
Add appropriate headings, lists, tables, and code blocks where they improve readability.
Fix spacing and paragraph breaks. Output ONLY the formatted markdown, nothing else.
```

Long documents are processed up to `MODEL_NUM_CTX * 3` chars per call; the unformatted tail is appended directly so no content is lost. Content-loss guard (D5) reverts to raw content if output < 65% of input.

### Region prompts (ECG / diagram / figure)
Stored in `vision_tools._REGION_PROMPTS` — separate detailed prompts per label type. Domain-neutral: ECG prompt describes waveform characteristics; diagram prompt describes layout and labels; figure prompt describes visual content.

---

## Known hardware limits on dev machine (2026-05-15)

- `qwen2.5vl:7b` is the **target primary model** — loads when ≥ 9 GB RAM free (close Chrome first)
- `llama3.2-vision:11b` loads but times out for full-page OCR — used only for region crops
- **To unlock the full quality loop:** free ≥ 9 GB RAM before running
- `VISION_TIMEOUT` raised to 400s in config.py — gives slow CPU/GPU inference more time
