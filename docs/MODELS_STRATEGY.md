# Model Strategy — cloak

> Which models to use for which tasks, why, and what to pull next.
> Updated: Session 28 · Hardware: RTX 5050 8 GB VRAM · 24 GB RAM

---

## What You Have Installed (Ollama)

| Model | Size | Best At | Role in cloak |
|---|---|---|---|
| `qwen3:14b` | 9.3 GB | Text reasoning, structured formatting, instruction-following | LLM — FORMAT, structuring, deep review |
| `glm-ocr:latest` | 2.2 GB | Document OCR, tables, formulas | Ground truth OCR (Phase 1b) |
| `qwen2.5vl:7b` | 6.0 GB | Document understanding, structured extraction | **Best VLM for cloak** — see below |
| `qwen3-vl:8b` | 6.1 GB | General vision | Current VISION_PRIMARY — should swap to 2.5vl |
| `qwen3-vl:4b` | 3.3 GB | Fast vision, fallback | VISION_FALLBACK |
| `gemma4:26b` | 17 GB | Multimodal, long context | Too large for regular use — Phase 9 only if needed |
| `qwen3.6:27b` | 17 GB | Agentic, long context reasoning | Too large — not recommended on this hardware |
| `codestral:22b` | 12 GB | Code generation | Not useful for PDF parsing |
| `qwen2.5-coder:14b` | 9.0 GB | Code generation | Not useful for PDF parsing |
| `llava:7b` | 4.7 GB | Basic vision | Older model, weaker than qwen2.5vl — skip |
| `qwen3.5:4b` | 3.4 GB | Small fast reasoning | Potential judge model — see below |
| `qwen3.5:latest` | 6.6 GB | Reasoning | Potential judge model |

---

## Key Decision: Switch VLM from qwen3-vl:8b → qwen2.5vl:7b

**Why qwen2.5vl:7b is better for cloak than qwen3-vl:8b:**

| | qwen3-vl:8b | qwen2.5vl:7b |
|---|---|---|
| Training focus | General (chat, reasoning, vision) | Document understanding (specifically trained on DocVQA, ChartQA, OCRBench) |
| Streaming behaviour | Inconsistent — enters batch mode, 0 tokens for 5+ min | More reliable streaming |
| Empty output rate | ~40% on complex poster images | Lower — document-tuned models handle dense layouts better |
| Size | 6.1 GB | 6.0 GB (same slot) |
| Already installed | ✓ | ✓ |

**Change: 1 line in `.cloak_local.json`**
```json
{
  "ORCHESTRATOR_MODEL": "qwen3:14b",
  "VISION_PRIMARY": "qwen2.5vl:7b",
  "VISION_FALLBACK": "qwen3-vl:4b",
  "DEEP_REVIEW_MODEL": "qwen3:14b"
}
```

---

## Recommended Model Role Table (after switch)

| Phase | Task | Model | Why |
|---|---|---|---|
| Phase 1b | Ground truth OCR (all pages) | `glm-ocr` | #1 OmniDocBench, 2.2 GB, always-resident |
| Phase 3 | Poster/flowchart extraction | `qwen2.5vl:7b` | Document-trained, reliable streaming |
| Phase 3 | Image-heavy page extraction | `qwen2.5vl:7b` | Same model, no swap needed |
| Phase 3 | Figure description (crops) | `qwen2.5vl:7b` | Same model |
| Phase 3 | Scanned page OCR | `glm-ocr` → surya → tesseract | Already configured |
| Phase 4 | Text structuring / FORMAT | `qwen3:14b` | Strong instruction-following |
| Phase 5 | Heuristic judge | none (instant) | GLM-OCR ground truth, no model |
| Phase 5 | VLM judge (scanned only) | `qwen3-vl:4b` | Faster, lighter — judge doesn't need the best model |
| Phase 6 | Gap-informed re-extraction | `qwen2.5vl:7b` | Same as extraction |
| Phase 7 | Deep review | `qwen3:14b` | Already loaded from Phase 4/6, zero reload |

---

## Models to Pull (not yet installed)

### Priority 1 — Pull now, high impact
```powershell
ollama pull qwen2.5vl:7b   # Already installed — just use it
```

No new pulls needed for the immediate switch. qwen2.5vl:7b is already there.

### Priority 2 — For judge role (optional)
```powershell
ollama pull qwen3.5:3b     # 2.0 GB — fast, fits alongside any other model
```
`qwen3.5:3b` would make an excellent judge:
- 2 GB — coexists with any VLM without RAM pressure
- qwen3.5 reasoning is strong for evaluation tasks
- Can output JSON reliably at this task complexity level

### Priority 3 — For two-stage structuring (future)
The installed `qwen3:14b` already covers two-stage structuring (GLM-OCR text → qwen3:14b → structured markdown). No additional model needed.

---

## Models NOT Worth Using for cloak

| Model | Why to skip |
|---|---|
| `gemma4:26b` | 17 GB — forces heavy RAM split, slow. Only if you have no 8B alternative. |
| `qwen3.6:27b` | 17 GB — same issue. Good model but wrong hardware. |
| `codestral:22b` | Code-only. 12 GB. No benefit for PDF parsing. |
| `qwen2.5-coder:14b` | Code-only. No benefit for PDF parsing. |
| `llava:7b` | Outdated VLM. qwen2.5vl:7b is strictly better at same size. |

---

## Two-Stage Structuring: The Right Pattern for Poster Fallback

When the VLM fails or produces empty output on a poster page, use this pattern:

```
GLM-OCR text (already extracted in Phase 1b)
  ↓
qwen3:14b (already loaded for Phase 4)
  ↓ prompt: "Here is raw OCR text from a clinical flowchart.
             Convert to structured markdown:
             - ALL-CAPS lines → ## headings
             - Indented or bulleted content → preserve hierarchy
             - Numbers that look like scores → keep as values
             - Preserve all clinical values exactly"
  ↓
Structured markdown with headings
```

This is more reliable than VLM because:
1. Text extraction (glm-ocr) is 99%+ accurate on born-digital PDFs
2. Structuring is a text task — no image needed
3. qwen3:14b excels at following explicit formatting rules

**Add to VISION_PRIMARY model in config**: When VLM returns empty, instead of GLM-OCR raw text fallback, call `qwen3:14b` to structure the GLM-OCR text.

---

## VRAM Budget with Recommended Stack

```
Phase 1b (GLM-OCR ground truth):
  glm-ocr         2.2 GB GPU    → fast text extraction

Phase 3 (VLM extraction):
  qwen2.5vl:7b    6.0 GB GPU
  glm-ocr         2.2 GB → auto-splits to RAM (0.2 GB)
  Total: 8.2 GB (0.2 GB RAM spill) ✓

Phase 4 / Phase 6 (LLM structuring/review):
  qwen3:14b       9.0 GB GPU+RAM (~8 GB VRAM + 1 GB RAM)
  glm-ocr         2.2 GB → RAM
  Total: 11.2 GB across GPU+RAM ✓

Phase 5 (VLM judge — rare):
  qwen3-vl:4b     3.3 GB GPU    → fast scoring
  Total: 3.3 GB ✓

Peak: 11.2 GB (well within 32 GB total pool)
```
