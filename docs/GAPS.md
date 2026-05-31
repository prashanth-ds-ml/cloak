---
type: gap-analysis
updated: 2026-05-31 (Session 26)
---

# Gap Analysis & Improvement Roadmap — cloak

> Where cloak falls short today, why, and what can be done about it.
> Related: [[PROGRESS.md]] · [[DECISIONS.md]] · [[BENCHMARK.md]]

---

## Benchmark baseline (Session 22/23 — gemma4:26b stack)

| # | Doc type | Score | Gap |
|---|---|---|---|
| 1 | Research paper (BERT) | 8.4 | Two-column layout occasionally scrambles |
| 2 | Medical guideline (STEMI) | 6.2 | Dense single-page poster — vision ceiling |
| 3 | Legal (SCOTUS) | 9.2 | — |
| 4 | Financial (Berkshire) | 9.1 | — |
| 5 | Technical manual (PostgreSQL) | 8.7 | — |
| 6 | Bilingual legal (ECHR) | 9.9 | — |
| 7 | Table-heavy (NHANES codebook) | 9.6 | — |
| 8 | IRS Pub 17 (tax tables) | 7.9 | Merged/complex table cells |
| 9 | Invoice | 6.5* | Was 4b timeout; gemma4:26b expected 9.0+ |
| 10 | Slide deck (MIT OCW) | 8.0 | Sparse slides with no text layer |
| 11 | NASA ESTO (image-heavy) | 6.6* | Was 4b timeout; gemma4:26b expected 8.0+ |
| 12 | Medical poster | 6.2 | Single dense page; vision one-shot insufficient |
| 13 | ArXiv multi-column | 7.6 | Citations, complex 2-col interleave |
| 14 | Engineering textbook | 8.3 | Bitmap math not in FormulaItems |
| 15 | JEE Advanced 2023 | 9.1 | — |
| 16 | GATE CS 2024 | **8.2*** | Fixed from 5.9; 4 pages stalled mid-gen |
| 17 | GATE EE 2024 | 5.9* | Circuit diagrams; expect improvement with gemma4:26b |
| 18 | ESE EE 2024 (scanned) | 6.2 | Fully scanned; OCR ceiling on circuit diagrams |
| 19 | Scanned (Dumfries 1800s) | 6.2 | Honest OCR ceiling for 1800s print quality |

`*` = score from Session 22 with 4b fallback model; gemma4:26b expected to recover these.
GATE CS 8.2 confirmed with gemma4:26b today (Session 23).

---

## Session 26 gaps (priority for Session 27)

### G_J1 — Judge JSON failures — qwen3-vl:8b returns prose not JSON *(High — blocks quality loop)*

**What:** Every parse ends with `JSON parse failed — model response was not valid JSON` in the confidence report. The L4 judge score falls back to 6.2 (neutral regex extraction) on all three tested docs. The quality loop is effectively blind — it cannot tell good extraction from bad.

**Why:** `qwen3-vl:8b` does not reliably follow the `{"score": ..., "gaps": [...], "action": "..."}` format constraint when processing images. The old gemma4:26b responded to format instructions; qwen3-vl:8b needs the Ollama `format: "json"` parameter enforced at the API level.

**Fix:** Add `format: "json"` to the `ollama.chat()` call inside `_call_timed()` when `judge_quality()` is invoked. Or add a dedicated `judge_call()` wrapper that sets format.

**File:** `cloak/vision/vision_tools.py` — `judge_quality()` → `_call_timed()`

---

### G_J2 — Patch loop never makes changes *(High — quality loop rounds wasted)*

**What:** Every parse ends with `Patch produced no changes — stopping early` after Round 1. No improvement beyond the initial extraction. The judge finds gaps but the patch loop cannot fill them.

**Why:** Two possible causes: (1) qwen3:14b tool-calling not working for the patch tools — returns a finish call immediately. (2) The gaps identified are structural (column order, layout) which cannot be fixed by text patching — the content is present but in wrong order.

**Investigate:** Run a patch with verbose logging to see what qwen3:14b actually returns. Check if tool calls are being made at all.

**File:** `cloak/orchestration/parser_agent.py` — `_run_patch_loop()`

---

### G_P1 — poster_mode detection misses AF-type documents *(High — column mixing)*

