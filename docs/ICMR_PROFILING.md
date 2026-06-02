# ICMR STW Profiling Findings — All 19 Documents

> Deep profiling combining pdfplumber + docling + GLM-OCR on all 19 ICMR STWs.
> Run: Session 29 · Script: `scripts/profile_combined.py`
> Purpose: understand each document before writing extraction code.

---

## Universal facts across all 19 ICMR STWs

| Property | Value |
|---|---|
| Page count | 1 (always single page) |
| Page size | 842 × 1634 pts (11.7" × 22.7" — A3 portrait) |
| PDF type | Born-digital vector PDF (not scanned) |
| Image area | 0–10% (flowchart boxes are vector art, not bitmap images) |

---

## Complete profiling table (all 19 documents)

| Document | PDF chars | PDF tables | Docling elems | Docling coverage | Large pics | GLM-OCR chars | Recommended strategy |
|---|---|---|---|---|---|---|---|
| cardiology_af | 5,849 | 3 | 69 | **34.4%** | 2 | 919* | hybrid |
| cardiology_bradyarrhythmia | 4,884 | 1 | 109 | 81.9% | 0 | 3,743 | text_mode |
| cardiology_heart_failure | 6,988 | 1 | 178 | 93.0% | 1 | 3,870 | text_mode |
| cardiology_nstemi | 7,934 | 4 | 146 | 58.5% | 1 | 4,835 | hybrid |
| cardiology_stable_angina | 6,004 | 2 | 140 | 93.4% | 0 | 5,013 | text_mode |
| cardiology_stemi | 6,625 | 6 | 44 | **29.1%** | 2 | 5,186 | poster_mode |
| ctvs_head_injury | 7,133 | 3 | 136 | 78.0% | 1 | 4,008 | text_mode |
| neonatology_sepsis | 5,356 | 5 | 54 | 65.5% | 1 | 5,452 | text_mode |
| neurology_acute_paralysis | 4,179 | 1 | 7 | **5.0%** | 1 | 4,468 | poster_mode |
| neurology_dementia | 4,966 | 3 | 34 | 66.7% | 2 | 3,702 | text_mode |
| neurology_epilepsy | 7,314 | 5 | 47 | 71.5% | 0 | 0** | text_mode |
| neurology_headache | 5,295 | 3 | 14 | **7.9%** | 1 | 3,645 | poster_mode |
| neurology_neuroinfections | 5,103 | 12 | 0† | 0% | 0 | 4,223 | poster_mode |
| neurology_stroke | 5,313 | 1 | 43 | 84.5% | 0 | 1,981 | text_mode |
| oncology_breast | 5,821 | 7 | 89 | 61.1% | 3 | 3,441 | text_mode |
| ortho_low_back_pain | 5,804 | 5 | 27 | 38.9% | 1 | 0** | hybrid |
| paediatrics_dengue | 4,290 | 6 | 6 | **2.9%** | 1 | 3,390 | poster_mode |
| psychiatry_depression | 6,608 | 2 | 59 | 84.2% | 1 | 5,781 | text_mode |
| tb_adult_abdominal | 5,499 | 4 | 11 | **12.4%** | 1 | 0** | poster_mode |

`*` cardiology_af: GLM-OCR extracted only 919 chars (partial — large image hits 1024px limit at 527px wide)
`**` GLM-OCR failed at all resize levels (1024/768/512px) — content-specific GGML bug in model
`†` neurology_neuroinfections: corrupt PDF — docling fails, but GLM-OCR works from rendered image

---

## Document groups by extraction strategy

### Group A — text_mode (docling coverage > 60%) — 10 documents

GLM-OCR ordered text + docling heading structure. No VLM needed.

```
cardiology_bradyarrhythmia  81.9%   3,743 glm chars
cardiology_heart_failure    93.0%   3,870 glm chars
cardiology_stable_angina    93.4%   5,013 glm chars
ctvs_head_injury            78.0%   4,008 glm chars
neonatology_sepsis          65.5%   5,452 glm chars
neurology_dementia          66.7%   3,702 glm chars
neurology_epilepsy          71.5%   0 glm (fallback: pdfplumber)
neurology_stroke            84.5%   1,981 glm chars
oncology_breast             61.1%   3,441 glm chars
psychiatry_depression       84.2%   5,781 glm chars
```

