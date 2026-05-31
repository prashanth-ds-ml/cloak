# Cloak — Benchmark Results

> Generated: 2026-05-25 12:56  |  Session 21  |  D38 slide_mode · D39 exam_mode · D35 pix2tex

## Score legend

✅ ≥ 9.0 (excellent)  |  🟡 ≥ 8.0 (good, meets threshold)  |  🟠 ≥ 6.0 (fair)  |  🔴 < 6.0 (poor)

## Results summary

| # | PDF Type | Score | Coverage | Completeness | Time | Notes |
|---|----------|-------|----------|--------------|------|-------|
| 1 | Research paper (two-column, academic) | 🟡 8.4 | 69% pages ≥ 8.0 | 83% words captured | 20.5 min | BERT paper — dense academic, references, 2-col layout |
| 2 | Medical guideline (clinical protocol) | 🟠 6.2 | 0% pages ≥ 8.0 | 82% words captured | 20.1 min | STEMI protocol — structured clinical text, decision trees |
| 3 | Legal document (court opinion) | ✅ 9.2 | 100% pages ≥ 8.0 | 89% words captured | 0.4 min | SCOTUS Dobbs — dense legal prose, footnotes, headings |
| 4 | Financial report (annual report) | ✅ 9.1 | 80% pages ≥ 8.0 | 98% words captured | 0.3 min | Berkshire 2023 AR — narrative + financial tables |
| 5 | Technical manual (software docs) | 🟡 8.7 | 80% pages ≥ 8.0 | 79% words captured | 0.6 min | PostgreSQL 15 docs — code blocks, technical terminology |
| 6 | Bilingual legal document | ✅ 9.9 | 100% pages ≥ 8.0 | 100% words captured | 0.6 min | ECHR judgment — English + French columns side-by-side |
| 7 | Table-heavy (government codebook) | ✅ 9.6 | 90% pages ≥ 8.0 | 99% words captured | 21.1 min | NHANES codebook — dense tables, variable codes, definitions |
| 8 | Government report (text + tables) | 🟠 7.9 | 60% pages ≥ 8.0 | 83% words captured | 75.1 min | IRS Pub 17 — mixed text and complex tax tables |
| 9 | Invoice (structured form) | 🟠 6.5 | 0% pages ≥ 8.0 | 78% words captured | 20.8 min | Sample invoice — line items, totals, addresses |
| 10 | Slide deck (image-heavy) | 🟡 8.0 | 70% pages ≥ 8.0 | 100% words captured | 71.1 min | MIT OCW — slide images, diagrams, text-sparse slides; D38 sl |
| 11 | Image-heavy annual report | 🟠 6.6 | 10% pages ≥ 8.0 | 99% words captured | 146.9 min | NASA ESTO 2024 — full-bleed images, captions, infographics |
| 12 | Medical poster (image + text layout) | 🟠 6.2 | 0% pages ≥ 8.0 | 86% words captured | 26.8 min | Neurology poster — mixed image/text, single-page dense layou |
| 13 | Multi-column academic survey | 🟠 7.6 | 40% pages ≥ 8.0 | 100% words captured | 78.2 min | ArXiv survey — 2-col layout, citations, figures |
| 14 | Textbook (engineering, math equations) | 🟡 8.3 | 60% pages ≥ 8.0 | 91% words captured | 41.7 min | P K Nag thermodynamics — inline equations, figures, multi-se |
| 15 | Exam paper — JEE Advanced 2023 | ✅ 9.1 | 100% pages ≥ 8.0 | 100% words captured | 67.6 min | JEE 2023 P1 — fragmented Symbol-font math, multi-choice; D39 |
| 16 | Exam paper — GATE CS 2024 | 🔴 5.9 | 0% pages ≥ 8.0 | 100% words captured | 103.4 min | GATE 2024 CS — theory + logic + programming questions; D39 |
| 17 | Exam paper — GATE EE 2024 | 🔴 5.9 | 0% pages ≥ 8.0 | 59% words captured | 742.8 min | GATE 2024 EE — circuit problems, equations, diagrams; D39 |
| 18 | Exam paper — ESE EE 2024 (UPSC) | 🟠 6.2 | 0% pages ≥ 8.0 | 100% words captured | 65.8 min | UPSC ESE 2024 EE — heavy diagrams, circuit problems, image-b |
| 19 | Scanned historical document | 🟠 6.2 | 0% pages ≥ 8.0 | 100% words captured | 42.1 min | 1800s Dumfries history — low-res scan, no text layer; Surya  |

