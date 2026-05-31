---
type: module-specs
updated: 2026-05-23 (Session 19)
---

# Module Specs — cloak

> Related: [[docs/ARCHITECTURE.md]] · [[docs/MODELS.md]] · [[docs/DECISIONS.md]] · [[docs/PROGRESS.md]]

**14 modules done. 1 planned (D38 — slide deck mode).**
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
region_describe(image, label, model, timeout) -> str  # figure/diagram → description
judge_quality(page_image, extracted_md, model, timeout) -> dict
    # returns: {"score": float, "gaps": [str], "action": "accept"|"patch"|"fallback"}
```

`layout_hints()` removed in Session 8 — heading detection inside `full_page_extract()` directly (D23 update).

### Image handling
- All images resized to long edge ≤ `MAX_IMAGE_PX` (1024px) before encoding as PNG
- Judge images capped at `JUDGE_MAX_IMAGE_PX` (512px) — reduces visual tokens ~4× for faster scoring
- Encoding via `_prepare_image()` — LANCZOS downsample if needed

### JSON fallback chain in judge_quality (D34)
If vision model returns non-JSON, strips markdown fences and runs through:
1. `json.loads()` — strict parse
2. Regex `"score": X.X` — extracts score from free-form text
3. Regex `X/10` pattern — last numeric fallback
4. Neutral 5.0 — never crashes the loop

Returns `{"score": 5.0, "gaps": [], "action": "patch"}` as final safety net.

### Hallucination filter (Gap F, Session 18)
`_HALLUCINATION_RE` detects VLM meta-commentary ("It seems the description...", "I cannot see...") that is not document content. `_strip_hallucination()` returns `""` for matching responses — prevents garbage from entering the markdown.

### Exceptions
```python
VisionTimeoutError   # daemon thread didn't complete within timeout
VisionCallError      # Ollama returned an error (e.g. insufficient RAM)
```

---

## 3 · quality/quality_judge.py ✅ done — structural fidelity + heuristic judge (D31, D33)

**Purpose:** Typed scoring layer on top of `vision_tools.judge_quality()`. Combined score = 0.7 × content_score + 0.3 × structure_score (D31). Per-page routing: vision judge for image/scanned pages, heuristic for text-only pages (D33).

### Key functions
```python
judge(page_image, extracted_md, round_num, model, timeout) -> JudgeResult
heuristic_judge(page_num, extracted_md, page_text) -> PageScore    # D33 — no vision call
aggregate_page_results(results: list[JudgeResult]) -> (avg_score, all_gaps, action)
```

### heuristic_judge (D33)
Used for `text_rich` and `table_heavy` pages with `needs_vision=False`. Computes word-overlap ratio between pdfplumber text and extracted markdown, adds structure bonus for heading/table presence. No model call — microseconds. Prevents 2000s+ judge phases on text-only documents.

### PageScore
```python
@dataclass
class PageScore:
    page_num: int
    score: float        # 0.0 – 10.0 (combined 0.7×content + 0.3×structure)
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

**Purpose:** VRAM-aware model selection. Enforces phase-based load/unload rules. D37: conditional phase boundary.

### Key functions
```python
reset()                      # called at start of each PDF
get_vision_model() -> str    # returns sticky model or VISION_PRIMARY
mark_success(model)          # sets sticky model after a successful call
before_vision_phase()        # phase boundary: unloads qwen3.6 when fallback is sticky
before_orchestrator_phase()  # phase boundary: unloads fallback when sticky, resets sticky→None
teardown_pdf()               # unloads vision model, resets sticky
set_parse_plan(plan)         # stores ParsePlan for model_tier routing (D28)
loaded_models() -> list[str] # GET /api/ps
unload(model)                # POST keep_alive=0
```

### D37 — conditional phase boundary
`before_orchestrator_phase()` is only called when `needs_fmt=True`. When FORMAT is skipped, vision model stays loaded and judge reuses it immediately — no cold reload (30–60s saved per skipped-format round).

