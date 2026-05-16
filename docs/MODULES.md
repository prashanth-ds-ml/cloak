---
type: module-specs
updated: 2026-05-16 (Session 8)
---

# Module Specs — cloak

> Related: [[docs/ARCHITECTURE.md]] · [[docs/MODELS.md]] · [[docs/DECISIONS.md]] · [[docs/PROGRESS.md]]

**All 11 modules done.** `pdf_tools`, `vision_tools`, `quality_judge`, `model_router`, `context_manager`, `parser_agent`, `page_profiler`, `ocr_tools`, `system_check`, `cli/main.py`, `deep_review`.
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

## 2 · vision/vision_tools.py ✅ done

**Purpose:** All Ollama vision calls in one place. Thin wrappers with daemon-thread timeouts. All prompts are domain-neutral (D16).

### Public API
```python
full_page_extract(image, model, timeout) -> str   # full page → markdown (headings from visual layout)
region_describe(image, label, model, timeout) -> str  # ECG/diagram → description
judge_quality(page_image, extracted_md, model, timeout) -> dict
    # returns: {"score": float, "gaps": [str], "action": "accept"|"patch"|"fallback"}
```

`layout_hints()` was removed in Session 8 — heading detection now happens inside `full_page_extract()` directly (D23 update). `_build_layout_context()` in parser_agent was also removed.

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

## 6 · orchestration/parser_agent.py ✅ done — 9-phase pipeline

**Purpose:** Orchestrator. Runs the full 9-phase pipeline. CLI entry point.

### Entry point
```python
from cloak.orchestration.parser_agent import parse
parse(pdf_path: Path, deep_review: bool = True)
```

### 9-phase pipeline (D14 + D19 + D20 + D21 + D23 + D24 + D27)
```
Phase 0  intake:    load_pages(), create output dir, create images_dir
Phase 1  profiler:  profile_all(pages) → RouteMap
Phase 3  extract:   _extract_by_route(pages, route_map, images_dir)
           → ALL pages use vision when available (D23 updated)
           → text_rich: _extract_text_page_vision() — full_page_extract for headings
           → image_heavy/mixed: _extract_text_page_vision() — full page + region describe
           → table_heavy: _extract_table_page()
           → scanned: _extract_scanned_page()
Phase 4  format:    qwen3:8b FORMAT session (D20) — once; /no_think prefix; FORMAT_NUM_CTX=8192
Phase 5  judge:     quality_judge per page — every round (D24)
Phase 6  patch:     qwen3:8b PATCH — targeted (D5 guard)
          repeat phases 5–6 up to MAX_ROUNDS, best round wins (D2/D3)
Phase 8  output:    final.md + confidence_report.md (D24) + images logged
model_router.teardown_pdf()
Phase 9  deep review: deep_review.run() — gemma4:latest CPU+GPU split (D27)
```

### Extraction functions
```python
_extract_by_route(pages, route_map, images_dir=None) -> str        # dispatcher
_extract_text_page_vision(pg, model, images_dir=None) -> str       # vision for ANY page type
_extract_text_page(pg) -> str                                       # pdfplumber fallback (no vision)
_extract_table_page(pg) -> str                                      # pdfplumber tables
_extract_scanned_page(pg) -> str                                    # Tesseract OCR
_extract_mixed_page(pg, model, images_dir=None) -> str             # text + vision for regions
_extract_vision_page(pg, model, images_dir=None) -> str            # image_heavy full-page
```

### Region image persistence
```python
_images_dir(md_path: Path) -> Path    # returns {stem}_images/ dir, creates if needed
_save_region(image, images_dir, page_num, label, idx) -> Path  # saves PNG, returns relative path
```
Region crops saved as `{stem}_images/page_{n}_{label}_{i}.png`. Embedded in markdown as `![label](relative_path)`.

### Output
```
data/markdown/{specialty}/{stem}.md
data/markdown/{specialty}/{stem}_confidence.md
data/markdown/{specialty}/{stem}_review.md    ← Phase 9 (if deep_review=True)
data/markdown/{specialty}/{stem}_images/      ← region crops (if any regions found)
  page_0_ecg_0.png
  page_3_diagram_0.png
  ...
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
Single `ollama.chat()` completion call — no tool loop. Input: raw_content string. Output: structured markdown. Context: `FORMAT_NUM_CTX = 8192` (larger than standard to prevent qwen3 thinking tokens truncating output).

System prompt starts with `/no_think` (suppresses qwen3 thinking chain). 6 rules: content from pre-structured vision output → dedup → preserve headings/levels → tables → merge abbreviation lists → no preamble. Content-loss guard (D5) reverts if output < 65% of input.

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

**Purpose:** Hardware probe + VRAM-aware model suitability display at startup. Startup memory cleanup. RAM gate before parsing begins.

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
check_model_suitability(model, free_ram_gb, free_vram_gb=0.0) -> dict
    # VRAM-aware priority: GPU → CPU+GPU → CPU → marginal → unavailable
    # returns: {"model", "status", "backend", "note", "reason", "required_vram_gb", "required_ram_gb"}
show_startup_screen(show_commands=False) -> None  # banner + hardware grid + model status table
run_startup_cleanup() -> None            # unload idle Ollama models; show top procs if RAM tight
ram_gate(min_gb=MIN_FREE_RAM_GB) -> bool  # warns if low, never blocks
get_top_processes(n=6, min_mb=250) -> list[dict]  # top N processes by RAM for memory hint
```