## Detailed results

### 1. Research paper (two-column, academic)

**File**: `data/samples/research_paper/bert_devlin_2018.pdf`  
**Score**: 🟡 8.4/10  
**Coverage**: 69% pages ≥ 8.0 (11/16)  
**Completeness**: 83% words captured  
**Structure**: 33 headings · 8 tables  
**Time**: 20.5 min  
**Notes**: BERT paper — dense academic, references, 2-col layout  

### 2. Medical guideline (clinical protocol)

**File**: `data/raw/cardiology/stemi.pdf`  
**Score**: 🟠 6.2/10  
**Coverage**: 0% pages ≥ 8.0 (0/1)  
**Completeness**: 82% words captured  
**Structure**: 23 headings · 0 tables  
**Time**: 20.1 min  
**Notes**: STEMI protocol — structured clinical text, decision trees  

### 3. Legal document (court opinion)

**File**: `data/samples/legal_document/scotus_dobbs_opinion_sliced.pdf`  
**Score**: ✅ 9.2/10  
**Coverage**: 100% pages ≥ 8.0 (10/10)  
**Completeness**: 89% words captured  
**Structure**: 20 headings · 0 tables  
**Time**: 0.4 min  
**Notes**: SCOTUS Dobbs — dense legal prose, footnotes, headings  

### 4. Financial report (annual report)

**File**: `data/samples/financial_report/berkshire_hathaway_2023_annual_report_sliced.pdf`  
**Score**: ✅ 9.1/10  
**Coverage**: 80% pages ≥ 8.0 (8/10)  
**Completeness**: 98% words captured  
**Structure**: 13 headings · 0 tables  
**Time**: 0.3 min  
**Notes**: Berkshire 2023 AR — narrative + financial tables  

### 5. Technical manual (software docs)

**File**: `data/samples/technical_manual/postgresql_15_docs_sliced.pdf`  
**Score**: 🟡 8.7/10  
**Coverage**: 80% pages ≥ 8.0 (4/5)  
**Completeness**: 79% words captured  
**Structure**: 4 headings · 0 tables  
**Time**: 0.6 min  
**Notes**: PostgreSQL 15 docs — code blocks, technical terminology  

### 6. Bilingual legal document

**File**: `data/samples/bilingual_document/echr_judgment_en_fr.pdf`  
**Score**: ✅ 9.9/10  
**Coverage**: 100% pages ≥ 8.0 (21/21)  
**Completeness**: 100% words captured  
**Structure**: 51 headings · 1 tables  
**Time**: 0.6 min  
**Notes**: ECHR judgment — English + French columns side-by-side  

### 7. Table-heavy (government codebook)

**File**: `data/samples/table_heavy/cdc_nchs_body_measurements_codebook_sliced.pdf`  
**Score**: ✅ 9.6/10  
**Coverage**: 90% pages ≥ 8.0 (9/10)  
**Completeness**: 99% words captured  
**Structure**: 25 headings · 1 tables  
**Time**: 21.1 min  
**Notes**: NHANES codebook — dense tables, variable codes, definitions  

### 8. Government report (text + tables)

**File**: `data/samples/government_document/irs_publication_17_sliced.pdf`  
**Score**: 🟠 7.9/10  
**Coverage**: 60% pages ≥ 8.0 (6/10)  
**Completeness**: 83% words captured  
**Structure**: 29 headings · 3 tables  
**Time**: 75.1 min  
**Notes**: IRS Pub 17 — mixed text and complex tax tables  

### 9. Invoice (structured form)

**File**: `data/samples/invoice/sample_invoice_sliced.pdf`  
**Score**: 🟠 6.5/10  
**Coverage**: 0% pages ≥ 8.0 (0/1)  
**Completeness**: 78% words captured  
**Structure**: 3 headings · 3 tables  
**Time**: 20.8 min  
**Notes**: Sample invoice — line items, totals, addresses  

### 10. Slide deck (image-heavy)

