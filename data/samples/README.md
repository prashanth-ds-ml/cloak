# CLOAK Sample PDFs

Test corpus for validating the CLOAK pipeline across all document types.

Each subfolder contains 1–3 representative PDFs that specifically exercise
the parsing challenges of that document type.

---

## Folder Index

| Folder | Doc Type | Primary Parsing Challenges |
|---|---|---|
| `medical_report/` | Clinical guidelines, medical reports | Tables (drug dosages, criteria), multi-column, complex headings |
| `research_paper/` | Academic papers | 2-column layout, equations, figures with captions, references section |
| `textbook/` | Textbooks | Chapter hierarchy (H1→H4), figures, numbered exercises, multi-page tables |
| `government_document/` | Government reports | Dense text, policy tables, appendices, heavy footnote use |
| `legal_document/` | Legislation, court opinions | Numbered clauses, definitions sections, cross-references |
| `question_paper/` | Exam papers | Question numbering, mixed text+equations, option lists (a/b/c/d), marks |
| `technical_manual/` | Technical docs | Spec tables, diagrams, code blocks, numbered step procedures |
| `financial_report/` | Annual reports, SEC filings | Dense financial tables, charts, multi-column narrative |
| `scanned_pdf/` | Scanned image PDFs | No digital text layer — pure OCR challenge |
| `mixed_pdf/` | Scanned + digital pages mixed | Per-page strategy switching (OCR vs digital extraction) |
| `table_heavy/` | Data-dense documents | Many tables per page, borderless tables, merged cells |
| `image_heavy/` | Image-dominated docs | Many figures, captions detached from images, low text density |
| `multi_column/` | 2–3 column layouts | Reading order across columns, column boundary detection |
| `slide_deck/` | Presentation PDFs | Sparse text per page, large fonts, bullet-heavy, inconsistent headings |
| `form/` | Structured forms | Key-value pairs, checkboxes, blank fields, label alignment |
| `invoice/` | Invoices, receipts | Structured table + header block, currency values, line items |
| `poster/` | Academic posters | Single large page, multi-section grid, mixed font sizes |
| `bilingual_document/` | Two-language docs | Side-by-side or alternating language blocks, mixed scripts |

---

## Testing Notes

### What each type tests in CLOAK

