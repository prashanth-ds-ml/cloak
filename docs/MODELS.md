---
type: model-reference
updated: 2026-05-16 (Session 8)
---

# Model Reference — cloak

> Related: [[ARCHITECTURE.md]] · [[MODULES.md]] · [[DECISIONS.md]]

All models run locally via Ollama. No cloud API calls.

---

## Model roster

| Model | Role | VRAM | Timeout |
|---|---|---|---|
| `qwen3:8b` | Orchestrator — format, patch, tool-calling, context summarise | ~5.2 GB | 150s |
| `qwen2.5vl:7b` | Vision primary — OCR, layout, quality judge | ~7.3 GB | 400s |
| `qwen3-vl:4b` | Vision fallback — same VL family, fits fully in GPU (replaced llama3.2-vision, D15) | ~3.5 GB | 400s |
| `gemma4:latest` | Phase 9 deep review only — CPU+GPU split after pipeline teardown | ~9.6 GB (GPU+RAM) | 600s |
| `mistral:7b` | Available locally but **not used** in parser | — | — |

---

## Model suitability table (D18, updated Session 8)

Checked at startup via `system_check.check_model_suitability()`. Confirmed on RTX 5050 8 GB VRAM / 24 GB RAM.

| Model | VRAM needed | RAM needed | Status on RTX 5050 | Coexists with |
|---|---|---|---|---|
| `qwen2.5vl:7b` | ~7.3 GB | 9.0 GB | **ready (GPU)** | `qwen3:8b` (safe) |
| `qwen3:8b` | ~5.2 GB | 5.5 GB | **ready (GPU)** | `qwen2.5vl:7b` (safe) |
| `qwen3-vl:4b` | ~3.5 GB | 4.5 GB | **ready (GPU)** | both (safe — all three coexist) |
| `gemma4:latest` | 9.6 GB (split) | — | **ready (CPU+GPU)** — only after teardown | loaded alone only (Phase 9) |

`MIN_FREE_RAM_GB = 9.0` in `config.py` — minimum to start a parse with vision enabled.

---

## VRAM observations (RTX 5050, 8 GB VRAM, 24 GB RAM)

```
qwen2.5vl:7b   — ~7.3 GB VRAM (vision encoder makes it larger than base 7B).
                  Needs ≥ 9 GB free RAM at startup. Close Chrome/heavy apps first.
                  VISION_TIMEOUT = 400s — gives slow GPU time to complete.
                  Confirmed: shows "ready (GPU)" on startup screen when ≥ 9 GB free.

qwen3:8b       — ~5.2 GB VRAM. Works fine. Coexists with qwen2.5vl:7b.

qwen3-vl:4b    — ~3.5 GB VRAM. Loads fully on GPU. Same VL family as qwen2.5vl.
                  Replaces llama3.2-vision:11b as VISION_FALLBACK (Session 8, D15).
                  All three pipeline models fit in 8 GB VRAM simultaneously.

llama3.2-vision:11b — (historical) loaded at 11 GB: 58% GPU + 42% CPU RAM.
                       Timed out on full-page OCR at 400s. Removed as fallback.
                       Replaced by qwen3-vl:4b.

gemma4:latest  — 9.6 GB. Used only for Phase 9 deep review after teardown.
                  Ollama auto-places across GPU VRAM + CPU RAM (CPU+GPU split).
                  DEEP_REVIEW_TIMEOUT = 600s — slower on split.
```

### Loading rules enforced by `model_router.py`

1. `_probe_vision()` runs at start of each PDF — tries VISION_PRIMARY then VISION_FALLBACK.
2. Whichever model passes the probe is set as the **sticky model** for the whole PDF via `mark_success()`.
3. Phase boundaries (`before_vision_phase` / `before_orchestrator_phase`) are called at phase starts — with qwen3-vl:4b fallback these are no-ops (all models coexist safely).
4. `teardown_pdf()` — at end of each PDF: unloads vision model, resets sticky state.
5. **Sticky model:** once a vision model succeeds, all calls reuse it for that PDF. No repeated load/unload churn.

---

## Ollama config applied to every call

