# Two-Pass Profiler — Design Document

> Agreed design before implementation. No code until this is reviewed.
> Session 30 · Status: design approved, not yet built.

---

## The problem

The current profiler (Pass 1: pdfplumber + docling + GLM-OCR) is fast and accurate for ~70% of ICMR STW documents. For the remaining 30%, it has specific blind spots:

1. **Column count** — gap-based heuristic fails for complex multi-column layouts (~30% wrong)
2. **Picture section type** — we know WHERE content gaps are (picture bboxes) but not WHAT type (flowchart / table / diagram / logo)
3. **Reading order** — when GLM-OCR fails (GGML error), the ordered text is missing

We cannot fix these with more heuristics. These require visual understanding.

---

## The design — two passes, one judge

```
PASS 1 — Fast profiling (current profiler, no VLM)
  Tools: pdfplumber + docling + GLM-OCR
  Output: DocumentProfile + uncertainty tags
  Time: ~10-20s per doc
  Runs on: all documents

  ↓ tags uncertain cases

PASS 2 — Targeted VLM profiling (fills the gaps)
  Model: qwen3-vl:4b  (3.3 GB, full GPU, fast)
  Input: page image (512px) or picture section crop
  Questions: simple one-word classification
  Time: ~5-15s per tagged doc
  Runs on: only TAGGED documents (~30-50 of 158)
  Load once, process all tagged docs, unload

  ↓ complete profile

JUDGE — Evaluates the complete profile
  Model: qwen2.5vl:7b  (6 GB, full GPU)
  Input: full page image + complete profile output
  Questions: is each profiler claim correct?
  Time: ~15-30s per doc
  Runs on: the 10-15 selected validation docs only
  Load once, process all validation docs, unload

  ↓ judgment log

HUMAN VALIDATION — You cross-check
  Input: annotated page image + profiler claims + judge verdict
  Time: ~2-3 min per doc
  Output: ground truth labels for 10-15 docs
```

---

## Why these two VLMs

### Pass 2 — qwen3-vl:4b (actor)

Filling profiling gaps requires:
- Fast inference (we may process 30-50 docs)
- Reliable for simple one-word answers
- Light enough to not compete with GLM-OCR (2.2 GB) if needed

qwen3-vl:4b: 3.3 GB, full GPU, already installed. Handles simple visual classification ("how many columns?", "what type?") well. Does not need to be the most capable model for this role.

### Judge — qwen2.5vl:7b (evaluator)

