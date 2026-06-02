# Profiler Findings — All 19 ICMR STWs

> Complete profiling results from `scripts/profiler.py` combining pdfplumber + docling + GLM-OCR.
> Session 29 · All 19 documents profiled.

---

## Complete results table

| Document | Coverage | Cols | Col boundary | GLM chars | Pics | Pic text | Strategy |
|---|---|---|---|---|---|---|---|
| cardiology_af | 34.4% | 2 | 54.2% | 919* | 2 | 2,497 | hybrid |
| cardiology_bradyarrhythmia | 81.9% | 1 | — | 3,743 | 0 | 0 | text_mode |
| cardiology_heart_failure | 93.0% | 1 | — | 3,870 | 1 | 83 | text_mode |
| cardiology_nstemi | 58.5% | 2 | 15.6% | 4,835† | 2 | 1,747 | hybrid |
| cardiology_stable_angina | 93.4% | 2 | 15.8% | 5,013 | 0 | 0 | text_mode |
| cardiology_stemi | 29.1% | 2 | 52.8% | 5,186 | 2 | 3,882 | poster_mode |
| ctvs_head_injury | 78.0% | 2 | 61.5% | 4,008 | 1 | 1,127 | text_mode |
| neonatology_sepsis | 65.5% | 2 | 56.6% | 5,452 | 1 | 1,005 | text_mode |
| neurology_acute_paralysis | 5.0% | 2 | 16.9% | 4,468 | 1 | 3,208 | poster_mode |
| neurology_dementia | 66.7% | 2 | 53.9% | 3,702 | 2 | 735 | text_mode |
| neurology_epilepsy | 71.5% | **3** | 49.2%, 70.7% | 0‡ | 0 | 0 | text_mode |
| neurology_headache | 7.9% | 2 | 21.9% | 3,645 | 1 | 4,113 | poster_mode |
| neurology_neuroinfections | 0% | 1 | — | 4,223 | 0 | 0 | poster_mode |
| neurology_stroke | 84.5% | 2 | 54.4% | 1,981 | 0 | 0 | text_mode |
| oncology_breast | 61.1% | 2 | 13.4% | 3,441 | 3 | 1,361 | text_mode |
| ortho_low_back_pain | 38.9% | 1 | — | 0‡ | 1 | 2,966 | hybrid |
| paediatrics_dengue | 2.9% | 1 | — | 3,390 | 1 | 3,404 | poster_mode |
| psychiatry_depression | 84.2% | 2 | 55.0% | 5,781 | 1 | 34 | text_mode |
| tb_adult_abdominal | 12.4% | 2 | 33.2% | 0‡ | 1 | 4,056 | poster_mode |

`*` cardiology_af: GLM-OCR partial (only 919 chars — 3-column layout compressed too narrow)
`†` cardiology_nstemi: GLM-OCR hallucinated (generated "CONSIDERATION 1–100" fake sections — see edge cases)
`‡` GGML tensor assertion error at all resize levels — content-specific GLM-OCR model bug

---

## GLM-OCR sections found per document

These are the ALL-CAPS clinical section headings GLM-OCR extracted. Sections marked ← are NOT in docling (new findings).

### paediatrics_dengue — 19 sections (complete) ✓
SYMPTOMS · WHEN TO SUSPECT? · ASSESSMENT · TREATMENT OF PROBABLE DENGUE WITHOUT WARNING SIGNS
SEVERE DENGUE · REASONS FOR REFERRAL · INVESTIGATIONS · ESSENTIAL · SHOCK
COMPENSATED SHOCK · OPTIONAL · HYPOTENSIVE SHOCK · PACKED RED CELLS
INDICATION FOR PLATELET TRANSFUSION & PACKED RED CELLS · PLATELETS
FRESH FROZEN PLASMA/CRYOPRECIPITATE · DISCHARGE CRITERIA · CLINICAL
KEEP A HIGH THRESHOLD FOR INVASIVE PROCEDURES

### cardiology_stemi — 9 sections ✓ (including 3 new)
ECG REVEALS ST ELEVATION MI | GENERAL MEASURES · DURING PROCEDURE | POST THROMBOLYSIS
POST PROCEDURE · PATIENT WITH STEMI IN 12-24 HOURS
**PATIENT WITH STEMI AFTER 24 HOURS** ← not in docling
**ABSOLUTE CONTRAINDICATIONS TO THROMBOLYTIC THERAPY** ← not in docling
**DRUGS & DOSAGE | STEMI DIAGNOSIS** ← not in docling (critical missing section!)
REFERENCES