| Parameter | Value | Config key | Effect |
|---|---|---|---|
| `num_ctx` | 4096 | `MODEL_NUM_CTX` | Smaller KV cache → less RAM |
| `num_ctx` (FORMAT only) | 8192 | `FORMAT_NUM_CTX` | Larger context for Phase 4 — prevents qwen3 thinking tokens truncating output |
| `keep_alive` | 0 | `MODEL_KEEP_ALIVE` | Model unloads immediately after call — explicit teardown handles lifecycle |
| `temperature` | 0.1 | hardcoded | Deterministic extraction |

> `MODEL_KEEP_ALIVE = 0` replaces the previous 600s value. Explicit phase-based unloads (via `model_router`) make the keep_alive warm-up unnecessary and wasteful.

---

## Task routing

| Task | Model | Fallback |
|---|---|---|
| Tool-calling / format / patch | `qwen3:8b` | none — skip round gracefully |
| Full-page extraction (round 1 only) | probe winner: `qwen2.5vl:7b` or `qwen3-vl:4b` | pdfplumber/OCR text |
| Region description (ECG, diagram) | sticky vision model | raw text placeholder |
| Quality judge (all rounds) | sticky vision model | score=5.0 (neutral) |
| Context summarisation | `qwen3:8b` | `"[Summary unavailable]"` |
| Phase 9 deep review | `gemma4:latest` | skipped (returns None, prints warning) |

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

## Prompts

All prompts are domain-neutral — cloak parses any PDF type (D16).

### Full-page extraction (vision_tools.py)
```
You are a document parser. Extract ALL content from this page into structured markdown.
Include every heading, section title, body text, table, list, figure caption,
footnote, and abbreviation visible on the page.
Do NOT summarise — extract verbatim where possible.
Use ## for major headings and ### for sub-headings.
Reproduce tables in markdown table format.
Output only the markdown. No preamble or closing remarks.
```

Used for ALL page types when vision is available — text_rich, image_heavy, and mixed (D23 updated). Headings are assigned from the visual layout in a single pass.

### Quality judge (vision_tools.py)
```
You are a document QA reviewer. Score how completely the markdown captures
EVERYTHING visible on the page (0.0 to 10.0). List missing content as gaps.
Decide action: "accept" (≥8.0), "patch" (≥5.0), "fallback" (<5.0).
Respond ONLY with valid JSON: {"score": float, "gaps": [str], "action": str}
```

### FORMAT prompt (parser_agent.py — qwen3:8b, Phase 4, D20)
Single completion call — no tool loop. Starts with `/no_think` (suppresses qwen3 thinking chain). Context: `FORMAT_NUM_CTX = 8192`.

```
/no_think
You are a document formatter. The content below has already been extracted by a vision model
and may already have headings and structure. Your job:
1. Preserve ALL content — never remove, summarise, or paraphrase.
2. Remove duplicate sections if the same content appears twice (keep first occurrence).
3. Preserve all existing headings and their levels (## / ###). Add headings only where clearly missing.
4. Fix table markdown syntax. Never drop table rows or columns.
5. Merge separate abbreviation lists into one Abbreviations section at the end.
6. Output ONLY the markdown — no preamble, no "Here is the formatted..." intro.
```

Content-loss guard (D5) reverts to raw content if output < 65% of input. Long documents processed up to `FORMAT_NUM_CTX * 3` chars; unformatted tail appended directly so no content is lost.

### Region prompts (vision_tools.py)
Stored in `vision_tools._REGION_PROMPTS` — separate detailed prompts per label type. Domain-neutral: ECG prompt describes waveform characteristics; diagram prompt describes layout and labels; figure prompt describes visual content.

### Deep review prompt (deep_review.py — gemma4:latest, Phase 9, D27)
Structured audit comparing raw pdfplumber text vs final markdown. Produces sections: Missing Content, Wrong/Missing Headings, Table Issues, Duplicate Content, Formatting Problems, Overall Assessment, Quality Score (0–10), Priority Fixes.

---

## Known hardware limits on dev machine (Session 8)

- `qwen2.5vl:7b` is the **target primary model** — loads when ≥ 9 GB RAM free (close Chrome first). Shows `ready (GPU)` when ≥ 7.3 GB VRAM free
- `qwen3-vl:4b` is the **fallback** — loads in 3.5 GB VRAM, always `ready (GPU)` on RTX 5050
- **To unlock the full quality loop:** free ≥ 9 GB RAM before running
- `VISION_TIMEOUT` = 400s, `DEEP_REVIEW_TIMEOUT` = 600s (gemma4 is slower on CPU+GPU split)
