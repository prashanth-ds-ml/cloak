---
type: module-specs
updated: 2026-05-16 (Session 7)
---

# Module Specs — cloak

> Related: [[docs/ARCHITECTURE.md]] · [[docs/MODELS.md]] · [[docs/DECISIONS.md]] · [[docs/PROGRESS.md]]

**Existing modules** (written, wired, move pending — D26): `pdf_tools`, `vision_tools`, `quality_judge`, `model_router`, `context_manager`, `parser_agent`.
**New modules** (planned): `page_profiler`, `ocr_tools`, `system_check`, `cli/main.py`.
**Legacy files** (`pdf_extractor.py`, `pdf_classifier.py`, `vision.py`, `markdown_builder.py`): read-only, stay in `ingestion/`.

---

## 1 · extraction/pdf_tools.py ✅ done + tested

**Purpose:** Everything PDF → Python. No LLM calls.

### Key functions
```python
load_pages(pdf_path) -> list[PageData]      # main entry — runs all below
render_page(page, dpi=PAGE_DPI) -> PIL.Image
extract_blocks(page) -> list[Block]          # get_text('dict'), ligatures normalised
spatial_sort(blocks, page_width) -> list[Block]  # spanning → left col → right col
detect_regions(page, page_num, blocks) -> list[RegionCrop]  # clips page at image bboxes
extract_tables(pdf_path, page_num) -> list[TableData]       # pdfplumber per page
```

### Data types produced
```python
PageData(page_num, image, width, height, blocks, regions, tables)
Block(text, bbox, block_type)        # block_type: "text" | "image"
RegionCrop(image, bbox, label, page_num)  # label: "ecg" | "diagram" | "figure"
TableData(rows, page_num)            # .to_markdown() → markdown table string
```

### Tested
- `bradyarrhythmia.pdf`: 2 ECG regions detected + labelled ✓
- `atrial_fibrillation.pdf`: 1 diagram, 2 tables ✓
- Ligature normalisation (ﬂ → fl etc.) applied at extraction ✓

---

## 2 · vision/vision_tools.py ✅ done — prompts need generalisation

**Purpose:** All Ollama vision calls in one place. Thin wrappers with daemon-thread timeouts.

### Key functions
```python
full_page_extract(image, model, timeout) -> str   # full page → markdown
region_describe(image, label, model, timeout) -> str  # ECG/diagram → description
judge_quality(page_image, extracted_md, model, timeout) -> dict
    # returns: {"score": float, "gaps": [str], "action": "accept"|"patch"|"fallback"}
```

### Image handling
- All images resized to long edge ≤ `MAX_IMAGE_PX` (1024px) before encoding as PNG
- Encoding via `_prepare_image()` — LANCZOS downsample if needed

### Exceptions
```python
VisionTimeoutError   # daemon thread didn't complete within timeout
VisionCallError      # Ollama returned an error (e.g. insufficient RAM)
```

### JSON fallback in judge_quality
If model returns non-JSON, strips markdown fences and retries `json.loads`. On failure: returns `{"score": 0.0, "gaps": ["JSON parse failed"], "action": "patch"}` — never crashes.

### Pending — next session
- Generalize all prompts: remove "medical document parser" language → domain-neutral (D16)
- Add `format_markdown(raw_text, model, timeout) -> str` — qwen3:8b FORMAT call (D20)
- See [[MODELS.md]] §Prompts for the updated prompt text

---

## 3 · quality/quality_judge.py ✅ done — PageScore needs per-page update

**Purpose:** Typed scoring layer on top of `vision_tools.judge_quality()`.

### Key functions
```python
judge(page_image, extracted_md, round_num, model, timeout) -> JudgeResult
aggregate_page_results(results: list[JudgeResult]) -> (avg_score, all_gaps, action)
```

### PageScore (replaces JudgeResult — D24)
```python
@dataclass
class PageScore:
    page_num: int
    score: float        # 0.0 – 10.0
    confidence: str     # "High" (≥8.0) | "Medium" (≥5.0) | "Low" (<5.0)
    gaps: list[str]     # missing content descriptions
    action: str         # "accept" | "patch" | "fallback"
    round_num: int
    model: str
```

