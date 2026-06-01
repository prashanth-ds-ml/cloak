# cloak — Retrospective (Sessions 1–28)

> What cloak was meant to be, every major decision, what worked, what went wrong, and the right path forward.
> Written: Session 29

---

## What cloak is (the original intent)

**General-purpose, local-only PDF → structured Markdown converter.**
No data leaves the machine. Works on any document type. Produces clean, structured Markdown suitable for RAG, knowledge bases, or human reading.

Target quality bar: 9.0+ on research papers, clinical documents, legal texts, textbooks.
Hard constraint: fully local — no cloud API calls, no Ollama telemetry, no external services.

---

## The 28-session journey in one table

| Sessions | What we built | Key outcome |
|---|---|---|
| 1–8 | 9-phase pipeline, CLI, basic models | Working system, qwen3:8b + qwen2.5vl:7b |
| 9–12 | Docling, Surya, DocProfile, ParsePlan | Smart routing, structural extraction |
| 13–17 | Bug fixes, heuristic judge, JSON fallback, reading order | 12 bugs fixed, 8.0+ on most doc types |
| 18–20 | Math OCR, slide mode, exam mode | Feature additions on top of foundation |
| 21–23 | 19-PDF benchmark, gemma4:26b stack, GLM-OCR | Model switch, score stabilised |
| 24 | Strategy pivot to ICMR focus | Realised foundation was broken |
| 25–26 | ICMR corpus, poster_mode, model split | Dengue: 5% → 97% completeness |
| 27–28 | D52: GLM-OCR ground truth, heuristic judge | Dengue 9.2/10, instant judge |

---

## What worked well

### 1. GLM-OCR as ground truth (D52) — the biggest win of the project
Running GLM-OCR on every page in Phase 1 gives accurate text for free. The heuristic judge that compares extracted markdown against GLM-OCR text is instant, calibrated, and doesn't require a VLM. This solved the 28-session problem of "how do we judge quality without a circular self-assessment."

### 2. Docling for structure
Docling reliably extracts element types (headings, tables, figures, formulas), heading hierarchy, and bounding boxes. The ElementInventory from docling is the right foundation for the judge checklist.

### 3. Heuristic judge (word recall + element coverage)
Instant. No model. Calibrated to real quality. 9.2/10 on dengue reflects actual quality, not model self-flattery.

### 4. poster_mode detection and extraction
Signal B (docling coverage < 50%) correctly identifies clinical flowchart posters. VLM with `_POSTER_PROMPT` dramatically improved ICMR STW quality when it works.

### 5. Two-model split with confirmed unload (D49, D50)
Proper VRAM management. Phase boundaries are explicit. No memory competition.

### 6. Cold stall detection
`cold_timeout=600s` aborts VLM batch-mode stalls in 10 min instead of 30 min.

### 7. postprocess.py Phase 8.5
Artifact stripping (HTML comments, exam headers, LaTeX corruption, think fragments) runs reliably on every output.

---

## What went wrong — the honest retrospective

### Problem 1: The judge was never independent (Sessions 1–24)
**The core mistake of the entire project.**

For 24 sessions, the same model that extracted the content also judged it. gemma4:26b extracted the markdown AND scored it 8.4/10. A model cannot reliably critique its own output. Every benchmark score from Sessions 1–23 is untrustworthy because of this.

The fix (D52, GLM-OCR ground truth) only happened in Session 28.

### Problem 2: The patch loop never worked (Sessions 1–28)
"Patch produced no changes — stopping early" has appeared in the logs of almost every parse across 28 sessions. The tool-calling loop with qwen3:8b, qwen3.6:27b, gemma4:26b, qwen3:14b — none of them could reliably fill gaps via text patching.

Why: the gaps are structural (wrong column order, missing sections), not textual. You cannot fix spatial layout confusion by appending text. Re-extraction was always the right answer.

The fix (gap-informed re-extraction, D52) was implemented in Session 28.

**28 sessions of engineering effort went into a broken loop.**

### Problem 3: Model churn — switching models hoping for quality
```
Sessions 1-8:   qwen3:8b + qwen2.5vl:7b
Sessions 9-14:  qwen3.6:27b + qwen2.5vl:7b
Sessions 15-22: qwen3.6:27b + qwen3-vl:8b
Session 23:     gemma4:26b (unified — orchestrator + vision)
Session 26:     qwen3-vl:8b + qwen3:14b
Session 27-28:  qwen3-vl:8b + qwen3:14b (with fixes)
Now considering: qwen2.5vl:7b (was already installed in Session 8)
```

We switched models 6 times without a reliable benchmark. Some switches were rational (gemma4:26b for multimodal). Most were trying to escape quality problems that were actually pipeline problems, not model problems.

**The pipeline was broken. Better models couldn't fix a broken pipeline.**

### Problem 4: Features built before the foundation was validated
Timeline:
- Session 17: heuristic judge working ✓
- Session 18: Gap A (docling fallback) fixed ✓
- Session 20: slide_mode, exam_mode, pix2tex math OCR built
- Session 22: first clean 19-PDF benchmark run
- Session 24: **realised circular judge invalidated all benchmark scores**

We built slide_mode, exam_mode, math OCR, and poster_mode on top of a pipeline whose judge was lying. The Session 22 benchmark was useless because of the circular judge problem.

### Problem 5: PDF type classification was too simple from the start
5 types: text_rich, table_heavy, image_heavy, scanned, mixed.

