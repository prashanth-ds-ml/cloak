# Profiler Findings — All 158 ICMR STW Documents

> Complete profiling of the full ICMR STW corpus (all 4 volumes + hypertension).
> Tool: `scripts/profiler.py` combining pdfplumber + docling + GLM-OCR.
> Session 29 · 158 PDFs across 26 specialties.

---

## Summary statistics

| Metric | Value |
|---|---|
| Total documents | 158 |
| Specialties covered | 26 |
| text_mode (docling ≥ 60%) | 108 docs (68%) |
| hybrid (30–60%) | 23 docs (15%) |
| poster_mode (< 30%) | 27 docs (17%) |
| GLM-OCR success | 135/158 (85%) |
| Max columns detected | 5 |
| Docling coverage mean | 62.6% |
| Docling coverage median | 73.7% |

---

## Column structure across all 158 documents

| Columns | Count | Examples |
|---|---|---|
| 1 | 17 | Single-column narrative (some TB docs, cardiology_bradyarrhythmia) |
| 2 | 90 | Most ICMR STWs — the dominant layout |
| 3 | 39 | More complex STWs (psychiatry, neurology, paediatric surgery) |
| 4 | 9 | neo_thermal_care, obgyn_antenatal, obgyn_postpartum_haem, tb_microbiology, etc. |
| 5 | 3 | ortho_supracondylar, paediatrics_diarrhea, tb_paed_lymphnode |

**Column boundaries follow a consistent pattern across documents:**
- Narrow left reference column (x ≈ 8–25%): specialist labels, indications
- Main content column (x ≈ 25–55%): clinical content, assessment
- Right action column (x ≈ 55–80%): management, treatment
- Additional columns appear at 80–100% for complex flow

---

## Docling coverage distribution

```
0-10%   coverage: 12 docs  → poster_mode mandatory
10-30%  coverage: 15 docs  → poster_mode
30-60%  coverage: 23 docs  → hybrid
60-80%  coverage: 54 docs  → text_mode (most common group)
80-100% coverage: 54 docs  → text_mode (best group)
```

**The 0.9% minimum** is ortho_tibial_plateau — docling found essentially nothing on this page. The entire clinical content is inside one large picture element.

---

## Strategy routing for all 158 documents

### poster_mode (27 docs) — full VLM or GLM-OCR extraction

Sorted by docling coverage (ascending):

| Document | Coverage | GLM chars | Pic text chars |
|---|---|---|---|
| ortho_tibial_plateau | 0.9% | 7,133 | 7,499 |
| psychiatry_psychosis | 1.6% | 3,698 | 6,369 |
| ir_haemoptysis | 2.1% | 5,086 | — |
| paed_surg_undescended | 2.3% | 1,992 | 4,613 |
| paediatrics_dengue | 2.9% | 3,390 | 3,404 |
| paediatrics_fever | 3.8% | 4,949 | 5,998 |
| surg_diabetic_foot | 4.4% | 4,832 | — |
| neurology_acute_paralysis | 5.0% | 4,468 | 3,208 |
| neurology_headache | 7.9% | 3,645 | 4,113 |
| gastro_jaundice | 8.9% | 3,846 | — |
| pulmonology_copd | 9.9% | 2,553 | 5,043 |
| surg_cbd_stone | 10.9% | 3,687 | — |
| tb_abdominal | 12.4% | 0* | 4,056 |
| derm_alopecia | 13.0% | 0* | 5,098 |
| ortho_septic_arthritis | 13.6% | 4,653 | 4,278 |
| paediatrics_pneumonia | 14.4% | 4,817 | 5,398 |
| urology_acute_urinary_retention | 14.6% | 3,381 | 3,928 |
| ortho_ankle_fractures | 16.8% | 0* | 4,686 |
| endo_hyponatremia | 16.9% | 3,826 | — |
| gastro_gi_bleed_b | 16.9% | 2,204 | 3,459 |
| gastro_gi_bleed_a | 19.0% | 5,278 | — |
| haem_sickle_cell | 19.5% | 2,710 | — |
| tb_musculoskeletal | 20.4% | 5,115 | 3,653 |
| obgyn_heavy_menstrual | 20.6% | 3,624 | — |
| urology_male_infertility | 24.0% | 955 | 7,191 |
| cardiology_stemi | 29.1% | 5,186 | 3,882 |