### Action thresholds
| Score | Action |
|---|---|
| ≥ 8.0 | `"accept"` — stop loop |
| ≥ 5.0 | `"patch"` — fill gaps |
| < 5.0 | `"fallback"` — switch vision model |

### Graceful fallback
If `VisionTimeoutError` or `VisionCallError` → returns `JudgeResult(score=5.0, action="patch")` — loop continues without crashing.

---

## 4 · orchestration/model_router.py ✅ done + wired

**Purpose:** VRAM-aware model selection. Enforces the VRAM coexistence rule.

### Key functions
```python
reset()                      # called at start of each PDF
get_vision_model() -> str    # returns sticky model or VISION_PRIMARY
mark_success(model)          # sets sticky model after a successful call
before_vision_phase()        # phase boundary: unloads qwen3:8b when llama3.2 is sticky
before_orchestrator_phase()  # phase boundary: unloads llama3.2 when sticky, resets sticky→None
teardown_pdf()               # unloads vision model, resets sticky
loaded_models() -> list[str] # GET /api/ps
unload(model)                # POST keep_alive=0
# kept for compatibility (not called in main path):
switch_to_fallback()
restore_orchestrator()
using_fallback() -> bool
```

### Call sequence in parser_agent (D14 — phase-based)
```
parse() start:            model_router.reset()
_probe_vision():          mark_success(winner)          ← sets sticky for whole PDF

Round 1:
  VISION PHASE:           before_vision_phase()         ← unload qwen3 if llama3.2 sticky
  _extract_all_pages():   get_vision_model()            ← sticky or VISION_PRIMARY (once only)
  quality_judge.judge():  get_vision_model()
  ORCHESTRATOR PHASE:     before_orchestrator_phase()   ← unload llama3.2; sticky→None
  _run_format_session():  qwen3:8b auto-loads
  _run_patch_loop():      qwen3:8b

Rounds 2+:
  VISION PHASE:           before_vision_phase()
  quality_judge.judge():  get_vision_model()            ← no re-extract (D19)
  ORCHESTRATOR PHASE:     before_orchestrator_phase()
  _run_patch_loop():      qwen3:8b

parse() end:              teardown_pdf()
```

### VRAM rule enforcement
`before_vision_phase()` checks if sticky == `VISION_FALLBACK` and calls `unload(ORCHESTRATOR_MODEL)`.
`before_orchestrator_phase()` checks if sticky == `VISION_FALLBACK`, calls `unload(VISION_FALLBACK)`, resets `_sticky_vision = None`.
`qwen2.5vl:7b` + `qwen3:8b` can coexist — neither boundary fires for them.

---

## 5 · orchestration/context_manager.py ✅ done

**Purpose:** Compress agent message history to stay under `CONTEXT_TOKEN_LIMIT` (8K tokens).

### Key functions
```python
estimate_tokens(messages) -> int     # rough: total chars / 4
compress_history(messages, token_limit, model) -> list[dict]
    # keeps: messages[0] (system prompt) + last 4 messages
    # summarises: everything between via qwen3:8b
summarise_messages(messages, model) -> str  # 200-word summary call
```

### Compression trigger
Only compresses if `estimate_tokens(messages) > CONTEXT_TOKEN_LIMIT`. Short conversations pass through unchanged.

### Safety
If summarise call times out → returns `"[Summary unavailable — timeout]"` and continues. Never crashes the loop.

---

## 6 · orchestration/parser_agent.py ✅ done — 8-phase pipeline implemented

**Purpose:** Orchestrator. Runs the full 8-phase pipeline. CLI entry point.

### Entry points
```bash
python -m cloak.orchestration.parser_agent data/raw/cardiology/heart_failure.pdf
```
```python
from cloak.orchestration.parser_agent import parse
parse("data/raw/cardiology/heart_failure.pdf")
```

### 8-phase loop (target — D14 + D19 + D20 + D21 + D23 + D24)
```
Phase 0  intake: load_pages(), create output dir
Phase 1  profiler: profile_all(pages) → RouteMap
Phase 3  extract: _extract_by_route(pages, route_map) — vision only for image_heavy/mixed (D23)
Phase 4  format:  qwen3:8b FORMAT session (D20) — once
Phase 5  judge:   quality_judge per page — every round (D21/D24)
Phase 6  patch:   qwen3:8b PATCH — targeted (D5 guard)
         repeat phases 5–6 up to MAX_ROUNDS, best round wins (D2/D3)
Phase 8  output:  final.md + confidence_report.md (D24)
model_router.teardown_pdf()
```

