# ICMR STW Profiling Findings

> Deep profiling of 7 ICMR Standard Treatment Workflow PDFs.
> Run date: Session 29 · Script: `scripts/profile_icmr.py`
> Goal: understand the exact structure of each document before writing extraction code.

---

## Universal facts about ICMR STWs

All 7 profiled documents share these characteristics:

| Property | Value | Notes |
|---|---|---|
| Page size | 842 × 1634 pts | 11.7" × 22.7" — A3 portrait format |
| Page count | 1 | Always single page |
| No bitmap images | 0% image area ratio (mostly) | Boxes and flowcharts are PDF vector, not bitmaps |
| PDF vector content | Yes | Text and boxes are real PDF text, not scanned |

**All ICMR STWs are single-page A3 PDF vector documents.** The boxes and arrows are PDF drawing commands, not images. pdfplumber can extract the text but in wrong order. Docling can extract structure but misses content inside complex sections (treats them as `picture` elements).

---

## Per-document profiles

### paediatrics_dengue.pdf

```
pdfplumber:  4,290 chars · 649 words · 6 tables · 0 images
Docling:     2.9% coverage (123/4290 chars) · 6 elements total
             3 picture · 1 section_header · 2 text
Columns:     1 (single column, or all in one big picture)
Large pictures: 1 — x=0% y=12% size=99%w × 84%h  ← ENTIRE CONTENT
```

**Diagnosis:** The whole flowchart (84% of page height) is ONE large `picture` element in docling. Docling sees almost nothing — 2.9% coverage. pdfplumber has the text but in wrong spatial order. This is the extreme case requiring full VLM poster extraction.

**pdfplumber tables:** 6 tables found, including:
- Full flowchart layout (y=0-50%)
- COMPENSATED SHOCK section (y=54-57%)
- HYPOTENSIVE SHOCK section (y=54-57%)
- Discharge criteria (y=90-100%)

**Extraction approach:** poster_mode → VLM full page (qwen2.5vl:7b) or GLM-OCR text + structure pass

---

### cardiology_af.pdf

```
pdfplumber:  5,849 chars · 851 words · 3 tables · 1 image (3.6%)
Docling:     34.4% coverage (2014/5849 chars) · 69 elements
             5 picture · 16 section_header · 39 list_item · 8 text · 1 table
Columns:     3  (x < ~35% | 35-63% | 63-100%)
Large pictures: 2 — Heart Rate Control (y=44%, 100%w × 30%h) + Management Algorithm (y=75%, 96%w × 17%h)
```

**Diagnosis:** Three-column layout. Top 42% of page (risk assessment, scoring) is well-structured text in docling. Bottom 58% (heart rate control + management algorithm) is treated as `picture` by docling — those sections are complex multi-column tables with embedded text. 34.4% docling coverage means we're missing 3,835 chars of clinical content.

**3 columns confirmed from section header X positions:**
- Column 1 (x=0-35%): SYMPTOMS, SIGNS, LOOK FOR RISK FACTORS
- Column 2 (x=35-63%): LOOK FOR PRECIPITATING FACTORS, MANAGEMENT PRINCIPLES, HEART RATE CONTROL
- Column 3 (x=63-100%): CATEGORIZE AF, LOOK FOR IMMEDIATE INTERVENTION INDICATORS, CHOICE OF ANTI-COAGULATION

**pdfplumber tables:**
- Table 1 (y=31-42%): CHA2DS2-VASc / HAS-BLED — 3 rows × 4 cols — grid table suitable for camelot
- Table 2 (y=43-75%): HEART RATE CONTROL — 13 rows × 7 cols — complex management algorithm
- Table 3 (y=96-100%): Footer

**Extraction approach:** Column-aware docling sort (top 42%) + camelot for CHA2DS2-VASc table + VLM crop for heart rate section

---

### neurology_stroke.pdf

```
pdfplumber:  5,313 chars · 805 words · 1 table · 0 images
Docling:     84.5% coverage (4489/5313 chars) · 43 elements
             5 picture · 11 section_header · 18 list_item · 9 text
Columns:     mixed (some multi-column sections)
Large pictures: 0 (no large content pictures)
```

**Diagnosis:** Best docling coverage of all (84.5%). Most content is captured. The missing 15.5% (824 chars) is likely in the one complex table (TYPES OF STROKE, y=34-55%, 5 rows × 8 cols). No large picture areas — this is the most "text-like" of all ICMR STWs.

**Section headers found:** Only 10 headers (missing: PRELIMINARY MANAGEMENT, INVESTIGATIONS, etc.) — these sections are inside the large table at y=34-55%.

**pdfplumber tables:**
- Table 1 (y=34-55%): TYPES OF STROKE — 5 rows × 8 cols — complex clinical table with ISCHEMIC / HAEMORRHAGIC / MANAGEMENT sections

**Extraction approach:** Docling path + column sort + pdfplumber table for TYPES OF STROKE section

---

### cardiology_stemi.pdf