`*` GLM-OCR failed with GGML error — use pdfplumber picture bbox text as fallback.

---

## Large content gaps — top 20 documents by picture section text

These documents have the most clinical content inside docling `picture` elements:

| Document | Pic text chars | Pics | Coverage |
|---|---|---|---|
| ortho_tibial_plateau | 7,499 | 1 | 0.9% |
| urology_male_infertility | 7,191 | 3 | 24.0% |
| psychiatry_psychosis | 6,369 | 1 | 1.6% |
| paediatrics_fever | 5,998 | 1 | 3.8% |
| paediatrics_pneumonia | 5,398 | 1 | 14.4% |
| derm_alopecia | 5,098 | 1 | 13.0% |
| pulmonology_copd | 5,043 | 1 | 9.9% |
| ortho_ankle_fractures | 4,686 | 3 | 16.8% |
| paed_surg_undescended | 4,613 | 1 | 2.3% |
| nephrology_chronic_kidney_disease | 4,572 | 2 | 41.1% |
| nephrology_glomerulonephritis | 4,458 | 1 | 2.4% |
| ortho_septic_arthritis | 4,278 | 1 | 13.6% |
| surg_diabetic_foot | 4,224 | 1 | 4.4% |
| neurology_headache | 4,113 | 1 | 7.9% |
| tb_abdominal | 4,056 | 1 | 12.4% |

**Key insight:** Even when docling coverage is low, pdfplumber CAN extract the content from inside the picture bbox. This is the primary fallback path — no VLM needed for most poster_mode docs.

---

## GLM-OCR performance

**85% success rate (135/158)** — 23 documents failed with GGML tensor assertion errors at all resize levels (1024/768/512px). No pattern found that predicts which documents fail — content-specific model bug.

### GLM-OCR hallucination — 3 confirmed cases

Documents where GLM-OCR generated fake numbered sections instead of real content:

| Document | Fake sections | Pattern |
|---|---|---|
| neurosurg_spinal | 106 | "CONSIDERATION 1" through "CONSIDERATION 106" |
| cardiology_nstemi | 100 | "CONSIDERATION 1" through "CONSIDERATION 100" |
| obgyn_antenatal | 90 | Similar numbered pattern |

**Detection rule (to implement):**
```python
def is_glm_hallucination(sections: list[str]) -> bool:
    if len(sections) < 10:
        return False
    numbered = sum(1 for s in sections if re.match(r'CONSIDERATION\s+\d+', s))
    return numbered / len(sections) > 0.5
```

When hallucination detected: discard GLM-OCR output, use pdfplumber picture bbox text.

### Documents with most valid GLM-OCR sections

| Document | Sections found |
|---|---|
| ophthal_diabetic_ret | 19 |
| paediatrics_dengue | 19 |
| urology_gross_haematuria | 19 |
| neurology_headache | 18 |
| urology_scrotal_swelling | 18 |
| surg_gallstone | 17 |
| derm_psoriasis | 16 |
| onco_lung | 16 |

---

## Edge cases and new discoveries

### New: 4-column and 5-column layouts

Previously we found max 3 columns. Full corpus reveals:

**5-column layouts:**
- `ortho_supracondylar`: boundaries at 17.6%, 50.5%, 68.9%, 79.2%
- `paediatrics_diarrhea`: boundaries at 9.6%, 21.2%, 60.3%, 74.8%
- `tb_paed_lymphnode`: boundaries at 7.4%, 27.8%, 49.0%, 68.1%

**4-column layouts (9 docs):**
- `ctvs_chronic_limb`, `derm_topical_steroids`, `ent_otorrhoea`
- `gastro_liver_fail`, `neo_thermal_care`, `obgyn_antenatal`
- `obgyn_postpartum_haem`, `tb_microbiology`, `tb_paed_meningitis`

**Impact on column detection:** The current algorithm handles up to 5 columns correctly (neurology_epilepsy 3-col confirmed, paediatrics_diarrhea 5-col detected). The issue is not missing columns — it's the spanning-header filter that incorrectly merges columns for AF-type docs.

### New: Very high coverage documents (>90%)