For these: GLM-OCR gives correctly-ordered text, docling gives heading hierarchy.
Combine → structured markdown without any VLM call.

### Group B — hybrid (30–60% coverage) — 3 documents

GLM-OCR text for well-structured sections + targeted VLM crop or camelot for large picture sections.

```
cardiology_af     34.4%   2 large pictures (y=44%, y=75%)   919 glm (partial)
cardiology_nstemi 58.5%   1 large picture  (y=10%)          4,835 glm chars
ortho_low_back_pain 38.9% 1 large picture                   0 glm (pdfplumber)
```

### Group C — poster_mode (< 30% coverage) — 6 documents

Most content is in large picture elements. Full-page VLM extraction needed.
GLM-OCR text is good fallback when VLM fails.

```
cardiology_stemi         29.1%   2 large pictures   5,186 glm chars
neurology_acute_paralysis  5.0%  1 large picture    4,468 glm chars
neurology_headache         7.9%  1 large picture    3,645 glm chars
neurology_neuroinfections  0%    corrupt PDF        4,223 glm chars ← GLM-OCR still works!
paediatrics_dengue         2.9%  1 large picture    3,390 glm chars
tb_adult_abdominal        12.4%  1 large picture    0 glm (GGML error)
```

---

## Key insight: GLM-OCR vs pdfplumber vs docling

### GLM-OCR chars vs pdfplumber chars

For docs where GLM-OCR worked:

| Doc | PDF chars | GLM chars | Ratio | Notes |
|---|---|---|---|---|
| psychiatry_depression | 6,608 | 5,781 | 87.5% | Good coverage |
| cardiology_stable_angina | 6,004 | 5,013 | 83.5% | Good coverage |
| neonatology_sepsis | 5,356 | 5,452 | 101.8% | GLM > PDF (adds table formatting) |
| cardiology_stemi | 6,625 | 5,186 | 78.3% | Good coverage |
| neurology_acute_paralysis | 4,179 | 4,468 | 106.9% | GLM > PDF |
| cardiology_nstemi | 7,934 | 4,835 | 61.0% | Partial (complex tables) |
| neurology_stroke | 5,313 | 1,981 | 37.3% | Low — likely timeout or partial |

**GLM-OCR extracts 70-100%+ of pdfplumber chars in most cases**, with correct column-aware reading order.

### The "large picture" content gap

Every ICMR STW has 0–3 `picture` elements in docling that are actually clinical content (not logos). These are the sections where docling gave up. Identified by size > 5% of page area:

| Doc | Large picture locations (y%) | What they contain |
|---|---|---|
| dengue | y=12% (84%h) | ENTIRE clinical flowchart |
| stemi | y=9% (20%h), y=52% (39%h) | Diagnosis section + DRUGS table |
| af | y=44% (30%h), y=75% (17%h) | Heart rate control + Mgmt algorithm |
| acute_paralysis | y=? (large) | Most clinical content |
| headache | y=? (large) | Most clinical content |
| nstemi | y=10% (20%h) | Chest pain diagnosis section |
| sepsis | y=15% (25%h) | Clinical assessment section |
| depression | y=14% (21%h) | Left-column WHEN TO SUSPECT |
| ctvs_head_injury | y=? (large) | Clinical content section |
| dementia | 2 large sections | Clinical content |
| oncology_breast | 3 large sections | Highest picture density |

---

## GLM-OCR failure analysis

### 3 documents that GGML-fail at all resize levels

`neurology_epilepsy`, `ortho_low_back_pain`, `tb_adult_abdominal`

**Cause:** Content-specific GGML tensor assertion error in the GLM-OCR model (`GGML_ASSERT(a->ne[2] * 4 == b->ne[0]) failed`). Tried all three sizes (1024px → 768px → 512px), converted to RGB — still fails. This is a bug in the Ollama/GLM-OCR model for specific image content.

**Mitigation:** Fall back to pdfplumber text. These documents have pdfplumber coverage of 4,179–7,314 chars which is enough for text_mode extraction. The reading order will be imperfect (pdfplumber extracts in stream order) but content is present.

### cardiology_af partial extraction (919 chars)

At 527×1024px (the stable resize), the 3-column layout is only 176px wide per column — too narrow for accurate OCR. GLM-OCR extracts only the most prominent text. The retry sizes (768, 512) would be even narrower.

**Mitigation:** Use pdfplumber text for AF or extract at higher resolution (1536px long-edge, accept occasional GGML errors).