**File**: `data/samples/slide_deck/mit_ocw_computational_biology_lecture1_sliced.pdf`  
**Score**: 🟡 8.0/10  
**Coverage**: 70% pages ≥ 8.0 (7/10)  
**Completeness**: 100% words captured  
**Structure**: 0 headings · 0 tables  
**Time**: 71.1 min  
**Notes**: MIT OCW — slide images, diagrams, text-sparse slides; D38 slide_mode  

### 11. Image-heavy annual report

**File**: `data/samples/image_heavy/nasa_esto_annual_report_sliced.pdf`  
**Score**: 🟠 6.6/10  
**Coverage**: 10% pages ≥ 8.0 (1/10)  
**Completeness**: 99% words captured  
**Structure**: 35 headings · 0 tables  
**Time**: 146.9 min  
**Notes**: NASA ESTO 2024 — full-bleed images, captions, infographics  

### 12. Medical poster (image + text layout)

**File**: `data/samples/poster/neurology_stroke.pdf`  
**Score**: 🟠 6.2/10  
**Coverage**: 0% pages ≥ 8.0 (0/1)  
**Completeness**: 86% words captured  
**Structure**: 11 headings · 0 tables  
**Time**: 26.8 min  
**Notes**: Neurology poster — mixed image/text, single-page dense layout  

### 13. Multi-column academic survey

**File**: `data/samples/multi_column/arxiv_survey_multi_column_sliced.pdf`  
**Score**: 🟠 7.6/10  
**Coverage**: 40% pages ≥ 8.0 (4/10)  
**Completeness**: 100% words captured  
**Structure**: 7 headings · 0 tables  
**Time**: 78.2 min  
**Notes**: ArXiv survey — 2-col layout, citations, figures  

### 14. Textbook (engineering, math equations)

**File**: `data/samples/textbook/engineering_thermodynamics_pk_nag_sliced.pdf`  
**Score**: 🟡 8.3/10  
**Coverage**: 60% pages ≥ 8.0 (6/10)  
**Completeness**: 91% words captured  
**Structure**: 4 headings · 0 tables  
**Time**: 41.7 min  
**Notes**: P K Nag thermodynamics — inline equations, figures, multi-section  

### 15. Exam paper — JEE Advanced 2023

**File**: `data/samples/question_paper/jee_advanced_2023_paper1_sliced.pdf`  
**Score**: ✅ 9.1/10  
**Coverage**: 100% pages ≥ 8.0 (10/10)  
**Completeness**: 100% words captured  
**Structure**: 0 headings · 0 tables  
**Time**: 67.6 min  
**Notes**: JEE 2023 P1 — fragmented Symbol-font math, multi-choice; D39  

### 16. Exam paper — GATE CS 2024

**File**: `data/samples/question_paper/gate_cs_2024_sliced.pdf`  
**Score**: 🔴 5.9/10  
**Coverage**: 0% pages ≥ 8.0 (0/10)  
**Completeness**: 100% words captured  
**Structure**: 1 headings · 1 tables  
**Time**: 103.4 min  
**Notes**: GATE 2024 CS — theory + logic + programming questions; D39  

### 17. Exam paper — GATE EE 2024

**File**: `data/samples/question_paper/gate_ee_2024_sliced.pdf`  
**Score**: 🔴 5.9/10  
**Coverage**: 0% pages ≥ 8.0 (0/10)  
**Completeness**: 59% words captured  
**Structure**: 5 headings · 2 tables  
**Time**: 742.8 min  
**Notes**: GATE 2024 EE — circuit problems, equations, diagrams; D39  

### 18. Exam paper — ESE EE 2024 (UPSC)

**File**: `data/samples/question_paper/ese_ee_2024_sliced.pdf`  
**Score**: 🟠 6.2/10  
**Coverage**: 0% pages ≥ 8.0 (0/10)  
**Completeness**: 100% words captured  
**Structure**: 1 headings · 0 tables  
**Time**: 65.8 min  
**Notes**: UPSC ESE 2024 EE — heavy diagrams, circuit problems, image-based; D39  

### 19. Scanned historical document

**File**: `data/samples/scanned_pdf/history_dumfries_1800s_scanned_sliced.pdf`  
**Score**: 🟠 6.2/10  
**Coverage**: 0% pages ≥ 8.0 (0/5)  
**Completeness**: 100% words captured  
**Structure**: 2 headings · 0 tables  
**Time**: 42.1 min  
**Notes**: 1800s Dumfries history — low-res scan, no text layer; Surya OCR  