### Call sequence in parser_agent
```
parse() start:            model_router.reset()
_probe_vision():          mark_success(winner)          ← sets sticky for whole PDF

Round 1:
  VISION PHASE:           before_vision_phase()
  _extract_docling_page() or _extract_*_page(): get_vision_model() (FigureItems only or image_heavy/mixed)
  quality_judge.judge():  get_vision_model() / heuristic_judge()
  ORCHESTRATOR PHASE:     before_orchestrator_phase() [only if needs_fmt]
  _run_format_session():  qwen3.6:27b auto-loads

Rounds 2+:
  VISION PHASE:           before_vision_phase()
  quality_judge.judge():  get_vision_model() / heuristic_judge()
  ORCHESTRATOR PHASE:     before_orchestrator_phase()
  _run_patch_loop():      qwen3.6:27b
```

---

## 5 · orchestration/context_manager.py ✅ done

**Purpose:** Compress agent message history to stay under `CONTEXT_TOKEN_LIMIT` (8K tokens).

### Key functions
```python
estimate_tokens(messages) -> int     # rough: total chars / 4
compress_history(messages, token_limit, model) -> list[dict]
    # keeps: messages[0] (system prompt) + last 4 messages
    # summarises: everything between via qwen3.6:27b
summarise_messages(messages, model) -> str  # 200-word summary call
```

### Compression trigger
Only compresses if `estimate_tokens(messages) > CONTEXT_TOKEN_LIMIT`. Short conversations pass through unchanged.

### Safety
If summarise call times out → returns `"[Summary unavailable — timeout]"` and continues.

---

## 6 · orchestration/parser_agent.py ✅ done — 9-phase pipeline

**Purpose:** Orchestrator. Runs the full 9-phase pipeline. CLI entry point.

### Entry point
```python
from cloak.orchestration.parser_agent import parse
parse(pdf_path: Path, deep_review: bool = True)
```

### 9-phase pipeline (D14 + D19 + D20 + D28 + D29 + D30 + D33 + D35 + D37)
```
Phase 0  intake:      load_pages(), create output dir, create images_dir
Phase 1  intelligence: run_docling_pass() → DoclingPageMap
                        heuristic profile_all(pages) → RouteMap
                        update_vision_from_docling() — refine needs_vision per page (D29)
                        build_doc_profile() → DocProfile (formula_count, vision_dependency, …)
                        build_parse_plan() → ParsePlan (model_tier, max_rounds, use_math_ocr, …)
Phase 2  staging:     probe vision based on ParsePlan.model_tier
Phase 3  extract:     _extract_by_route() dispatcher:
                        if docling available + not scanned → _extract_docling_page() (D29)
                          SectionHeaderItem → ##/###/#### heading
                          TextItem          → pdfplumber chars
                          TableItem         → pdfplumber (simple) or docling (complex)
                          FigureItem        → bbox crop → vision.region_describe() + caption
                          FootnoteItem      → collected, appended at section end
                          FormulaItem       → pix2tex bbox crop → $$latex$$ (D35); fallback `text`
                          Gap A: empty docling → pdfplumber _extract_text_page() fallback
                          Gap C: garbled glyphs → reroute to vision extraction
                        scanned → _extract_scanned_page() → surya OCR → tesseract fallback (D30)
                        image_heavy (no docling) → _extract_vision_page()
                        mixed (no docling) → _extract_mixed_page()
Phase 4  format:      qwen3.6:27b FORMAT once (D20) — /no_think; FORMAT_NUM_CTX=32768; content-loss guard
Phase 5  judge:       per-page routing: text_rich/no-vision → heuristic_judge (D33);
                        image/mixed/scanned → vision judge (sampled per ParsePlan.judge_sample_rate)
                        carryover: pages ≥ JUDGE_SKIP_THRESHOLD skip re-judge (D31)
Phase 6  patch:       qwen3.6:27b PATCH — targeted gaps; content-loss guard (D5)
          Phases 5–6 repeat up to ParsePlan.max_rounds; best round wins (D2); stop at ≥ 8.0 (D3)
Phase 8  output:      final.md + confidence_report.md + flagged.md + images/
          model_router.teardown_pdf()
Phase 9  deep review: gemma4:latest compares pdfplumber text vs final.md → review.md (D27)
```