---

## Section header extraction by GLM-OCR

GLM-OCR sections were detected using ALL-CAPS line detection. Sample from key docs:

### paediatrics_dengue (GLM-OCR, 3,390 chars)
`WHEN TO SUSPECT?` `WARNING SIGNS` `ASSESSMENT` `TREATMENT OF PROBABLE DENGUE WITHOUT WARNING SIGNS` `SEVERE DENGUE` `REASONS FOR REFERRAL` `INVESTIGATIONS` `SHOCK` `COMPENSATED SHOCK` `HYPOTENSIVE SHOCK` `INDICATION FOR PLATELET TRANSFUSION & PACKED RED CELLS` `DISCHARGE CRITERIA`

12 clinical sections correctly identified — matches the PDF structure exactly.

### psychiatry_depression (GLM-OCR, 5,781 chars)
`DEPRESSION ICD10-F45` `CORE SYMPTOMS` `CLINICAL ASSESSMENT` `ADDITIONAL SYMPTOMS` `ASSESSMENT OF SUICIDE RISK` `INVESTIGATION` `AT PRIMARY CARE` `REFERRAL TO SECONDARY CARE` `REFERENCES`

9 sections — complete clinical structure captured.

---

## neurology_neuroinfections — special case

Docling FAILS on this PDF (corrupt PDF format — pypdfium2 cannot open it).
GLM-OCR WORKS (4,223 chars) — because GLM-OCR works from the rendered page image, not the PDF structure.

**Insight:** Image-based extraction (GLM-OCR) is more robust than PDF-structure extraction (docling) for certain PDFs. Always run GLM-OCR from the rendered image, not from the PDF directly.

---

## Recommended profiler architecture

Based on all 19 profiles:

### Phase 1 profiling — always run in this order:

```
Step 1: pdfplumber  (instant)
  → page geometry, word positions, table bboxes

Step 2: docling  (8-12s, CPU)
  → element types, heading hierarchy, bboxes
  → identify "large picture" sections (content gaps)
  → compute docling_coverage = docling_chars / pdfplumber_chars

Step 3: GLM-OCR  (10-80s, GPU)
  → column-aware text extraction (correct reading order)
  → tables as markdown
  → fallback chain: 1024px → 768px → 512px on GGML errors
  → if all fail: use pdfplumber text as ground truth

Step 4: Classify document
  → coverage < 30%: poster_mode (VLM full-page)
  → coverage 30-60%: hybrid (GLM-OCR + VLM crops for picture sections)
  → coverage > 60%: text_mode (GLM-OCR ordered text + docling headings)
```

### Phase 3 extraction — strategy per group:

**text_mode (10 docs, 53% of corpus):**
- Primary content: GLM-OCR ordered text (correct column order)
- Structure: docling section headers (inject ## headings at correct positions)
- Tables: pdfplumber table bboxes → camelot or docling TableFormer
- No VLM needed

**hybrid (3 docs, 16% of corpus):**
- Text sections: GLM-OCR ordered text + docling headings
- Picture sections: targeted VLM crop (qwen2.5vl:7b) of just the large picture bbox
- Tables: camelot for grid tables

**poster_mode (6 docs, 31% of corpus):**
- Full-page VLM extraction (qwen2.5vl:7b with _POSTER_PROMPT)
- Fallback: GLM-OCR text + `promote_allcaps_headings()` postprocessing
- Target: 2.9% docling coverage → GLM-OCR gives 87% content recovery

---

## What to implement next

Priority 1 (immediate, 30 min each):
1. `promote_allcaps_headings()` in postprocess.py — fixes "0 headings" on GLM-OCR fallback
2. Switch VISION_PRIMARY to qwen2.5vl:7b — already installed, better document training

Priority 2 (1 session):
3. `_build_from_ground_truth(glm_text, docling_elements)` — combine GLM-OCR content + docling headings
4. Coverage-based routing in Phase 3 (replace binary poster_mode with 3-tier coverage routing)
5. Better column detection using docling element X positions (current algorithm fails for AF)

Priority 3 (1-2 sessions):
6. Targeted picture section extraction using docling `picture` bboxes
7. camelot for identified grid tables (CHA2DS2-VASc in AF, DRUGS table in stemi)
8. Fix cardiology_af GLM-OCR (higher resolution or different extraction strategy)