```
pdfplumber:  6,625 chars · 1,011 words · 6 tables · 0 images
Docling:     29.1% coverage (1927/6625 chars) · 44 elements
             5 picture · 14 section_header · 20 list_item · 5 text
Columns:     2  (boundary at x=22%)
Large pictures: 2 — diagnosis section (y=9%, 99%w × 20%h) + drugs/dosage section (y=52%, 100%w × 39%h)
```

**Diagnosis:** Two-column layout detected correctly (boundary at 22%). Top section (y=0-29%) and bottom sections (y=52-95%) are large pictures. The clinical pathway content (PCI capable hospital, GENERAL MEASURES, thrombolysis) is well-captured in docling (column sort output looked correct). The DRUGS & DOSAGE table (y=52-92%, 5 rows × 12 cols) is the main missing piece.

**Column-sorted reading order worked correctly for stemi** — the docling extracted content came out in the right clinical order (PCI CAPABLE / PCI INCAPABLE side by side at y=38-50% correctly separated).

**pdfplumber tables:**
- Table 4 (y=38-51%): PCI INCAPABLE CENTRE — 2 rows × 1 col
- Table 5 (y=51-56%): PATIENT WITH STEMI IN 12-24 HOURS — 3 rows × 2 cols
- Table 6 (y=61-92%): DRUGS & DOSAGE / CONTRA-INDICATIONS — 5 rows × 12 cols ← most important missing section

**Extraction approach:** Column-aware docling sort (works for top 52%) + camelot for DRUGS & DOSAGE table (complex 12-col table)

---

### neonatology_sepsis.pdf

```
pdfplumber:  5,356 chars · 826 words · 5 tables · 0 images
Docling:     65.5% coverage (3508/5356 chars) · 54 elements
             6 picture · 10 section_header · 17 list_item · 19 text · 2 table
Columns:     1 (single column or 2-column with boundary around x=50%)
Large pictures: 1 — clinical assessment section (y=15%, 97%w × 25%h)
```

**Diagnosis:** Good docling coverage (65.5%). One large picture section (y=15-40%, 25% of page height) contains the clinical assessment content. The rest of the document (AT-RISK SEPSIS, REVIEW AT 48 HRS, DURATION OF ANTIBIOTICS, REMEMBER, ABBREVIATIONS) is well-captured.

**Section headers:** HIGH PROBABILITY OF SEPSIS and AT-RISK/SUSPECT SEPSIS both at y=41.6% but different X (15% vs 67%) — 2-column layout for those sections.

**Extraction approach:** Docling path + VLM crop for the large picture section (y=15-40%)

---

### psychiatry_depression.pdf

```
pdfplumber:  6,608 chars · 1,038 words · 2 tables · 4 images (9.3%)
Docling:     84.2% coverage (5563/6608 chars) · 59 elements
             5 picture · 10 section_header · 33 list_item · 10 text · 1 table
Columns:     2  (boundary at x=22%)
Large pictures: 1 — left column section (y=14%, 29%w × 21%h)
```

**Diagnosis:** Second-best docling coverage (84.2%). Two-column layout (same 22% boundary as stemi). The column-sorted reading order produced a nearly perfect extraction. Missing content (1,045 chars) is inside the large picture in the left column (y=14-35%).

**pdfplumber tables:**
- Table 1 (y=29-35%): Severity classification — 4 rows × 3 cols (mild/moderate/severe symptoms)
- Table 2 (y=39-100%): AT PRIMARY CARE / SECONDARY CARE treatment — 12 rows × 4 cols ← main treatment table

**Extraction approach:** Docling path with column sort (works well) + camelot for treatment table

---

### ortho_low_back_pain.pdf

```
pdfplumber:  5,804 chars · 870 words · 5 tables · 122 images (24.5%)
Docling:     [profiling failed — Unicode encoding error in terminal output]
```

**Diagnosis (from partial data):** 122 images with 24.5% area coverage — this is the most image-heavy ICMR STW. The images are likely small icons, checkboxes, or decorative elements. Needs re-profiling with UTF-8 encoding.

---

## Cross-document patterns

### 1. Docling coverage as extraction strategy signal

| Coverage | Documents | Extraction approach |
|---|---|---|
| < 30% | dengue (2.9%), stemi (29.1%) | poster_mode: VLM full-page extraction |
| 30–70% | cardiology_af (34.4%), neonatology_sepsis (65.5%) | Hybrid: docling text + VLM/camelot for picture sections |
| > 70% | stroke (84.5%), depression (84.2%) | Docling path: column sort → good output |

**This replaces the current `_detect_poster()` threshold.** Instead of counting docling elements, use coverage percentage:
- Coverage < 40%: poster_mode (full VLM)
- Coverage 40-75%: hybrid mode (docling + targeted extraction for picture sections)
- Coverage > 75%: text_mode (docling path + column sort)

### 2. Column layout patterns