### Extraction functions
```python
_extract_by_route(pages, route_map, vision_available, on_page_done, images_dir, element_map, use_math_ocr) -> str
_extract_docling_page(elements, pg, vision_available, model, images_dir, use_math_ocr) -> str  # D29 + D35
_extract_text_page_vision(pg, model, images_dir) -> str    # image_heavy/mixed without docling
_extract_text_page(pg) -> str                              # pdfplumber fallback (no vision)
_extract_table_page(pg) -> str                             # pdfplumber tables
_extract_scanned_page(pg) -> str                           # surya → tesseract fallback (D30)
_extract_mixed_page(pg, model, images_dir) -> str          # text + region vision (no docling)
_extract_vision_page(pg, model, images_dir) -> str         # image_heavy full-page (no docling)
_crop_normalized(image, bbox_norm) -> PIL.Image | None     # crop by normalised bbox
```

### Artifact cleanup
`_clean_output_artifacts()` runs after extraction:
- Gap D: `re.sub(r"^\.$", "", text, flags=re.MULTILINE)` — removes TOC leader dots
- `<math>` watermark: filters `<math display="block">\mathsf{Digitized}\,\mathsf{by}\,Google</math>` artifacts

### Output
```
data/markdown/{specialty}/{stem}.md
data/markdown/{specialty}/{stem}_confidence.md
data/markdown/{specialty}/{stem}_review.md    ← Phase 9 (if deep_review=True)
data/markdown/{specialty}/{stem}_flagged.md   ← pages < LOW_CONFIDENCE_THRESHOLD (5.0)
data/markdown/{specialty}/{stem}_images/      ← figure/region crops
  page_0_figure_0.png
  page_3_figure_1.png
  ...
```

### Tool-calling tools (qwen3.6:27b patch loop)
| Tool | Purpose |
|---|---|
| `get_page_text(page_num)` | Return sorted text for a page |
| `get_region_description(page_num, region_index)` | Vision describe a region crop |
| `patch_section(heading, content)` | Replace a section in current markdown |
| `add_section(heading, content)` | Append a new section |
| `finish(markdown)` | Signal patch complete, return final markdown |

---

## 7 · profiling/page_profiler.py ✅ done

**Purpose:** Classify each PDF page heuristically (zero models) and produce a RouteMap. When docling is installed, `needs_vision` is refined via `update_vision_from_docling()`. See [[docs/DECISIONS.md]] §D21 §D29.

### Key functions
```python
profile_page(page: PageData) -> PageProfile
profile_all(pages: list[PageData]) -> list[PageProfile]
build_route_map(profiles: list[PageProfile]) -> RouteMap
summarise(profiles: list[PageProfile]) -> dict[str, int]   # count by type
update_vision_from_docling(profiles, element_map) -> None  # D29 — refine needs_vision
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
    needs_vision: bool        # refined by update_vision_from_docling after docling pass
```

### Classification rules (priority order)
| Condition | Type |
|---|---|
| `text_length < 100 AND image_area_ratio > 0.4` | `scanned` |
| `image_area_ratio > 0.5 AND text_length < 500` | `image_heavy` |
| `table_count >= 2` | `table_heavy` |
| `image_area_ratio > 0.2 AND text_length >= 100` | `mixed` |
| everything else | `text_rich` |

### update_vision_from_docling
After docling pass, sets `needs_vision=True` only for pages that have actual `picture` elements. Prevents vision being called for heading-only or text-rich pages where docling already handles structure.

---

## 8 · extraction/ocr_tools.py ✅ done — Surya primary + Tesseract fallback (D30)

**Purpose:** OCR for scanned pages. Primary: Surya (GPU-accelerated, reading-order-aware). Fallback: Tesseract. Called by `parser_agent` for `RouteMap[page_num] == "scanned"`. See [[docs/DECISIONS.md]] §D22 §D30.

### Key functions
```python
ocr_page(image: PIL.Image, lang: str = "eng") -> str
    # Dispatches to Surya (when OCR_PRIMARY=="surya" + GPU) or Tesseract
    # Returns clean text string; raises OCRError on binary not found

clean_ocr_text(raw: str) -> str
    # remove page numbers, repeated headers, fix hyphenation
    # normalise whitespace and line endings
```