### Output
```
data/markdown/{specialty}/{stem}.md
data/markdown/{specialty}/{stem}_confidence.md
```

### 2-step extract cascade (`_extract_all_pages`) — D14/D15
```
1. sticky model          → mark_success on win
2. raw text blocks       → pg.text + table markdown + region placeholders
   (llama3.2-vision:11b is NOT attempted for full-page OCR — times out on this hardware)
```

### Tool-calling tools (qwen3:8b patch loop)
| Tool | Purpose |
|---|---|
| `get_page_text(page_num)` | Return sorted text for a page |
| `get_region_description(page_num, region_index)` | Vision describe a region crop |
| `patch_section(heading, content)` | Replace a section in current markdown |
| `add_section(heading, content)` | Append a new section |
| `finish(markdown)` | Signal patch complete, return final markdown |

### FORMAT session (qwen3:8b — Phase 4, once — D20)
Single `ollama.chat()` completion call — no tool loop. Input: raw_content string. Output: structured markdown.

System prompt instructs: preserve ALL content, add headings/lists/tables, fix spacing. Content-loss guard (D5) reverts if formatted output < 65% of input length. Long documents are processed up to `MODEL_NUM_CTX * 3` chars; the unformatted tail is appended so no content is silently dropped.

### Content-loss guard
After every format or patch: `if len(new) < len(old) * 0.65` → revert. Configured via `config.CONTENT_LOSS_LIMIT`.

### Output path logic
`data/raw/{specialty}/{stem}.pdf` → `data/markdown/{specialty}/{stem}.md`
Falls back to `data/markdown/{stem}.md` if no `raw/` directory in path.

---

## 7 · profiling/page_profiler.py ✅ done

**Purpose:** Classify each PDF page heuristically (zero models) and produce a RouteMap that drives extraction strategy selection in Phase 3. See [[docs/DECISIONS.md]] §D21.

### Key functions
```python
profile_page(page: PageData) -> PageProfile
profile_all(pages: list[PageData]) -> list[PageProfile]
build_route_map(profiles: list[PageProfile]) -> RouteMap
summarise(profiles: list[PageProfile]) -> dict[str, int]   # count by type
```

### PageProfile
```python
@dataclass
class PageProfile:
    page_num: int
    text_length: int          # chars extracted by PyMuPDF
    image_area_ratio: float   # total image bbox area / page area  (0.0–1.0)
    table_count: int          # pdfplumber tables found on this page
    page_type: str            # "text_rich" | "table_heavy" | "image_heavy" | "scanned" | "mixed"
    needs_ocr: bool
    needs_vision: bool
```

### Classification rules (priority order — earlier takes precedence)
| Condition | Type |
|---|---|
| `text_length < SCANNED_TEXT_THRESHOLD(100) AND image_area_ratio > IMAGE_AREA_THRESHOLD(0.4)` | `scanned` |
| `image_area_ratio > 0.5 AND text_length < SCANNED_TEXT_THRESHOLD * 5` | `image_heavy` |
| `table_count >= 2` | `table_heavy` |
| `image_area_ratio > 0.2 AND text_length >= SCANNED_TEXT_THRESHOLD` | `mixed` |
| everything else | `text_rich` |

Note: `image_heavy` requires sparse text (`< 500 chars`) so PDFs with large background images but real digital text are classified as `mixed`, not `image_heavy`.

### RouteMap
```python
RouteMap = dict[int, str]  # {page_num: page_type}
```

### Dependencies
- Uses `PageData` from `extraction/pdf_tools.py` — no additional imports needed
- `config.SCANNED_TEXT_THRESHOLD`, `config.IMAGE_AREA_THRESHOLD`

---

## 8 · extraction/ocr_tools.py ✅ done

**Purpose:** Tesseract OCR wrapper for scanned pages. Called by `parser_agent` for pages where `RouteMap[page_num] == "scanned"`. See [[docs/DECISIONS.md]] §D22.