| Doc | Columns | Column boundaries | Detection |
|---|---|---|---|
| dengue | 1 (all in picture) | n/a | n/a |
| cardiology_af | 3 | ~35%, ~63% | FAILED (algorithm needs fix) |
| stroke | 1-2 (unclear) | ~32% possibly | FAILED |
| stemi | 2 | 22% | CORRECT |
| sepsis | 1-2 | ~50% possibly | Not detected |
| depression | 2 | 22% | CORRECT |

**Stemi and depression both have boundary at x=22%** — a narrow left reference/note column and a main content column. This might be a common ICMR template pattern.

**Cardiology AF has 3 columns** — the algorithm failed because filtering at x < 60% excluded the right-column headers. Algorithm needs to consider all headers including those at x > 60%.

### 3. The "large picture" problem

Every ICMR STW has 1-2 large `picture` elements in docling that contain clinical content:

| What docling calls a picture | What it actually is |
|---|---|
| dengue (y=12%, 99%×84%) | The entire clinical flowchart |
| AF (y=44%, 100%×30%) | HEART RATE CONTROL flowchart table |
| AF (y=75%, 96%×17%) | MANAGEMENT ALGORITHM flowchart |
| stemi (y=9%, 99%×20%) | ANGINA/CHEST PAIN diagnosis section |
| stemi (y=52%, 100%×39%) | DRUGS & DOSAGE table |
| sepsis (y=15%, 97%×25%) | Clinical assessment |
| depression (y=14%, 29%×21%) | Left-column WHEN TO SUSPECT section |

**These are NOT images.** They are PDF sections where the content is rendered with complex visual formatting (colored backgrounds, merged cells, arrows). pdfplumber can read the text from them — the bbox information tells us exactly where they are.

### 4. pdfplumber tables as a signal

| Doc | pdfplumber tables | Significance |
|---|---|---|
| dengue | 6 | All major flowchart sections detected as tables |
| cardiology_af | 3 | CHA2DS2-VASc (extractable), Heart Rate Control (complex) |
| stroke | 1 | TYPES OF STROKE (8-col complex table) |
| stemi | 6 | Multiple sections including DRUGS & DOSAGE |
| sepsis | 5 | Multiple assessment tables |
| depression | 2 | Severity table + treatment table |

**pdfplumber table bboxes tell us exactly where complex content is.** We can use these bboxes to route specific regions to camelot or VLM crops, not the whole page.

---

## Recommended extraction architecture per document

```
dengue:
  Approach: poster_mode (VLM full-page)
  Fallback: GLM-OCR text + qwen3:14b structuring
  Reason:   2.9% docling coverage — docling has nothing useful

cardiology_af:
  Approach: docling top sections + column sort
            + camelot for CHA2DS2-VASc table (y=31-42%, clear grid)
            + VLM crop for heart rate control (y=43-75%)
            + VLM crop for management algorithm (y=75-93%)
  Reason:   34.4% docling coverage, 3 columns, 2 large picture sections

neurology_stroke:
  Approach: docling path + column sort
            + pdfplumber table for TYPES OF STROKE (y=34-55%)
  Reason:   84.5% coverage, mostly text, one complex table

cardiology_stemi:
  Approach: docling path + column sort (2 cols, 22% boundary)
            + camelot for DRUGS & DOSAGE table (y=61-92%, 12 cols)
  Reason:   29.1% coverage BUT column sort works for PCI sections

neonatology_sepsis:
  Approach: docling path + VLM crop for clinical assessment section (y=15-40%)
  Reason:   65.5% coverage, one large picture section

psychiatry_depression:
  Approach: docling path + column sort (2 cols, 22% boundary)
            + camelot for treatment table (y=39-100%, 4 cols)
  Reason:   84.2% coverage, column sort works very well

ortho_low_back_pain:
  Approach: Re-profile needed (Unicode error during profiling)
  Note:     122 images suggest icon-heavy layout
```

---

## What to build next (priority order)

### Immediate — fixes all docs

1. **Fix column detection** — include headers at x > 60% in boundary detection
2. **Add `promote_allcaps_headings()` to postprocess.py** — fixes 0 headings on GLM-OCR fallback
3. **Switch VISION_PRIMARY to qwen2.5vl:7b** — already installed, better document extraction

### Next session — targeted per-doc improvements

4. **Use docling coverage (not element count) for poster_mode detection**
   - Coverage < 40%: poster_mode
   - Coverage 40-75%: hybrid (docling + picture section extraction)
   - Coverage > 75%: text_mode (docling + column sort)

5. **Extract picture sections by bbox** — when docling marks a region as `picture` but it spans > 10% of page height, extract it as a targeted region:
   - Try camelot (if pdfplumber finds a table at that bbox)
   - Fall back to VLM crop of that region
   - Fall back to pdfplumber text from that region

6. **Fix column-aware reading order** — reorder docling elements by detected columns before extraction

### Future

7. **camelot for grid tables** — CHA2DS2-VASc (AF), DRUGS & DOSAGE (stemi), treatment table (depression)
8. **Complete ortho_low_back_pain profiling** with UTF-8 encoding
9. **Profile remaining 12 ICMR STWs** to build complete picture