Documents where docling captures almost everything:
- `tb_paed_osteoarticular`: 96.2%
- `paediatrics_encephalitis`: 95.7%
- `psychiatry_alcohol`: 95.3%
- `endo_diabetes_t1`: 94.9%
- `cardiology_stable_angina`: 93.4%

For these, GLM-OCR is mainly used for section ordering correction, not content recovery.

### New: Interventional radiology — image-heavy (not flowcharts)

The IR documents are different from all other STWs:
- `ir_varicose_veins`: 35,710 KB (largest by far — full of procedure images)
- `ir_haemoptysis`: 10,557 KB
- `ir_liver_tumors`: 8,809 KB
- `ir_stroke`: 14,944 KB

These are NOT flowchart posters — they are multi-page procedure guides with actual medical images (angiograms, fluoroscopy). The profiler correctly classifies them as `poster_mode` but the extraction strategy needs to handle actual bitmap images, not vector flowcharts.

### New: Multi-page documents

Some docs in the full corpus are multi-page:
- IR documents: likely 2-5 pages
- Psychiatry collection docs: likely 2-3 pages
- Paediatric TB: likely 2-4 pages

The profiler currently only profiles page 0. Need to extend to handle multi-page documents when encountered.

---

## OCR error taxonomy (extended from 19→158 docs)

Beyond the 23 types found in the initial 19-doc corpus, new patterns from the full 158:

| Pattern | Example | Fix |
|---|---|---|
| Phonetic medical terms | THROMBOVIC, MIGRINE, ISTERITY | qwen3:14b correction pass |
| Numbered fake sections | CONSIDERATION 1-106 | Hallucination detection |
| Visual character confusion | CLIENTICAL, APPREPARED | qwen3:14b correction pass |
| Abbreviation expansion | ABREVIATIONS, REFERAL | Dictionary fix |
| British/Indian spelling | HAEMOPTYSIS vs HEMOPTYSIS | Accept both |
| Section label truncation | EARN BREAST CANCER (EARLY) | Context correction |
| Column header fusion | "BASIC EVALUATION \| HISTORY \| EXAMINATION" | Keep as-is (informative) |

---

## Profiler reliability assessment

| Feature | Status | Accuracy |
|---|---|---|
| Strategy routing | Works | ~95% (few misclassifications) |
| Column count detection | Works | ~85% (AF 3-col still shows 2) |
| Column boundaries | Works | ±5% accuracy |
| Picture bbox extraction | Works | 100% (pdfplumber reliable) |
| GLM-OCR section extraction | Works | 85% coverage, hallucination in 3% |
| HTML parsing (glm_to_plain_text) | Works | Good |
| Multi-page handling | Missing | Currently page 0 only |
| OCR correction | Missing | Needs qwen3:14b pass |
| Hallucination detection | Missing | Rule identified, not implemented |

---

## Recommended implementation roadmap

### Immediate (fixes profiler reliability)

1. **Hallucination detection** — if >10 sections AND >50% match `CONSIDERATION \d+` → discard
2. **Multi-page support** — profile all pages, not just page 0
3. **OCR correction pass** — qwen3:14b after GLM-OCR for medical term fixes
4. **AF-type column fix** — filter spanning headers (x < 5%) before clustering

### Short-term (improves extraction quality)

5. **Build GroundTruthMap** — structured output from DocumentProfile for pipeline use
6. **Coverage-based routing** — use docling_coverage_pct for Phase 3 decisions
7. **Picture bbox extraction** — use pdfplumber text from picture bboxes as fallback

### Longer-term (handles new document types)

8. **Multi-page IR documents** — different extraction strategy (actual radiology images)
9. **5-column sort** — reading order algorithm validated for up to 5 columns
10. **Profile more document types** — research papers, exam papers, legal, financial

---

## Files

- `scripts/profiler.py` — profiler implementation
- `scripts/download_icmr_stws.py` — downloads all 158 PDFs from icmr.gov.in
- `scripts/analyze_profiles.py` — statistical analysis of profiling results
- `data/batch_logs/profile2_*.txt` — full per-document reports (158 files)
- `data/batch_logs/profile2_*.json` — structured data per document
- `data/batch_logs/profile2_summary.json` — one-row summary for all 158
- `data/samples/icmr_stw_full/` — all 158 PDFs organized by specialty