**What:** `cardiology_af.pdf` has 63 docling text elements but only 33.9% docling text coverage — docling finds box labels but misses 66% of the content. poster_mode uses `< 8 elements` threshold and didn't fire. AF output has severe column mixing (SYMPTOMS / CATEGORIZE AF / PRECIPITATING FACTORS interleaved).

**Why:** Element count is the wrong signal. A flowchart with many short box labels appears to have "lots of elements" but covers very little of the pdfplumber text. Coverage ratio is the correct signal.

**Fix:** In `_detect_poster()`, add second condition: `docling_text_coverage < 0.50`. Coverage = `sum(len(e.text) for text_elements) / len(pg.text)`. AF: 33.9% < 50% → fires. Stroke: 84.7% > 50% → doesn't fire (correct).

**File:** `cloak/orchestration/parser_agent.py` — `_detect_poster()`

---

### G_H1 — Figure hallucination not caught by filter *(Medium)*

**What:** `cardiology_af.pdf` figure 1 (an ICMR logo): VLM generated *"The user's input appears to be a mix of text and code, but it seems to contain a mistake... Arabic text... correct answer is NEW DELHI"* — completely fabricated.

**Why:** The `_strip_hallucination()` regex checks for specific opener phrases but not this pattern. The VLM produced a "help me solve this problem" response instead of describing the image.

**Fix:** Extend `_HALLUCINATION_RE` in `vision_tools.py` with: `the user'?s? input`, `it seems to contain a mistake`, `the correct answer is`, `correct location should be`. Also add a length/coherence check: if response has no overlap with known document vocabulary, flag as hallucination.

**File:** `cloak/vision/vision_tools.py` — `_strip_hallucination()`

---

## Gap catalogue

### G1 — Processing artifacts in final output *(Quick fix)*

**What:** `<!-- TABLES: structured form of page content — use these, remove any duplicate prose above -->` comment appears verbatim in `final.md`. This is an internal signal to the FORMAT/PATCH agent and should never reach output.

**Why:** `_extract_docling_page()` inserts the comment inline to guide the LLM. The cleanup step in `_clean_output_artifacts()` doesn't strip it.

**Fix:** Add HTML comment stripping regex to `_clean_output_artifacts()` in `parser_agent.py`:
```python
text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
```

**Impact:** Cosmetic but necessary for production-quality output.

---

### G2 — Page header/footer pollution in exam_mode *(Quick fix)*

**What:** Every page block in exam output repeats "GATE 2024 / IISc Bengaluru / Computer Science (CS1) / Page X of 36 / Organizing Institute: IISc Bengaluru". 10 pages × 5 lines = 50 lines of noise.

**Why:** Docling discards `PageHeader`/`PageFooter` elements normally. In `exam_mode`, we bypass docling text extraction and send the full page image to `exam_page()`. The vision model extracts everything visible, including headers and footers.

**Fix:** Post-process exam_mode output with a regex filter for known exam header patterns:
```python
_EXAM_HEADER_RE = re.compile(
    r'^(?:GATE\s+\d{4}|JEE|ESE|IISc|IIT|NIT|Organizing Institute:.*|Page \d+ of \d+).*$',
    re.MULTILINE | re.IGNORECASE,
)
```
Apply in `_extract_exam_page()` before returning.

**Impact:** Reduces noise ~15% in exam paper output; cleaner markdown for downstream use.

---

### G3 — LaTeX encoding corruption *(Quick fix)*

**What:** LaTeX commands contain embedded CJK/Unicode characters: `\mathbb定`, `\frac{x}{y定}`. Happens when gemma4:26b tokenizes a LaTeX command near a unicode character boundary.

**Why:** gemma4:26b's tokenizer sometimes merges CJK tokens with preceding LaTeX tokens at high generation speed (no thinking, think=False).

**Fix:** Post-processing regex that strips non-ASCII from inside LaTeX delimiters:
```python
def _clean_latex(text: str) -> str:
    # Strip non-ASCII chars from inside $...$ and $$...$$
    text = re.sub(r'(\$\$?)(.*?)(\$\$?)', 
                  lambda m: m.group(1) + re.sub(r'[^\x00-\x7F]', '', m.group(2)) + m.group(3),
                  text, flags=re.DOTALL)
    return text
```

**Impact:** Prevents malformed LaTeX from breaking downstream renderers (MathJax, KaTeX, Pandoc).