### Surya API (D30 — Session 18 fix)
```python
_load_surya() -> tuple[RecognitionPredictor, DetectionPredictor]:
    _surya_foundation = FoundationPredictor()       # must be created first
    _surya_det = DetectionPredictor()
    _surya_rec = RecognitionPredictor(_surya_foundation)  # pass foundation as arg
```

`RecognitionPredictor` changed API in newer surya versions — requires `FoundationPredictor` as first positional arg. `transformers>=5.0` breaks `SuryaDecoderConfig.pad_token_id` access → pinned `transformers>=4.56.1,<5.0` in `pyproject.toml`.

### Image preprocessing (before OCR)
```python
_preprocess(image: PIL.Image) -> PIL.Image:
    # 1. convert to grayscale
    # 2. ImageFilter.SHARPEN (mild)
    # 3. resize to min 2000px long edge for OCR resolution
```

### Exception handling
```python
OCRError  # raised if binary not found or call fails
```
On `OCRError` → caller falls back to raw PyMuPDF text blocks.

### Config
```python
OCR_PRIMARY  = "surya"     # primary OCR engine (D30)
OCR_FALLBACK = "tesseract" # fallback when surya unavailable or GPU absent
OCR_LANG     = "eng"       # Tesseract language code
```

---

## 9 · cli/system_check.py ✅ done

**Purpose:** Hardware probe + VRAM-aware model suitability display at startup. Startup memory cleanup. RAM gate before parsing begins.

### Key functions
```python
get_free_ram_gb() -> float
get_total_ram_gb() -> float
get_free_vram_gb() -> float
get_total_vram_gb() -> float
get_gpu_name() -> str
is_ollama_running() -> bool
get_installed_models() -> list[str]
check_model_suitability(model, free_ram_gb, free_vram_gb=0.0) -> dict
show_startup_screen(show_commands=False) -> None
run_startup_cleanup() -> None
ram_gate(min_gb=MIN_FREE_RAM_GB) -> bool
get_top_processes(n=6, min_mb=250) -> list[dict]
```

### Startup screen visibility (D17)
`show_startup_screen(show_commands=True)` — bare `cloak` only.
`show_startup_screen()` — `cloak status` only.
NOT called on `cloak parse` or `cloak list`.

---

## 10 · cli/main.py ✅ done

**Purpose:** typer CLI entry point.

### Commands
```
cloak                    → run_startup_cleanup() + show_startup_screen(show_commands=True)
cloak parse <pdf|dir>    → parse PDF(s); no startup screen; supports --no-review, --dry-run
cloak status             → run_startup_cleanup() + show_startup_screen()
cloak list               → table of data/markdown/ contents with score + status (registry)
cloak clean              → remove all data/markdown/ output (confirmation prompt)
cloak clean --yes        → clean without confirmation
```

---

## 11 · quality/deep_review.py ✅ done — Phase 9

**Purpose:** Post-pipeline deep quality review. Loads `gemma4:latest` after all pipeline models are unloaded, compares raw pdfplumber text vs final markdown, writes actionable quality improvement report. See [[docs/DECISIONS.md]] §D27.

### Entry point
```python
rev_path = dr.run(pdf_path, pages, final_markdown, review_out, console) -> Path | None
```

### Config
```python
DEEP_REVIEW_MODEL   = "gemma4:latest"
DEEP_REVIEW_TIMEOUT = 1200   # 20 min — bumped from 600s in Session 18 for gemma4:26b headroom
DEEP_REVIEW_NUM_CTX = 8192
```

### Report sections
Missing Content · Wrong/Missing Headings · Table Issues · Duplicate Content · Formatting Problems · Overall Assessment · Quality Score (0–10) · Priority Fixes

---

## 12 · profiling/doc_profiler.py ✅ done (D28, D35 update)

**Purpose:** Aggregate page profiles into `DocProfile`, generate `ParsePlan`. Runs after page_profiler, before any model load.

### Key functions
```python
build_doc_profile(page_profiles: list[PageProfile], element_map: DoclingPageMap | None) -> DocProfile
build_parse_plan(doc_profile: DocProfile, primary_viable: bool, use_docling: bool) -> ParsePlan
run_docling_pass(pdf_path: Path) -> DoclingPageMap | None
```