Evaluating a complete profile requires:
- Higher quality document reasoning
- Specifically trained on DocVQA (answering questions about document images — exactly the judge's job)
- More reliable than qwen3-vl for structured document evaluation

qwen2.5vl:7b: 6 GB, full GPU, already installed. Specifically trained on DocVQA benchmarks. Was the primary vision model in sessions 8–15 and proven reliable. qwen3-vl series shifted toward general vision; qwen2.5vl is better for document-specific structured questions.

### Memory sequence — clean, no VRAM conflict

```
Pass 1:   GLM-OCR (2.2 GB) throughout
Pass 2:   load qwen3-vl:4b (3.3 GB) → fill gaps → unload
Judge:    load qwen2.5vl:7b (6 GB)   → evaluate → unload

Peak VRAM at any point:
  Pass 2: 3.3 GB (qwen3-vl:4b) + 2.2 GB (glm-ocr) = 5.5 GB ✓
  Judge:  6.0 GB (qwen2.5vl:7b) alone                       ✓
```

Never loaded simultaneously. Clean sequential lifecycle.

---

## Tagging criteria — what triggers Pass 2

The profiler tags a document when it cannot be confident about a profiling signal.

### Tag: `needs_vlm_columns`

Triggers when column detection is unreliable:
- Fewer than 4 anchored headers found (too sparse for gap algorithm)
- Column count was determined by the 10% fallback path (less confident)
- Docling coverage < 40% (not enough structure to detect columns from headers)

Pass 2 question: **"How many main content columns does this page have? Answer: 1, 2, 3, 4, or 5."**
Image: full page at 512px long-edge.

### Tag: `needs_vlm_picture_type`

Triggers for every large picture section (area > 10% of page):
- We always know WHERE the content gap is (bbox from docling)
- We never know WHAT TYPE it is without visual inspection
- Type drives the extraction strategy

Pass 2 question: **"What type of content is in this image region? Answer: flowchart, table, diagram, or logo."**
Image: crop of the picture bbox at 512px.

### Tag: `needs_vlm_ordering`

Triggers when reading order is missing:
- GLM-OCR failed (glm_chars == 0, GGML error)
- AND docling coverage < 40%
- AND picture_text_chars > 2000 (content IS there via pdfplumber)

Pass 2 question: **"What is the correct top-to-bottom reading order of the main sections on this page? List the section headings you see from top to bottom."**
Image: full page at 512px.

---

## Judge evaluation criteria

The judge (qwen2.5vl:7b) looks at each validation doc and evaluates three claims:

### Claim 1 — Column count
- Input: full page image + "profiler says N columns, with boundaries at X%"
- Question: "Looking at this document image, how many main content columns do you see? Is the profiler's count correct?"
- Output: agreed count + correct/incorrect verdict

### Claim 2 — Picture section type
- Input: picture section crop image + "profiler says this is [type based on pdfplumber content]"
- Question: "What type of content is in this image? flowchart / table / diagram / logo. Is the profiler's classification correct?"
- Output: type classification + correct/incorrect verdict

### Claim 3 — Strategy routing
- Input: full page image + "profiler routed this as text_mode / hybrid / poster_mode"
- Context: text_mode = mostly structured text, hybrid = mixed, poster_mode = mostly visual flowchart
- Question: "Looking at this page, is the profiler's strategy routing correct?"
- Output: correct/incorrect + brief reason

---

## The 10-15 validation documents

Chosen to cover every profiler failure mode:

| # | Document | Why selected |
|---|---|---|
| 1 | cardiology_af | 3-column target — main fix case |
| 2 | paediatrics_dengue | 2.9% coverage — extreme poster_mode |
| 3 | neurology_stroke | Column disagreement (profiler says 3, should be 2) |
| 4 | cardiology_stemi | 29% coverage + drugs table content gap |
| 5 | neurology_headache | 7.9% coverage + GGML failed |
| 6 | cardiology_nstemi | Known hallucination case |
| 7 | neonatology_sepsis | 65% coverage, hybrid with picture section |
| 8 | neurology_acute_paralysis | 5% coverage, poster_mode, GLM-OCR worked |
| 9 | psychiatry_depression | 84% coverage — should be clean pass |
| 10 | neurology_neuroinfections | Corrupt PDF (docling failed, GLM-OCR worked) |
| 11 | ortho_low_back_pain | Hybrid, GGML failed, pdfplumber has content |
| 12 | cardiology_bradyarrhythmia | Dense headers, column detection confusing |
| 13 | neurology_epilepsy | 3-column doc profiler called 4 |
| 14 | cardiology_heart_failure | 93% coverage — easiest case, should pass cleanly |
| 15 | psychiatry_psychosis | 1.6% coverage + GLM-OCR worked |

---

## Log output per document

Each validation document produces one self-contained log:

### 1. Annotated page image (PNG)
Rendered page with overlaid:
- Vertical dashed lines at detected column boundaries
- Colored boxes around each picture section (red border)
- Text labels: column count, strategy, picture types

### 2. Side-by-side claims table
```
                    PASS 1 PROFILER     PASS 2 VLM        JUDGE
Columns:            3                   3                  3 (agrees)
Strategy:           hybrid              —                  correct
Picture section 1:  y=44-73% 30%       flowchart          correct (high conf)
Picture section 2:  y=74-92% 16%       flowchart          correct (med conf)
GLM-OCR sections:   16 found           —                  not re-evaluated
Hallucination:      no                 —                  agrees
```

### 3. Verdict form (you fill in)
```
YOUR CHECK — open the PDF and answer:

  Column count you see:        ___
  Picture section 1 type:      ___  (flowchart / table / diagram / logo)
  Picture section 2 type:      ___  (if present)
  Strategy feels correct:      yes / no / partially

  Notes: _________________________________

  VERDICT:
    [ ] Full agreement — profiler + judge + your check all match
    [ ] Minor error    — one thing slightly off
    [ ] Major error    — something important is wrong
```

---

## How you cross-check — the exact process

Open two windows side by side:
- **Left:** the annotated PNG for that document (shows boxes drawn on the page)
- **Right:** the actual PDF

For each document (2–3 minutes):

1. **Column count (10 sec):** Count the main content columns in the PDF. Write it in the verdict form. Compare to profiler + judge.

2. **Picture sections (60 sec):** Find the red boxes on the annotated image. Locate them in the PDF. What's actually inside?
   - Boxes + arrows → flowchart
   - Rows + columns → table
   - Drawing/illustration → diagram
   - Institution logo → logo

3. **Strategy (30 sec):** Does the doc feel like structured text (text_mode), a mix (hybrid), or a visual flowchart (poster_mode)?

4. **Verdict (10 sec):** Full agreement / Minor error / Major error.

---

## What you build with this exercise

After 10-15 docs, you have a verified ground truth:

```json
{
  "cardiology_af": {
    "columns": 3,
    "picture_types": ["flowchart", "flowchart"],
    "strategy": "hybrid",
    "profiler_correct": true,
    "judge_correct": true,
    "notes": ""
  }
}
```

This becomes the **eval set** — run any profiler change against these 15 docs and know immediately if it improved or regressed.

---

## What we learn from the 15-doc validation

| Pattern | Meaning |
|---|---|
| Profiler + judge agree, you agree | Both working — trust this signal |
| Profiler wrong, judge caught it | Two-pass approach adds value |
| Both profiler + judge wrong | Systematic blind spot — most valuable finding |
| Judge wrong, profiler right | Judge model unreliable for that question type |

---

## Implementation sequence (when ready to build)

1. Update `DocumentProfile` dataclass — add `needs_vlm_columns`, `needs_vlm_picture_type`, `needs_vlm_ordering` tag fields
2. Add tagging logic to `profile()` function in `profiler.py`
3. Build `pass2_vlm_profile(pdf_path, tags, model="qwen3-vl:4b")` — processes tagged docs
4. Build `judge_profile(pdf_path, profile, model="qwen2.5vl:7b")` — evaluates complete profile
5. Build `generate_validation_report(prof, pass2, judgment)` — produces annotated PNG + log
6. Wire into `main()` with `--validate` flag for the 10-15 doc run