All functions are safe to call anytime — never raise; failures return sentinel values (0.0, "unknown", []).

### VRAM-aware suitability (Session 8 — D18)
`check_model_suitability()` now accepts `free_vram_gb`. Priority:
1. GPU — fits fully in VRAM → `ready (GPU)`
2. CPU+GPU split — VRAM + RAM covers model → `ready (CPU+GPU)`
3. CPU — no GPU but RAM sufficient → `ready (CPU)` (yellow)
4. Marginal (≥ 85% of needed) → `marginal`
5. Otherwise → `unavailable`

### Model requirements (Session 8)
| Model | VRAM needed | RAM needed | Role |
|---|---|---|---|
| `qwen2.5vl:7b` | 7.3 GB | 9.0 GB | Vision primary |
| `qwen3:8b` | 5.2 GB | 5.5 GB | Orchestrator |
| `qwen3-vl:4b` | 3.5 GB | 4.5 GB | Vision fallback |

### RAM gate behaviour
If both VRAM and RAM are insufficient for `VISION_PRIMARY`: print warning, return `False`. Never blocks execution — let the runtime probe (`_probe_vision`) decide.

### Startup screen visibility (D17 update)
`show_startup_screen(show_commands=True)` — called only on bare `cloak`. `show_startup_screen()` (no commands) — called only on `cloak status`. NOT called on `cloak parse` or `cloak list`.

### Dependencies
- `psutil` (in pyproject.toml)
- `subprocess` — for `nvidia-smi` calls
- `httpx` — for Ollama API calls
- `rich` — for display

---

## 10 · cli/main.py ✅ done

**Purpose:** typer CLI entry point. Startup screen only on bare `cloak` and `cloak status` (D17 updated).

### Commands
```
cloak                    → run_startup_cleanup() + show_startup_screen(show_commands=True)
cloak parse <pdf|dir>    → parse single PDF or all PDFs; no startup screen; supports --no-review
cloak status             → run_startup_cleanup() + show_startup_screen()
cloak list               → table of data/markdown/ contents with size + date + confidence flag
```

### parse command behaviour
- Accepts a file (`*.pdf`) or directory (recursively finds all `.pdf` files)
- Does NOT show startup screen (removed in Session 8 — reduces noise in parse output)
- `--no-review` flag skips Phase 9 deep review (`deep_review=False` passed to `parse()`)
- Parses each PDF via `orchestration.parser_agent.parse(pdf, deep_review=not no_review)`
- Collects errors per file — reports failures at the end, does not abort on first error

### Package entry point (`pyproject.toml`)
```toml
[project.scripts]
cloak = "cloak.cli.main:app"
```

### Dependencies
- `typer>=0.12.0` (in pyproject.toml)
- `rich` — for table display in `cloak list`

---

## 11 · quality/deep_review.py ✅ done — Phase 9

**Purpose:** Post-pipeline deep quality review. Loads `gemma4:latest` after all pipeline models are unloaded, compares raw pdfplumber text (ground truth) vs final AI-processed markdown, writes actionable quality improvement report. See [[docs/DECISIONS.md]] §D27.

### Entry point
```python
from cloak.quality import deep_review as dr
rev_path = dr.run(
    pdf_path: Path,
    pages: list,           # list[PageData] — still in memory after pipeline
    final_markdown: str,
    review_out: Path,      # {stem}_review.md path
    console,               # rich Console
) -> Path | None           # returns path written, or None if failed
```

### Internal functions
```python
_call(raw_text: str, final_md: str) -> str
    # Truncates inputs to 10,000 chars each
    # Calls DEEP_REVIEW_MODEL via daemon thread with DEEP_REVIEW_TIMEOUT
    # Returns review text

_unload() -> None
    # POST keep_alive=0 to Ollama API for DEEP_REVIEW_MODEL
    # Always called in finally block — model never left loaded
```

### Report structure
```markdown
# Quality Review — {pdf_name}
**Model:** `gemma4:latest`  ·  **Pages reviewed:** N

---
## Missing Content
## Wrong or Missing Headings
## Table Issues
## Duplicate Content
## Formatting Problems
## Overall Assessment
## Quality Score
Score: X/10
## Priority Fixes
```

### Memory strategy
Called after `model_router.teardown_pdf()` — all pipeline models unloaded. `gemma4:latest` (9.6 GB) exceeds pure VRAM but Ollama places it automatically across GPU + CPU RAM. No explicit split management needed.

### Config
```python
DEEP_REVIEW_MODEL   = "gemma4:latest"   # config.py
DEEP_REVIEW_TIMEOUT = 600               # 10 min — CPU+GPU split is slower
```

### Error behaviour
If `DEEP_REVIEW_MODEL` is not installed or call fails: prints warning, returns `None`. Parse output is unaffected — review is best-effort.