### neonatology_sepsis — 11 sections ✓ (including 5 new)
**RED FLAG SIGNS** ← not in docling
**YELLOW FLAG SIGNS** ← not in docling
HIGH PROBABILITY OF SEPSIS · REVIEW AT 48 HRS
**SIGNS OF SEPSIS DISAPPEARED (CRP <12 MG/L)** ← not in docling
**SIGNS OF SEPSIS WORSENED** ← not in docling
DURATION OF ANTIBIOTICS · CONDITION · REMEMBER · ABBREVIATIONS · REFERENCES

### psychiatry_depression — 9 sections ✓ (including 4 new)
CORE SYMPTOMS · ADDITIONAL SYMPTOMS · INVESTIGATION
**AT PRIMARY CARE** ← not in docling
**MILD DEPRESSION** ← not in docling
**REFERRED TO SECONDARY CARE** ← not in docling
**REFERRED TO TERTIARY CARE** ← not in docling (OCR: ISTERITY → TERTIARY)
REFERENCES · KEEP A HIGH THRESHOLD FOR INVASIVE PROCEDURES

### neurology_acute_paralysis — 7 sections (mostly new)
PRESENTATION WITH ACUTE ONSET PARAPLEGIA OR QUADRIPLEGIA
**WEAKNESS PROXIMAL · REFLEXES ABSENT · REFLEXES PRESENT** ← not in docling
**MANAGEMENT · DISCHARGE CRITERIA** ← not in docling
KEEP A HIGH THRESHOLD FOR INVASIVE PROCEDURES

### cardiology_bradyarrhythmia — 6 sections
BASIC EVALUATION | HISTORY | EXAMINATION | TESTS TO BE DONE
EVALUATION AND TREATMENT OF UNSTABLE PATIENTS
INDICATIONS FOR PERMANENT PACING | AV CONDUCTION DISEASE
ECG: SINUS BRADYCARDIA | ECG: THIRD DEGREE HEART BLOCK
ABBREVIATIONS · REFERENCES

### ctvs_head_injury — 6 sections
CLINICAL ASSESSMENT (OCR: CLIENTICAL) · NEUROLOGICAL ASSESSMENT
MANAGEMENT AT HIGHER CENTRE WITH CT SCAN & NEUROSURGEON
MILD HEAD INJURY (GCS: 13-15) | MODERATE HEAD INJURY (GCS: 9-12)
MEDICAL MANAGEMENT · KEEP A HIGH THRESHOLD FOR INVASIVE PROCEDURES

### neurology_headache — 17 sections (dense but many OCR errors)
ICD-10-G43-44 · CONTINUOUS HEADACHES · CHRONIC TENSION HEADACHE
TREATMENT FLOWCHART LEGEND/INDEX/KEY · MIGRAINE (OCR: MIGRINE)
TRIGEMINAL AUTONOMIC CEPHALALGIAS (OCR: TREASURAL AUTONOMIC CERIAL LASER)
CLUSTER HEADACHE · RED FLAG SIGNS (OCR: RID FLAG SIGNS)
TREATMENT OF MAJOR CATASTROPHIC HEADACHES AT TERTIARY CENTRE
SUBARACHNOID HAEMORRHAGE (OCR: SURRAGENOID MEMORAGE)
INDICATIONS FOR ADMISSION · CRITERIA FOR DISCHARGE
FOLLOW UP · CAUSES OF HEADACHE · TREATMENT OF HEADACHE
KEEP A HIGH THRESHOLD FOR INVASIVE PROCEDURES · REFERENCES · ABBREVIATIONS

### neurology_dementia — 5 sections
IMPORTANT POINTS TO CONSIDER · EVALUATION OF DEMENTIA
FOLLOW UP — INTERVENTION MATRIX FOR DEMENTIA ACROSS PLATFORMS OF CARE
MEDICATIONS FOR COGNITION | FOR DEPRESSION

### oncology_breast — 5 sections
SIGNS | WORK UP — TRIPLE ASSESSMENT · MULTIDISCIPLINARY CARE
MANAGEMENT OF BREAST CANCER
EARLY BREAST CANCER | ADVANCED BREAST CANCER (OCR: EARN BREAST CANCER)
ABBREVIATIONS

### neurology_stroke — 1 section only
KEEP A HIGH THRESHOLD FOR INVASIVE PROCEDURES
*(Stroke GLM-OCR returned plain text, not HTML — most content not in ALL-CAPS)*

### cardiology_stable_angina — 8 sections
PATIENT PRESENTING WITH CHEST PAIN | CONSIDER ANGINA IF
ANGINA UNLIKELY IF · INVESTIGATIONS
SENSITIVE INVESTIGATIONS | OTHER INVESTIGATIONS | OPTIONAL INVESTIGATIONS
RISK CATEGORY STRATIFICATION (OCR: ANGINATION) · ABBREVIATIONS · REFERENCES

---

## OCR errors catalogue