**medical_report/** — Tests whether Docling preserves drug dosage tables (complex multi-column tables), clinical criteria lists, and numbered guidelines. The DRUGS & DOSAGE gap identified in Session 13 on `stemi.pdf` is representative.

**research_paper/** — Tests multi-column reading order (2-column academic layout), equation handling (FormulaItem), figure+caption linking (FigureItem), and references section preservation.

**textbook/** — Tests deep heading hierarchy (chapters → sections → subsections → sub-subsections), cross-page tables, numbered exercise lists, and figure captions that appear above or below figures.

**government_document/** — Tests appendix structure, footnote density, policy table extraction, and long document performance (many reports are 100+ pages).

**legal_document/** — Tests numbered clause preservation (§1.1, §1.1.1), definition blocks, cross-reference links, and reading order in schedules/annexures.

**question_paper/** — Tests question numbering (1., 1a., i., etc.), inline equations, option lists (A/B/C/D), marks annotations, and section separators. High table and list density.

**technical_manual/** — Tests numbered step procedures, code blocks, spec tables (model numbers, tolerances), and diagrams with labels.

**financial_report/** — Tests dense numerical tables, footnote linking from table cells, charts (image extraction), and narrative+table alternating structure.

**scanned_pdf/** — Tests Surya OCR path: no digital text layer. Forces `is_scanned=True` for all pages. Validates OCR quality and reading order reconstruction.

**mixed_pdf/** — Tests the per-page routing logic: some pages go through digital extraction, others through OCR. Validates that `page_type` detection correctly identifies scanned vs digital pages.

**table_heavy/** — Tests pdfplumber table extraction, borderless table detection, and table repair in the FORMAT step.

**image_heavy/** — Tests FigureItem crop + `region_describe()` vision call, caption detection and linking, and `image_area_ratio` threshold correctness.

**multi_column/** — Tests spatial sort (`bbox` reading order), column boundary detection, and whether text from different columns is correctly sequenced.

**slide_deck/** — Tests sparse-page handling, large-font heading detection, bullet list preservation, and performance on many short pages.

**form/** — Tests key-value pair extraction, checkbox handling, and blank field representation in Markdown.

**invoice/** — Tests line-item table extraction, header block (vendor, date, total), and currency value preservation.

**poster/** — Tests single large-page handling, multi-section grid layout, and mixed font sizes in a non-linear reading order.

**bilingual_document/** — Tests language detection, mixed-script OCR, and side-by-side bilingual table handling.

---

## Current Corpus Inventory

| Folder | Files | Size | Status |
|---|---|---|---|
| `medical_report/` | 0 | — | **Empty — needs real medical reports** (see medical_report/NOTES.md) |
| `research_paper/` | 2 | ~2.9 MB | Ready — Attention paper + BERT |
| `textbook/` | 1 | ~13 MB | Ready — P K Nag Engineering Thermodynamics |
| `government_document/` | 4 | ~4.3 MB | Ready — CDC MMWR, CDC Obesity brief, IRS Pub 17, WHO COVID-19 sitrep |
| `legal_document/` | 2 | ~2.2 MB | Ready — SCOTUS Dobbs opinion, US Appropriations Act 2020 |
| `question_paper/` | 2 | ~4.5 MB | Ready — JEE Advanced 2023 Paper 1 + Paper 2 |
| `technical_manual/` | 1 | ~14 MB | Ready — PostgreSQL 15 documentation |
| `financial_report/` | 1 | ~2.9 MB | Ready — Berkshire Hathaway 2023 Annual Report |
| `scanned_pdf/` | 1 | ~9 MB | Ready — History of Dumfries (1800s Google scan) |
| `table_heavy/` | 2 | ~3 MB | Ready — CDC NCHS codebook + NHANES survey contents |
| `image_heavy/` | 1 | ~6.5 MB | Ready — NASA ESTO Annual Report 2024 |
| `multi_column/` | 1 | ~3 MB | Ready — arXiv survey paper (2-column) |
| `slide_deck/` | 1 | ~8 MB | Ready — MIT OCW Computational Biology Lecture 1 |
| `form/` | 1 | ~215 KB | Ready — IRS Form 1040 |
| `invoice/` | 1 | ~43 KB | Ready — Sliced Invoices sample |
| `bilingual_document/` | 2 | ~211 KB | Ready — ECHR judgment (EN/FR) + UN ICCPR (EN/FR) |
| `poster/` | 10 | ~5.3 MB | Ready — 10 ICMR STW clinical flowchart posters (297×576mm, 1-page each) |
| `mixed_pdf/` | 0 | — | **Manual download needed** — see mixed_pdf/NOTES.md |

**Total: 33 PDFs · ~78 MB · 16 of 18 types populated**
**poster/ is now populated (10 ICMR STWs). medical_report/ is now empty — needs real multi-page medical documents.**

---

## How to Run a Test

```powershell
# Activate venv
.\.venv\Scripts\Activate.ps1

# Parse one sample
cloak parse data/samples/question_paper/sample_iit_jee.pdf

# Parse an entire type folder
cloak parse data/samples/question_paper/

# Validate the output
cloak validate data/outputs/question_paper/sample_iit_jee/final.md

# Check what was parsed
cloak list
```

---

## What a Good Parse Looks Like

After parsing, check these in `final.md`:

- [ ] YAML frontmatter present with correct title and confidence
- [ ] `cloak validate` returns VALID (no heading level skips, no broken tables)
- [ ] All major sections have headings at correct hierarchy level
- [ ] Tables have `| --- |` separator and equal column counts
- [ ] No page header/footer artifacts in body text
- [ ] Images have descriptive alt text (not `![image]`)
- [ ] Confidence score ≥ 8.0 for clean digital PDFs
- [ ] `human_review_queue.json` correctly identifies any low-confidence pages

---

## Adding New Samples

1. Place PDF in the correct subfolder
2. Run `cloak parse data/samples/{type}/{file}.pdf`
3. Inspect output: `cloak validate`, `cloak list`, read `final.md`
4. If parse quality is poor: document the failure in `data/samples/{type}/NOTES.md`
5. Use failure to improve the pipeline, then re-parse