These are signal-level types (what content is present), not document-type classifications (what kind of document is it). ICMR clinical flowchart posters get classified as `table_heavy` because pdfplumber finds 2 tables — so they go down the wrong extraction path.

24 sessions of ICMR work were harder than they needed to be because we never properly classified the document type.

### Problem 6: Reading order was broken from Session 10, still not fixed
Docling bug #1203: multi-column PDFs produce scrambled reading order. This was documented in Session 17 (D36 sort fix), but D36 only sorted elements by bbox within each page — it didn't detect columns and sort within columns.

Cardiology AF, neurology stroke, every multi-column document has incorrect reading order. 28 sessions and this still isn't fixed.

### Problem 7: No simple post-processing for structure
ALL-CAPS lines in GLM-OCR text are section headings. Five lines of regex would add `##` headings to every document that falls back to pdfplumber or GLM-OCR text. This was identified in Session 28, never implemented.

Result: every GLM-OCR fallback produces "0 headings" even when the content is complete.

---

## The right mental model (what we should have built)

```
1. CLASSIFY the document type FIRST
   → Not just "does it have images" but "what kind of document is this"
   → 10 types, each with known extraction strategy

2. PROFILE with all tools (no models)
   → docling: structure, element types, bboxes
   → GLM-OCR: ground truth text (already accurate, no VLM needed)
   → pdfplumber: raw text + tables
   → Geometry: column detection, reading order

3. EXTRACT with the right strategy for each type
   → text_linear: docling path, no VLM
   → poster_flowchart: qwen2.5vl:7b with poster prompt, GLM-OCR fallback
   → table_dominant: camelot + docling TableFormer
   → exam: VLM with exam prompt + pix2tex
   etc.

4. POST-PROCESS to add structure
   → ALL-CAPS → ## headings (simple regex)
   → Column sort for multi-column layouts
   → Fragment joining for pdfplumber text

5. JUDGE with GLM-OCR ground truth (instant, no model)
   → word_recall = intersection / GLM-OCR words
   → element_coverage = found / expected (docling)
   → Combined score

6. RE-EXTRACT once if score < 8.0 (not patch — full re-extract with gap context)
   → One additional pass, not 4 rounds
```

---

## Where we are today (Session 28)

**What's working:**
- Dengue (poster): 9.2/10, 23 headings, instant judge, 264s total ✓
- GLM-OCR ground truth runs reliably (26s per page)
- Heuristic judge is accurate and instant
- Cold stall detection prevents 30-min hangs
- 60/60 tests passing

**What's still broken:**
- Cardiology AF: VLM returns empty → GLM-OCR fallback → 0 headings (6.9/10) ✗
- Reading order for multi-column layouts (AF, stroke, research papers) ✗
- No ALL-CAPS → headings post-processing for GLM-OCR fallback output ✗
- qwen3-vl:8b inconsistent — enters batch mode on complex images ✗

**Key gap:**
For every document where the VLM works, we get good structured output.
For every document where the VLM fails or returns empty, we get correct content but no structure.
The post-processor is what bridges this gap — it should add structure to unstructured text that already has the right content.

---

## What to build next (priority order)

### Immediate (30 min, high impact)

**1. Switch VLM to qwen2.5vl:7b** (already installed)
Document-trained, more reliable streaming than qwen3-vl:8b.
Change one line in `.cloak_local.json`.

**2. Add `promote_allcaps_headings()` to postprocess.py**
Five lines of regex. Fixes "0 headings" on every GLM-OCR fallback document.
Test it on the AF output — SYMPTOMS, INVESTIGATIONS, SHOCK, etc. all become ## headings.

### Short-term (1 session each)

**3. Column-aware reading order**
Detect columns (X-bbox clustering), sort within each column top→bottom.
Fixes AF, stroke, research papers. Addresses docling bug #1203.

**4. Two-stage structuring for GLM-OCR fallback**
When VLM fails: GLM-OCR text → qwen3:14b (text-only, already loaded) → structured markdown.
The LLM is already in memory, costs nothing extra, and is much better than raw OCR text.

**5. Expand classification from 5 → 10 types**
See `docs/PDF_CLASSIFICATION.md` for the full taxonomy.
Each type gets the right tools — no more ICMR posters classified as `table_heavy`.

### Medium-term (2-3 sessions)

**6. Test qwen2.5vl:7b properly on all ICMR STWs**
After switching, run all 19 docs. Identify which ones still fail. Document patterns.

**7. External comparison: run MinerU2.5 on same ICMR docs**
MinerU scores 93/100 on OmniDocBench. Understanding where it beats us tells us what to fix.

**8. Proper ICMR schema judge**
"Does this STW have: WHEN TO SUSPECT, INVESTIGATIONS with ESSENTIAL/DESIRABLE/OPTIONAL, DISCHARGE CRITERIA?"
Binary checks. No model needed. This is what "done" means for ICMR STWs.

---

## The two things you need (user's instinct was right)

**1. Good profiler = understand the document type and geometry before any model call**
- What kind of document is this? (10-type classification)
- How many columns? (geometry, not docling)
- What's the reading order? (column-aware sort)
- Where are the section boundaries? (ALL-CAPS, colon-terminated lines, GLM-OCR text patterns)

**2. Proper post-processor = take raw accurate text and add structure**
- ALL-CAPS → ## headings
- Column-sorted text → properly ordered markdown
- Fragmented lines → joined sentences
- GLM-OCR text + docling bboxes → structured sections

These two things together mean: even when the VLM fails (which it will, unpredictably), the output is still structured, correct, and usable. The VLM becomes an enhancement, not a dependency.