### DocProfile
```python
@dataclass
class DocProfile:
    page_count:        int
    type_distribution: dict[str, float]  # fraction per page type
    vision_dependency: str               # "none" | "low" | "medium" | "high"
    complexity_score:  float             # 0.0–1.0
    size_tier:         str               # "small"(<50) | "medium"(50–200) | "large"(200–500) | "huge"(>500)
    formula_count:     int               # D35: total FormulaItem elements across all pages
```

### ParsePlan
```python
@dataclass
class ParsePlan:
    model_tier:        str          # "none" | "fallback" | "primary"
    max_rounds:        int          # adaptive from size_tier ± complexity_score
    judge_sample_rate: float        # 0.1–1.0 — fraction of pages judged per round
    use_docling:       bool         # True when DoclingPageMap available
    use_math_ocr:      bool         # D35: True when formula_count ≥ MATH_FORMULA_THRESHOLD and pix2tex available
    math_ocr_engine:   str          # D35: "pix2tex" | "none"
```

### Adaptive round budget
| size_tier | base rounds | judge_sample_rate |
|---|---|---|
| small | 4 | 1.0 |
| medium | 3 | 0.6 |
| large | 2 | 0.3 |
| huge | 1 | 0.1 |

`complexity_score > 0.6` → +1 round; `< 0.3` → −1 round (min 1).

### run_docling_pass
Runs docling layout analysis with `do_ocr=False, device=cpu`. Sorts each page's elements by `(bbox_norm.y, bbox_norm.x)` for visual reading order (D36). Returns `None` on failure — pipeline falls back to heuristic profiles.

Discards `PageHeader` / `PageFooter` elements (D29). Retains: `section_header`, `text`, `table`, `picture`, `footnote`, `formula`, `list_item`, `caption`, etc.

---

## 13 · extraction/math_ocr.py ✅ done (D35, Session 19)

**Purpose:** pix2tex wrapper for LaTeX OCR on FormulaItem bbox crops. Called from `_extract_docling_page()` when `ParsePlan.use_math_ocr=True`.

### Public API
```python
is_pix2tex_available() -> bool          # checks pix2tex import; cached
pix2tex_equation(image: PIL.Image) -> str  # run LatexOCR on crop; returns LaTeX or ""
unload_model() -> None                  # release LatexOCR from memory
```

### Load behaviour
Singleton: `LatexOCR` model loaded on first call to `pix2tex_equation()`, cached for the session. Downloads ~100 MB on first use. Silent fail on `ImportError` — pipeline continues using docling text fallback.

### Activation condition
`ParsePlan.use_math_ocr = True` when:
1. `DocProfile.formula_count >= MATH_FORMULA_THRESHOLD` (default 3) AND
2. `is_pix2tex_available() == True`

### Output format
- pix2tex returns LaTeX → emitted as `$$\n{latex}\n$$` display block
- pix2tex returns empty → fall back to `` `{docling_text}` `` inline code

### Config
```python
MATH_OCR_ENGINE        = "pix2tex"   # engine identifier
MATH_OCR_TIMEOUT       = 30          # seconds per equation crop
MATH_FORMULA_THRESHOLD = 3           # min FormulaItem count to activate math OCR
```

### Dependencies
- `pix2tex>=0.1.4` (in pyproject.toml)
- `torch`, `timm`, `x-transformers`, `albumentations` (installed as pix2tex deps)
- Not using Ollama — pure Python inference

---

## 14 · cloak/registry.py ✅ done

**Purpose:** Lightweight document registry. Tracks all parsed PDFs with scores, timestamps, and status. Backed by `data/registry.json`.

Used by `cloak list` to display parsed document inventory with quality scores and confidence flags.

---

## Planned

### D38 · Slide deck per-slide VLM mode (planned)

For PDFs where `image_heavy` fraction > 70% AND content is consistent with slide decks (1–3 figures per page, minimal pdfplumber text): render each slide as a full-page image and send to vision model with a slide-description prompt. Current approach tries to extract pdfplumber text (near-empty on slides) and falls back to prose descriptions — poor structure, no slide titles captured correctly.

Expected impact: slide deck score 6.2 → ~8.5.
