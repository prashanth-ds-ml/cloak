# cloak — CLI Guide

> Content-aware Local Ollama Agentic Knowledge Parser  
> Converts any PDF to structured markdown. All processing is local — no data leaves your machine.

---

## Prerequisites

Before using cloak you need:

| Requirement | Install command |
|---|---|
| [Ollama](https://ollama.com) | Download from ollama.com |
| qwen3:8b (orchestrator) | `ollama pull qwen3:8b` |
| qwen2.5vl:7b (vision) | `ollama pull qwen2.5vl:7b` |
| Tesseract OCR (for scanned pages) | `winget install UB-Mannheim.TesseractOCR` |

Make sure Ollama is running before any parse:
```powershell
ollama serve
```

---

## Setup

```powershell
# 1. Clone the repo
git clone https://github.com/prashanth-ds-ml/cloak.git
cd cloak

# 2. Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install cloak and all dependencies
pip install -e .
```

Verify the install worked:
```powershell
cloak --help
```

---

## Commands

### `cloak` — startup screen

Shows hardware status, free RAM, and whether each model is ready to load.
Run this first to confirm the system can support vision parsing.

```powershell
cloak
```

Example output:
```
╭────────────────────────────────────────────────────────────╮
│ cloak  Content-aware Local Ollama Agentic Knowledge Parser │
│ Local-only · No data leaves your machine                   │
╰────────────────────────────────────────────────────────────╯
GPU     NVIDIA GeForce RTX 5050 Laptop GPU  8 GB VRAM  (7.2 GB free)
RAM     25 GB total  /  11.4 GB free
Ollama  http://localhost:11434  running

    Model                     Role              Status
✓   qwen2.5vl:7b              vision primary    ready
✓   qwen3:8b                  orchestrator      ready
✗   llama3.2-vision:11b       vision fallback   unavailable
```

**Status meanings:**
- `ready` — enough free RAM, model can load
- `marginal` — borderline RAM; close other apps first
- `unavailable` — not enough RAM; vision features will be skipped
- `not pulled` — model isn't downloaded yet (`ollama pull <model>`)

---

### `cloak status` — hardware check only

Same as the startup screen. Useful to check RAM without triggering a parse.

```powershell
cloak status
```

---

### `cloak parse <path>` — parse a PDF or directory

#### Parse a single PDF
```powershell
cloak parse data/samples/cardiology_af.pdf
```

#### Parse all PDFs in a folder
```powershell
cloak parse data/samples/
```

#### Parse PDFs from a specific specialty folder
```powershell
cloak parse data/raw/cardiology/
```

**What happens during parse:**

```
Phase 0  Loading pages ...
Phase 1  Profiling ...     (classifies each page: text_rich | scanned | image_heavy | mixed | table_heavy)
Phase 2  Routing plan:     (shows how many pages of each type)
Phase 3  Extracting ...    (PyMuPDF / Tesseract OCR / vision — per page type)
Phase 4  Formatting ...    (qwen3:8b restructures raw text into clean markdown)

Round 1/4  Judge + Patch
  Score: 7.2/10  action=patch
  Patching 3 gap(s) ...

Round 2/4  Judge + Patch
  Score: 8.4/10  action=accept
  Quality threshold 8.0 reached — stopping early

Done.  Best round: 2  Score: 8.4/10
Output:     data/markdown/cardiology/cardiology_af.md
Confidence: data/markdown/cardiology/cardiology_af_confidence.md
```

**Output files:**

| File | Contents |
|---|---|
| `data/markdown/{folder}/{name}.md` | Extracted markdown — the main output |
| `data/markdown/{folder}/{name}_confidence.md` | Per-page quality report |

---

### `cloak list` — view parsed documents

Lists all markdown files in `data/markdown/` with size, date, and whether a confidence report exists.

```powershell
cloak list
```

Example output:
```
           Parsed documents  (data\markdown)
┌───────────────────────────────────┬───────┬──────────────────┬────────────┐
│ File                              │  Size │ Parsed           │ Confidence │
├───────────────────────────────────┼───────┼──────────────────┼────────────┤
│ cardiology\cardiology_af.md       │ 14 KB │ 2026-05-16 18:30 │ yes        │
│ neurology\neurology_stroke.md     │  9 KB │ 2026-05-16 17:45 │ yes        │
└───────────────────────────────────┴───────┴──────────────────┴────────────┘
```

---

## Understanding the output

### `final.md`
Structured markdown of the full PDF. Sections have `##` headings, tables are rendered as markdown tables, figures are described inline.

### `_confidence.md`
Per-page quality report. Use this to know which pages to manually review.

```markdown
# Confidence Report — cardiology_af.pdf

| Page | Confidence | Score | Notes |
|---|---|---|---|
| 1  | High   | 9.1 | —                              |
| 4  | Medium | 6.8 | table structure uncertain       |
| 7  | Low    | 3.2 | scanned page — review manually  |
```

| Level | Score | Meaning |
|---|---|---|
| High | ≥ 8.0 | Extraction looks complete |
| Medium | 5.0–7.9 | Minor gaps, usable but verify |
| Low | < 5.0 | Significant content may be missing — review the source page |

---

## Hardware tips

### Vision parsing needs ≥ 9 GB free RAM

`qwen2.5vl:7b` requires ~9 GB free system RAM to load. If the startup screen shows `unavailable`:

1. Close Chrome, browser tabs, and heavy apps
2. Run `cloak status` again to confirm RAM is free
3. Run your parse

### Text-only fallback

If vision cannot load, cloak automatically falls back to text-only extraction (PyMuPDF + pdfplumber + Tesseract). Output quality is lower for pages with diagrams or images, but the pipeline never crashes.

### OCR for scanned PDFs

Pages with no extractable text and a scanned image are automatically routed to Tesseract OCR. Install the binary first:

```powershell
winget install UB-Mannheim.TesseractOCR
```

If Tesseract is not installed, scanned pages fall back to raw PyMuPDF text (usually empty for scanned pages — low confidence score will flag this).

---

## Full test sequence

Run these commands in order to verify the full pipeline:

```powershell
# Step 1 — activate environment
.\.venv\Scripts\Activate.ps1

# Step 2 — check hardware and model status
cloak status

# Step 3 — parse one sample PDF (text-heavy, fast)
cloak parse data/samples/neurology_stroke.pdf

# Step 4 — view the output
cloak list

# Step 5 — read the markdown output
cat data/markdown/neurology_stroke.md

# Step 6 — read the confidence report
cat data/markdown/neurology_stroke_confidence.md

# Step 7 — parse a full folder
cloak parse data/samples/

# Step 8 — list everything
cloak list
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `cloak: command not found` | Run `.\.venv\Scripts\Activate.ps1` first, or use `.\.venv\Scripts\cloak.exe` directly |
| `Ollama is not running` | Run `ollama serve` in a separate terminal |
| Vision `unavailable` in status | Free ≥ 9 GB RAM — close Chrome and heavy apps |
| Scanned pages empty | Install Tesseract: `winget install UB-Mannheim.TesseractOCR` |
| Parse is very slow | Vision model cold-loading — check that `MODEL_KEEP_ALIVE = -1` in `config.py` (models should stay loaded within a phase) |
| Low confidence on all pages | Vision model not loading — check `cloak status` and free more RAM |
| `ImportError` on first run | Run `pip install -e .` from the project root with the venv activated |

---

## Config reference

All tuning knobs are in `cloak/config.py`:

| Key | Default | What it controls |
|---|---|---|
| `QUALITY_THRESHOLD` | `8.0` | Stop the judge loop early when score ≥ this |
| `MAX_ROUNDS` | `4` | Maximum judge→patch iterations |
| `MODEL_KEEP_ALIVE` | `-1` | Model stays loaded until explicit phase-boundary unload — no cold reloads within a phase |
| `VISION_TIMEOUT` | `400` | Seconds before a vision call is aborted |
| `AGENT_TIMEOUT` | `150` | Seconds before an orchestrator call is aborted |
| `PAGE_DPI` | `150` | Resolution for page rendering |
| `MAX_IMAGE_PX` | `1024` | Long-edge cap before sending image to vision model |
| `SCANNED_TEXT_THRESHOLD` | `100` | Chars below which a page is treated as scanned |
| `OCR_LANG` | `"eng"` | Tesseract language code |
| `CONTENT_LOSS_LIMIT` | `0.35` | Revert patch if >35% of chars are removed |