All phonetic/visual OCR errors found across 19 documents. These can be corrected with a medical term correction pass (qwen3:14b).

| OCR output | Correct term | Doc | Type |
|---|---|---|---|
| THROMBOVIC THERAPY | THROMBOLYTIC THERAPY | stemi | Phonetic |
| POST THROMBOSIS | POST THROMBOLYSIS | stemi | Phonetic |
| ISTERITY CARE | TERTIARY CARE | depression | Phonetic |
| AT TERMARY CARE | AT TERTIARY CARE | neuroinfections | Phonetic |
| DURANTARY CARE | TERTIARY CARE | heart_failure | Phonetic |
| CLIENTICAL ASSESSMENT | CLINICAL ASSESSMENT | ctvs | Visual |
| MIGRINE | MIGRAINE | headache | Phonetic |
| SURRAGENOID MEMORAGE | SUBARACHNOID HAEMORRHAGE | headache | Phonetic |
| TREASURAL AUTONOMIC CERIAL LASER | TRIGEMINAL AUTONOMIC CEPHALALGIAS | headache | Phonetic |
| RID FLAG SIGNS | RED FLAG SIGNS | headache | Visual |
| PROXISMAL HEMICRIANAS | PAROXYSMAL HEMICRANIAS | headache | Phonetic |
| CONDUCTIVAL INJECTION | CONJUNCTIVAL INJECTION | headache | Phonetic |
| APPREPARED | APPEARED | sepsis | Visual |
| ABREVIATIONS | ABBREVIATIONS | sepsis, headache | Visual |
| SCONDARY CARE | SECONDARY CARE | neuroinfections | Visual |
| AV CONDUCTION DIESECTION | AV CONDUCTION DISEASE | bradyarrhythmia | Visual |
| EARN BREAST CANCER | EARLY BREAST CANCER | oncology_breast | Visual |
| PRECARCIATION | PRECAUTION / PRECAUTIONS | heart_failure | Visual |
| RISK CATEGORY ANGINATION | RISK CATEGORY STRATIFICATION | stable_angina | Phonetic |
| MORPHOGEN | MORPHINE? | stemi | Unclear |
| RASTLESSE | RESTLESSNESS | dengue | Phonetic |
| REASONS FOR REFERAL | REASONS FOR REFERRAL | dengue | Visual |
| CRYOPRECIPIATE | CRYOPRECIPITATE | dengue | Visual |

**Fix: medical OCR correction pass using qwen3:14b after GLM-OCR extraction**
```
Prompt: "Fix OCR phonetic/visual substitution errors in this medical text.
Rules: 1) Keep all clinical values exact (numbers, doses, thresholds, signs).
       2) Fix only clear medical terminology errors.
       3) Keep ALL-CAPS headings in ALL-CAPS.
       4) Do not add or remove content — only fix spelling.
Return corrected text only."
```

---

## Edge cases found

### Edge case 1 — GLM-OCR hallucination (cardiology_nstemi)

GLM-OCR generated "CONSIDERATION 1" through "CONSIDERATION 100" — 100 fake section headings — instead of actual content. The drug dosage table in nstemi has numbered items, and at 527×1024px resolution the model interpreted the entire document as a numbered list and hallucinated section names.

**Detection:** If >10 sections found and >50% match the pattern `CONSIDERATION \d+` → hallucination flag.
**Mitigation:** Discard GLM-OCR output for this doc, use pdfplumber text from picture bboxes instead.

### Edge case 2 — Corrupt PDF (neurology_neuroinfections)

Docling FAILS (pypdfium2 cannot open the PDF). GLM-OCR WORKS (renders page image, extracts 4,223 chars). This validates image-based extraction as a fallback for corrupt PDFs.

**Sections found:** ESSENTIAL · AT SECONDARY CARE LEVEL · AT TERTIARY CARE HOSPITAL (OCR: SCONDARY/TERMARY)

### Edge case 3 — GLM-OCR GGML tensor errors (neurology_epilepsy, ortho_low_back_pain, tb_adult_abdominal)

Content-specific GGML assertion failure in GLM-OCR at all resize levels (1024/768/512px). Cause unknown — something in these specific images triggers a tensor dimension mismatch in the model.

**Mitigation:** Fall back to pdfplumber text (pdfplumber has 7,314, 5,804, 5,499 chars respectively). For ortho and tb, the picture section text (2,966 and 4,056 chars) is available via pdfplumber bbox extraction.

### Edge case 4 — Minimal GLM-OCR output (neurology_stroke)

Only 1,981 chars extracted (lowest of any successful run) and only 1 section found. Stroke GLM-OCR output appears to be plain text (not HTML tables), so ALL-CAPS section detection finds almost nothing.