---

### G4 — GLM-OCR fallback for exam_page failures *(Medium effort)*

**What:** When `exam_page()` fails with `VisionTimeoutError`, the current path is raw pdfplumber text. For GATE CS pages with thin text layers, this produces flat unformatted content with garbled equations.

**Why:** Pdfplumber has no understanding of mathematical notation layout. GLM-OCR (already installed, #1 on OmniDocBench) handles math+text document layouts in one pass.

**Fix:** In `_extract_exam_page()` exception handler, try GLM-OCR before falling back to pdfplumber:
```python
except VisionTimeoutError:
    try:
        from cloak.extraction.ocr_tools import _ocr_page_glm, is_glm_ocr_available
        if is_glm_ocr_available() and pg.image is not None:
            return _ocr_page_glm(pg.image)
    except Exception:
        pass
    return pg.text.strip()  # last resort
```

**Impact:** Pages 3–6 in GATE CS (which stalled on vision) would get GLM-OCR quality instead of raw pdfplumber. Expected GATE CS: 8.2 → 8.7+.

---

### G5 — Stall threshold too tight for CPU+GPU split *(FIXED in Session 23)*

**What:** `STALL_SECONDS=90` caused mid-generation stalls on pages with complex math/diagrams. gemma4:26b auto-split (36% VRAM, 64% CPU) pauses 90–120s when processing CPU-offloaded attention layers on dense content.

**Fix applied:** Raised `STALL_SECONDS` to 150s in `config.py`.

**Impact:** Pages 3–6 of GATE CS should now complete extraction. Expected further improvement on subsequent runs.

---

### G6 — Exam section hierarchy missing *(Medium effort)*

**What:** A 10-page GATE CS paper has clear structural sections (General Aptitude Q.1–Q.10, CS Theory Q.11–Q.35, CS Problems Q.36–Q.65). The output has only 1 heading (`## General Aptitude (GA)`) for the entire document.

**Why:** The `_EXAM_PROMPT` asks to "preserve question numbers as-is" but doesn't ask to infer or preserve section hierarchy. Gemma4:26b extracts question-by-question without anchoring to sections.

**Fix:** Add explicit section detection to `_EXAM_PROMPT`:
```
- Section headers (e.g. "Q.1 – Q.5 Carry ONE Mark Each", "SECTION A", "General Aptitude") → ## heading
- Sub-section markers (question ranges within sections) → ### heading
```

**Impact:** Improves navigability and structural fidelity score (30% of quality score).

---

### G7 — Figure/diagram questions not sub-extracted *(Harder)*

**What:** Questions referencing diagrams ("The circuit shown in the figure…", "The graph below shows…") only have the text captured. The figure/diagram itself is not described separately.

**Why:** `exam_page()` sends the full page as one image. The vision model extracts text but doesn't separately describe referenced figures unless specifically prompted.

**Fix options:**
- **Option A** (simpler): Detect "figure/diagram/circuit" references in extracted text → trigger `region_describe()` on the page with label="diagram"
- **Option B** (complete): In exam_mode, after `exam_page()` extraction, run a second pass: "List all figure bounding boxes on this page as JSON" → crop each → `region_describe()`

**Impact:** GATE EE (circuit problems), ESE EE (diagrams), and any exam with visual questions. Expected: +1.0–2.0 on diagram-heavy exam papers.

---

### G8 — Dense single-page docs at ceiling *(Harder)*

**What:** STEMI (6.2), medical poster (6.2) — both are single dense pages with complex multi-column layouts, decision trees, and tables mixed with text. One `full_page_extract()` call can't capture everything.

**Why:** A single vision call on a dense page overwhelms the model's output budget. Gemma4:26b generates ~1000 tokens before stopping; a dense page may have 3000 tokens of content.

**Fix — Strip extraction (D46 candidate):**
Divide dense pages into horizontal strips (top 40%, middle 40%, bottom 20%) and extract each strip independently:
```python
strips = _divide_page_into_strips(page_image, n=3)
parts = [full_page_extract(strip, model=model) for strip in strips]
return merge_strips(parts)  # deduplicate overlap, maintain order
```

**Impact:** STEMI/medical posters: expected 6.2 → 7.5–8.0.

---

### G9 — Judge hallucination / false flagging *(Structural)*

**What:** Page 5 of GATE CS was scored 2.4/10 ("completely wrong content") but the raw source text and the markdown both had the correct content. The judge hallucinated a mismatch.

**Why:** Single-call judge with no verification. The judge (gemma4:26b, think=True) sometimes confuses which page's content it's comparing, especially when pages look similar (multiple-choice questions with similar layouts).

**Fix options:**
- **Option A — Double-check flagged pages**: When `action="fallback"` and `score < 3.0`, re-run the judge once more. If second score contradicts, take the higher (benefit of doubt).
- **Option B — Chunk-level judge**: Instead of comparing the full page markdown at once, compare question-by-question (split by `Q.\d+` regex). The judge can't confuse content if it only sees one question at a time.
- **Option C — Self-consistency (3-vote)**: Run judge 3× at temperature=0.3, take modal action. Reduces single-call hallucination by ~60%.

**Impact:** Reduces false positives in `_flagged.md`. Improves judge reliability on uniform-layout documents (exam papers, forms, codebooks).

---

### G10 — IRS Pub 17 table ceiling *(Expected fix from D45)*

**What:** IRS Pub 17 scored 7.9 — complex merged-cell tax tables (multi-row headers, colspan cells, worksheet grids) that pdfplumber extracts as broken cell dumps.

**Expected fix:** GLM-OCR table extraction (D45) now wired into `_extract_docling_page()`. When `el.table_md` is short/empty, GLM-OCR crops the table bbox and extracts structured markdown. Expected: 7.9 → 9.0+.

**Status:** Wired in Session 23. Will confirm in full benchmark re-run.

---

### G11 — Scanned exam papers with circuit diagrams *(Hard ceiling)*

**What:** ESE EE 2024 (6.2) — fully scanned exam with circuit schematics, waveform diagrams, and control system block diagrams. GLM-OCR and Surya extract text but can't reconstruct circuit topology.

**Why:** Circuit diagrams are fundamentally spatial relationships (node connections, component placement). No current OCR or VLM reliably converts them to structured text (SPICE netlist, ASCII art, or even a verbal description accurate enough to solve).

**Fix — Describe don't reconstruct:** For circuit diagram regions, instead of trying to extract structured data, generate a natural language description: "Figure: Series RLC circuit with R=100Ω, L=10mH, C=1μF connected to voltage source V(t)=10sin(100t)". This is achievable with `region_describe(label="diagram")`.

**What needs to happen:**
1. Detect circuit/diagram regions on scanned pages (GLM-OCR can output image bounding boxes)
2. Route detected regions to `region_describe(label="diagram")` with gemma4:26b
3. Insert descriptions as `[Figure: ...]` blocks in markdown

**Impact:** ESE EE: 6.2 → ~7.5. Remaining gap vs LlamaParse on circuit-heavy exams: genuine — LlamaParse also can't reconstruct circuits.

---

## Improvement techniques — research-backed approaches

### T1 — Adaptive resolution scaling

**Concept:** Instead of fixed `EXAM_MAX_IMAGE_PX=1536` for all exam pages, scale based on content density:
- Sparse pages (few equations) → 1024px (fast, fewer visual tokens)
- Dense pages (many equations, small text) → 1536px or 2048px
- Detection signal: pdfplumber char density + FormulaItem count from docling

**Why it works:** gemma4:26b's variable resolution encoder (70–1120 visual tokens) produces better LaTeX on higher-res inputs. For text-only questions (Q.1 word analogy), 1024px is identical in quality to 1536px.

**Trade-off:** Higher resolution = more visual tokens = slower generation. On auto-split hardware, 2048px adds ~30–60s per page.

---

### T2 — Prompt chaining (skeleton → fill)

**Concept:** Two-pass extraction instead of one:
1. **Skeleton pass** (fast): "List all question numbers and section headers on this page as a JSON array"
2. **Fill pass** (full): For each question in skeleton, "Extract question Q.X including all equations, answer options, and figure references"

**Why it works:** LLMs reliably extract structure better in isolation. One-shot "extract everything" on a dense page causes context overload — the model prioritises visible text but misses equations, skips options, or confuses question boundaries.

**Trade-off:** 2× model calls per page. Offset by: each call is shorter and more focused → faster per call, better quality.

---

### T3 — Confidence-aware retry with different strategy

**Concept:** When a page scores < 6.0, don't just patch — retry with a different extraction strategy:

```
Score < 6.0 after round 1:
  strategy A used → try strategy B
  strategy B → try strategy C
  stop if strategy C also < 6.0 (write to flagged.md)
```

Strategies by page type:
- `exam_page` failed → try `full_page_extract` → try `_ocr_page_glm`
- `full_page_extract` failed → try strip extraction (T-shaped split)
- vision failed → try `region_describe` per detected text block

**Why it works:** The current pipeline has one extraction strategy per page type. A low score usually means the strategy was wrong for that specific page, not that the content is unextractable.

---

### T4 — Post-processing pipeline (deterministic, no model)

**Concept:** A dedicated `_postprocess(text)` function that runs before Phase 8 write on the final markdown. Purely regex/deterministic — no model calls:

1. Strip `<!-- ... -->` HTML comments (G1)
2. Strip repeated page header/footer patterns (G2)
3. Clean LaTeX encoding corruption (G3)
4. Deduplicate consecutive identical lines (page headers that repeat)
5. Normalise whitespace (multiple blank lines → single blank line)
6. Validate markdown structure (unclosed `**`, dangling `|`)

**Why it works:** These are all detectable without a model. Running them after the quality loop prevents formatting artifacts from affecting judge scoring AND cleans the final output.

**Effort:** 2 hours. Pure regex, fully testable, no regressions.

---

### T5 — Semantic chunk scoring (replace page-level scoring)

**Concept:** Instead of judging "page 5 completeness", judge "question Q.7 completeness" by matching the extracted answer options against the expected structure. For academic papers, judge "section 3.1 completeness" against the pdfplumber text for that section.

**Why it works:** Pages are an arbitrary unit — a question can span two pages, a section can span five. Scoring at the semantic unit level gives:
- More accurate gap identification ("Q.12 options C and D missing" vs "page 8 has gaps")
- Better patch targeting (agent knows exactly which question to fix)
- Fewer judge hallucinations (judging a single question is much harder to get wrong than a full page)

**Implementation path:**
- Extract semantic boundaries: `Q.\d+` regex for exams, `##/###` headings for structured docs
- Map each semantic unit to its source page(s)
- Judge per unit, not per page

---

### T6 — Parallel extraction for independent pages

**Concept:** For text_rich pages (using pdfplumber or GLM-OCR, no GPU contention), extract N pages simultaneously in threads. For vision pages, keep sequential (one GPU call at a time).

**Current state:** All pages are extracted sequentially, including text_rich ones.

**Why it works:** `pdfplumber` is CPU-only. GLM-OCR is fast (2.2 GB). For a 20-page text_rich document:
- Current: 20 × 0.1s = 2s (but blocking on the model between)
- Parallel: ~0.5s for all 20 pages

**Real impact:** Speed improvement on text-dominant documents. Not the bottleneck on image-heavy docs.

---

### T7 — Pre-flight text quality scoring

**Concept:** Before routing a page to vision, score the quality of the pdfplumber text:
```python
quality = text_quality_score(page.text)
# signals: char entropy, symbol-font ratio, garbled_ratio, coverage
if quality > 0.85:
    route = "text_direct"  # trust pdfplumber, skip vision
elif quality > 0.50:
    route = "text_with_glm_tables"  # pdfplumber text + GLM-OCR tables
else:
    route = "vision"  # text layer unreliable, must use vision
```

**Why it works:** `_is_garbled()` already detects glyph-code pollution. Extending this to a continuous quality score (entropy-based) lets the router make finer decisions, reducing unnecessary vision calls on near-clean pages.

**Impact:** Reduces vision calls by ~20–30% on typical digital PDFs. Each skipped vision call saves 60–300s.

---

### T8 — Table cell reconstruction (beyond GLM-OCR)

**Concept:** For complex tables with merged cells and colspan, after GLM-OCR extraction, validate the markdown table structure:
1. Check all rows have same column count
2. Detect merged cells (GLM-OCR outputs `--` or blank for merged)
3. Reconstruct cell boundaries using pdfplumber word positions as spatial anchor

**Why it works:** GLM-OCR returns markdown tables but doesn't always handle colspan correctly. pdfplumber has exact `(x, y)` positions for every word — using those as anchors to validate which markdown cell a word belongs to can fix merged-cell errors.

**Impact:** IRS Pub 17, NHANES codebook — fix the remaining 0.5–1.0 point gap from merged cells.

---

## Prioritised roadmap (revised — doc-type focused, D46)

### Sprint 0 — Foundation ✅ COMPLETE (Session 24)

Fix what is broken for EVERY document before doing any doc-type work. These are not features — they are correctness fixes.

| Task | What it fixes | Status |
|---|---|---|
| Phase 8.5 `postprocess.py` | G1 HTML artifacts, G2 exam headers, G3 LaTeX corruption, /think fragments, duplicate lines | ✅ done S24 |
| 4-level docling-grounded judge | Circular self-scoring, judge hallucination (G9), false positives | ✅ done S24 |
| Hallucination rate in heuristic_judge | Fabricated content currently passes word-overlap check | ✅ done S24 |
| Tests for pure functions | Every regression currently found by expensive benchmark re-run | ✅ 55/55 S24 |
| Phase 3.5 structural merge | Multi-page continuation tables currently written as two broken fragments | ⏳ Sprint 1 |
| Phase 4.5 pre-judge inventory | Quality loop starts blind — doesn't know what's missing before round 1 | ⏳ Sprint 1 |
| Phase 7 structural validation | Final completeness check before write — last chance to catch missing sections | ⏳ Sprint 1 |

---

### Sprint 1 — Doc Type 1: ICMR Standard Treatment Workflows (Sessions 25–27) 🔄 active

**Target:** 9.0+ on 8/10 ICMR documents, clinician review of 3 outputs confirms medically accurate.

**Why ICMR first:** hardest single-page layout problem (G8 dense single-page), known domain, real sensitivity requirement (healthcare — must be local), maximum room for improvement (currently 6.2).

| Task | Gap | Status |
|---|---|---|
| Build ICMR test corpus | No ground truth | ✅ done S25 — 19 docs in `data/samples/icmr_stw/` |
| Full vision extraction for ICMR posters | docling/pdfplumber can't read poster content | ✅ confirmed S25 — gemma4:26b extracts correctly, stemi 6.2→9.6 |
| `think=False` for judge | Judge timeout on dense docs (D48) | ✅ fixed S25 — `vision_tools.judge_quality()` |
| `deduplicate_sections()` in postprocess | Section-level duplicates from patch agent | ✅ fixed S25 — 60/60 tests |
| `is_ollama_available()` health check | Silent patch failure when Ollama down | ✅ fixed S25 — model_router.py |
| Verify think=False judge timing | Needs re-run confirmation | 🔄 in progress — bcum15g17 running at session end |
| Investigate dengue failure (286 chars, 5%) | Unknown — routing issue or PDF limit | ⏳ Session 26 first task |
| Strip extraction for dense pages | G8 — single vision call misses content | ⏳ Session 26 after dengue diagnosed |
| Schema-aware ICMR judge | Circular judge can't verify clinical content | ⏳ Session 26-27 |
| Decision tree representation | Flowcharts render as prose | ⏳ Session 27 |
| Domain validation | No external quality signal | ⏳ Session 27 — clinician review |

**Success gate:** 9.0+ on 8/10 ICMR docs with schema judge. Clinician says output medically usable. Only then move to Sprint 2.

---

### Sprint 2 — Doc Type 2: Exam Papers (Sessions 28–30)

**Target:** 8.5+ on JEE/GATE/ESE. Already at 8.2–9.1 — mostly polish.

| Task | Gap | Expected impact |
|---|---|---|
| GLM-OCR as exam_page fallback | G4 — vision timeout → raw pdfplumber today | GATE CS: 8.2 → 8.7+ |
| Exam section hierarchy in prompt | G6 — flat output, no ## sections | Structure score +0.5 |
| Diagram question detection + region_describe | G7 — "the circuit shown" but no figure | GATE EE/ESE EE: +1.0–2.0 |
| Document-level timeout watchdog | GATE EE ran 742 min — no guard | Prevents runaway parses |

**Note:** G2 (exam header noise) is already fixed in Sprint 0 post-processing.

**Success gate:** 8.5+ on 4/5 exam papers with independent subject-expert review of 1 paper.

---

### Sprint 3 — Doc Type 3: Research Papers (Sessions 31–32)

**Target:** 9.0+ on academic papers. Already at 8.4 — small gap.

| Task | Gap | Expected impact |
|---|---|---|
| Multi-page continuation table merger | N1 — table fragments across pages | IRS/academic: +0.5–1.0 |
| TOC validation pass | N2 — missed sections not caught | Heading completeness signal |
| Citation completeness check | N7 — in-text refs not verified vs bibliography | Academic paper integrity |

---

### Sprint 4 — Doc Type 4: Legal / Financial (Session 33)

**Target:** 9.5+ consistently. Already at 9.1–9.9 — mostly done.

| Task | Gap | Expected impact |
|---|---|---|
| Complex table cell reconstruction | T8 — colspan/merged cells | IRS Pub 17: 7.9 → 9.0+ (GLM-OCR already wired, validate) |
| Incremental output + resume | No protection for long parses | 200+ page legal docs safe to run |

---

### Sprint 5 — Doc Type 5: Scanned + Image-heavy (Sessions 34+)

| Task | Gap | Expected impact |
|---|---|---|
| OCR confidence gate | N5 — heuristic judge measures garbage vs garbage for scanned | Reliable scanned page scores |
| Circuit diagram description | G11 — region_describe on detected diagram crops | ESE EE: 6.2 → 7.5 |
| Strip extraction for image-heavy | G8 generalized | NASA ESTO: 6.6 → 8.0+ |

---

### Sprint 6 — Credibility (Session 35+)

Only run after Sprint 1–3 are done. Scores are only worth comparing externally when the judge is trustworthy.

| Task | Why |
|---|---|
| Human calibration on 20 pages | Calibrate model scores against human judgment — are self-scores accurate? |
| Marker + MinerU + Docling comparison on shared docs | External validation of cloak quality claims |
| Publish comparison table | Show where cloak wins (local, exam mode, privacy, deep review) |

---

## Where cloak already wins vs LlamaParse / Landing.ai

| Dimension | cloak | LlamaParse | Landing.ai |
|---|---|---|---|
| Privacy | ✅ 100% local — zero data leaves machine | ❌ Cloud API — all content sent to Llama Index | ❌ Cloud API |
| Exam papers (JEE/GATE/ESE) | ✅ Dedicated exam_mode prompt | ❌ No exam-specific mode | ❌ No exam-specific mode |
| Deep quality review | ✅ Phase 9 gemma4 audit with gap report | ❌ None | ❌ None |
| Cost | ✅ One-time hardware cost, zero per-page | ❌ Pay-per-page ($0.003–0.01/page) | ❌ Pay-per-page |
| Math equations | 🟡 pix2tex for FormulaItems; vision for others | ✅ Mathpix-backed (cloud) | 🟡 VLM description |
| Complex tables | 🟡 GLM-OCR (new, validating) | ✅ Mature table extraction | ✅ Excellent |
| Speed | 🟠 5–20 min/doc (local hardware bound) | ✅ < 1 min/doc (cloud GPU) | ✅ < 1 min/doc |
| Circuit diagrams | 🟠 Text description only | 🟠 Same | ✅ Specialised visual reasoning |

**cloak's defensible moat:** privacy + exam mode + zero recurring cost + Phase 9 deep audit. These are structural advantages no cloud service can match.

---

## Honest ceiling estimates

| Content type | Current | Achievable (Sprint 1–3) | Hard ceiling |
|---|---|---|---|
| Digital text (legal, financial, academic) | 8.7–9.9 | 9.5–9.9 | ~9.9 |
| Structured tables (tax, government) | 7.9 | 9.0+ (GLM-OCR) | ~9.5 |
| Research papers (2-col, math) | 7.6–8.4 | 8.5–9.0 | ~9.0 |
| Exam papers (JEE/GATE) | 8.2–9.1 | 8.7–9.3 | ~9.5 |
| Slide decks | 8.0 | 8.5 | ~9.0 |
| Dense single-page layouts | 6.2 | 7.5–8.0 (strip extraction) | ~8.5 |
| Scanned docs (clean print) | 6.2 | 7.0 (GLM-OCR primary) | ~8.0 |
| Scanned with circuit diagrams | 6.2 | 7.5 (diagram description) | ~8.0 |
| Image-heavy annual reports | 6.6 | 8.0 (gemma4:26b speed) | ~8.5 |