### Key functions
```python
ocr_page(image: PIL.Image, lang: str = "eng") -> str
    # renders page through pytesseract → clean text string
    # preprocesses: convert to grayscale, mild contrast boost

clean_ocr_text(raw: str) -> str
    # remove page numbers, repeated headers, fix hyphenation
    # normalise whitespace and line endings
```

### Image preprocessing (before OCR)
```python
_preprocess(image: PIL.Image) -> PIL.Image:
    # 1. convert to grayscale
    # 2. ImageFilter.SHARPEN (mild)
    # 3. resize to min 2000px long edge for OCR resolution
    # returns preprocessed PIL image
```

### Exception handling
```python
OCRError  # raised if tesseract binary not found or call fails
```
On `OCRError` → caller falls back to raw PyMuPDF text blocks (same as text_rich path).

### Dependencies
- `pytesseract` — Python wrapper
- Tesseract binary must be installed on system (Windows: Tesseract-OCR installer)
- `Pillow` (already in deps)

### Config
```python
OCR_LANG = "eng"  # add to config.py — extend to "eng+hin" etc. if needed
```

---

## 9 · cli/system_check.py ✅ done

**Purpose:** Hardware probe + model suitability display at startup. RAM gate before parsing begins.

**Location:** `cloak/cli/system_check.py` — imported as `from cloak.cli import system_check`

### Key functions
```python
get_free_ram_gb() -> float              # psutil.virtual_memory().available / 1e9
get_total_ram_gb() -> float             # psutil.virtual_memory().total / 1e9
get_free_vram_gb() -> float             # nvidia-smi --query-gpu=memory.free (MiB → GB)
get_total_vram_gb() -> float            # nvidia-smi --query-gpu=memory.total
get_gpu_name() -> str                   # nvidia-smi --query-gpu=name
is_ollama_running() -> bool             # GET /api/tags — returns False on any error
get_installed_models() -> list[str]     # GET /api/tags → model name list
check_model_suitability(model, free_ram_gb) -> dict
    # {"model": str, "status": "ready"|"marginal"|"unavailable", "reason": str, "required_gb": float}
show_startup_screen() -> None           # banner + hardware grid + model status table
ram_gate(min_gb=MIN_FREE_RAM_GB) -> bool  # warns if low, never blocks
```

All functions are safe to call anytime — never raise; failures return sentinel values (0.0, "unknown", []) and print a warning.

### Model RAM requirements (from [[DECISIONS.md]] §D18)
| Model | Min free RAM needed | Role |
|---|---|---|
| `qwen2.5vl:7b` | 9.0 GB | Vision primary |
| `qwen3:8b` | 5.5 GB | Orchestrator |
| `llama3.2-vision:11b` | 11.0 GB | Vision fallback |

Marginal threshold: free_ram ≥ required × 0.85.

### RAM gate behaviour
If `free_ram < MIN_FREE_RAM_GB` (9.0 GB): print warning, return `False`. Never blocks execution — let the runtime probe (`_probe_vision`) decide.

### Dependencies
- `psutil` (in pyproject.toml)
- `typer` (in pyproject.toml)
- `subprocess` — for `nvidia-smi` calls
- `httpx` — for Ollama API calls
- `rich` — for display

---

## 10 · cli/main.py ✅ done

**Purpose:** typer CLI entry point. Shows startup screen on every invocation.

### Commands
```
cloak                    → startup screen + command list
cloak parse <pdf|dir>    → parse single PDF or all PDFs in a directory
cloak status             → hardware + model status only
cloak list               → table of data/markdown/ contents with size + date + confidence flag
```

### parse command behaviour
- Accepts a file (`*.pdf`) or directory (recursively finds all `.pdf` files)
- Shows startup screen first (hardware + model status)
- Parses each PDF via `orchestration.parser_agent.parse()`
- Collects errors per file — reports failures at the end, does not abort on first error

### Package entry point (`pyproject.toml`)
```toml
[project.scripts]
cloak = "cloak.cli.main:app"
```

### Dependencies
- `typer>=0.12.0` (in pyproject.toml)
- `rich` — for table display in `cloak list`