**Docling coverage is 84.5%** so docling handles stroke well. GLM-OCR is less important here.

### Edge case 5 — Partial GLM-OCR (cardiology_af)

Only 919 chars extracted from a 5,849-char document. The 3-column layout is compressed to 526px wide at 1024px long-edge resize. Most text is too small to read accurately.

**Current workaround:** Use pdfplumber text from picture bboxes (2,497 chars available).
**Better fix:** Higher resolution for wide multi-column docs, or use pdfplumber bbox text directly.

---

## Column detection results

| Document | Detected | Correct | Notes |
|---|---|---|---|
| cardiology_af | 2 (54.2%) | 3 | Missing left col boundary (~35%). Spanning header at x=2.6% pulls algorithm wrong |
| cardiology_bradyarrhythmia | 1 | 1 | ✓ Single column |
| cardiology_heart_failure | 1 | 1 | ✓ Single column |
| cardiology_nstemi | 2 (15.6%) | 2 | ✓ Narrow left ref col + main col |
| cardiology_stable_angina | 2 (15.8%) | 2 | ✓ Same pattern |
| cardiology_stemi | 2 (52.8%) | 2 | ✓ PCI Capable | PCI Incapable |
| ctvs_head_injury | 2 (61.5%) | 2 | ✓ |
| neonatology_sepsis | 2 (56.6%) | 2 | ✓ |
| neurology_acute_paralysis | 2 (16.9%) | 2 | ✓ |
| neurology_dementia | 2 (53.9%) | 2 | ✓ |
| **neurology_epilepsy** | **3 (49.2%, 70.7%)** | **3** | **✓ Perfect 3-column detection** |
| neurology_headache | 2 (21.9%) | 2 | ✓ |
| neurology_neuroinfections | 1 | ? | Docling failed |
| neurology_stroke | 2 (54.4%) | 2 | ✓ |
| oncology_breast | 2 (13.4%) | 2 | ✓ |
| ortho_low_back_pain | 1 | ? | Too few headers for detection |
| paediatrics_dengue | 1 | 1 | ✓ All content in one picture element |
| psychiatry_depression | 2 (55.0%) | 2 | ✓ |
| tb_adult_abdominal | 2 (33.2%) | 2 | ✓ |

**16/19 column detection is correct.** 3 issues:
- cardiology_af: detects 2 instead of 3 (spanning header at x=2.6% skews clustering)
- ortho_low_back_pain: too few docling headers for clustering
- neurology_neuroinfections: docling failed entirely

---

## What the profiler now tells us for each document

The `DocumentProfile` output contains:

```
docling_coverage_pct    → strategy routing (text_mode/hybrid/poster_mode)
columns.count           → number of columns
columns.boundaries_pct  → exact X% boundaries for reading order sort
picture_sections        → list of content gaps (bbox + pdfplumber text inside)
picture_text_chars      → total chars of content inside picture bboxes
glm_text                → GLM-OCR HTML output (parse to get ordered text)
glm_sections            → clinical section headings (after HTML parsing)
sections_only_in_glm    → sections GLM found but docling missed
```

This is sufficient to drive the extraction pipeline without any VLM call for 10/19 documents (text_mode group).

---

## Next steps

### Fix OCR errors
1. Add `correct_medical_ocr(text, model="qwen3:14b")` function to profiler
2. Call after GLM-OCR extraction, before section detection
3. Validate: THROMBOVIC → THROMBOLYTIC, ISTERITY → TERTIARY, etc.

### Fix GLM-OCR hallucination detection
1. If >10 sections AND >50% match `CONSIDERATION \d+` pattern → hallucination flag
2. Discard GLM-OCR output, fall back to pdfplumber picture bbox text

### Fix cardiology_af 3-column detection
1. Filter spanning headers (x < 5% or x > 80%) before column clustering
2. STROKE RISK SCORE at x=2.6% is a spanning header, not a column anchor

### Build GroundTruthMap dataclass for pipeline
Use `DocumentProfile` to produce `GroundTruthMap` that drives Phase 3 extraction:
```python
GroundTruthMap:
  strategy:         "text_mode" | "hybrid" | "poster_mode"
  column_boundaries: list[float]        # for reading order sort
  ordered_text:      str                # GLM-OCR parsed text (corrected)
  sections:          list[str]          # clinical sections found
  picture_bboxes:    list[BboxInfo]     # where to aim VLM crops or camelot
  picture_texts:     list[str]          # pdfplumber text within each picture bbox
```

### Profile more document types
Once ICMR STW profiling is solid, run profiler on:
- Research papers (BERT, ArXiv) → validate text_mode for academic 2-col
- Exam papers (JEE/GATE) → validate exam_mode signals  
- Scanned docs → validate scanned detection
- Slide decks → validate slide_mode signals
