# PDF Classification & Workflow Routing — cloak

> Design document: how to classify PDFs by type and route each type to the right tools.
> Current classifier has 5 types. This doc proposes 10 types with specific detection signals.
> Updated: Session 28

---

## The Problem with Current Classification

The current 5-type classifier uses only:
- `text_length` (pdfplumber chars)
- `image_area_ratio` (image block area / page area)
- `table_count` (pdfplumber tables found)

This misclassifies:
- ICMR flowchart posters → `table_heavy` (has 2 pdfplumber tables, lots of text)
- Academic papers → `text_rich` (docling reading order then scrambles columns)
- Exam papers → sometimes `text_rich`, needs special math handling

**Root cause:** The classifier looks at WHAT is present, not HOW it is laid out.

---

## Proposed 10-Type Taxonomy

### Type 1: `text_linear`
**What it is:** Single-column text documents. Legal, government reports, narrative text.
**Detection signals:**
- `text_length > 500`
- `image_area_ratio < 0.15`
- `table_count <= 1`
- Docling elements: mostly `text` and `section_header`, few `picture`
- Average text block width > 70% of page width (single column)

**Best extraction:** docling path → pdfplumber text → no VLM needed
**Models:** none for extraction; `qwen3:14b` for FORMAT if needed
**Quality risks:** footnotes, cross-references

---

### Type 2: `text_multicolumn`
**What it is:** Two-column academic papers, research papers, newsletters.
**Detection signals:**
- `text_length > 500`
- Multiple docling text elements with X-bbox concentrated in two clusters (left ~25-45%, right ~55-75% of page width)
- `image_area_ratio < 0.40`
- Docling bug #1203 applies here — reading order may be scrambled

**Best extraction:** Custom column-aware sort (by X-cluster then Y) before docling path
**Models:** no VLM for text; `qwen2.5vl:7b` only for figures
**Quality risks:** Column mixing (docling bug), citation formatting, footnotes

---

### Type 3: `poster_flowchart`
**What it is:** Single-page clinical flowcharts, medical posters, decision trees (ICMR STW).
**Detection signals:**
- `page_count <= 5` (short document)
- `text_length > 500` (has real text)
- Docling text coverage `< 50%` of pdfplumber text (docling misses most content)
- OR docling text elements `< 8` despite `text_length > 800`
- Many short text segments (avg docling element text < 30 chars)

**Best extraction:**
1. `qwen2.5vl:7b` with `_POSTER_PROMPT` → structured markdown with branches
2. Fallback: GLM-OCR text + `qwen3:14b` structuring (two-stage)
3. Last resort: GLM-OCR raw text + `promote_allcaps_headings()` postprocess

**Models:** `qwen2.5vl:7b` (primary), `glm-ocr` (ground truth), `qwen3:14b` (structuring fallback)
**Quality risks:** Branch order, column mixing, arrow direction lost

---

### Type 4: `table_dominant`
**What it is:** Spreadsheet-like documents, coding manuals, data tables. Tables are the primary content.
**Detection signals:**
- `table_count >= 3` (pdfplumber finds many tables)
- `text_length` relative to table content is low
- Docling finds many `table` elements

**Best extraction:** camelot (lattice for grid tables) + docling TableFormer + pdfplumber fallback
**Models:** `glm-ocr` for table crops; no VLM unless tables have images inside
**Quality risks:** Merged cells, spanning headers, column alignment

---

### Type 5: `exam_paper`
**What it is:** JEE/GATE/ESE exam papers with math, circuits, multiple choice.
**Detection signals:**
- Existing `_detect_exam_paper()` patterns (Q.1, GATE 2024, Maximum Marks, etc.)
- `formula_count >= 3` (docling detects FormulaItems)
- High image content (circuit diagrams, graphs)

**Best extraction:** `qwen2.5vl:7b` with `_EXAM_PROMPT` → LaTeX for math
**Models:** `qwen2.5vl:7b`, `pix2tex` for formula crops
**Quality risks:** Symbol-font math (docling text is garbled), circuit diagrams

---

### Type 6: `slide_deck`
**What it is:** PDF-exported presentations. Sparse content per page, large images.
**Detection signals:**
- `page_count >= 5`
- Average `text_length` per page `< 300` (sparse)
- `image_area_ratio > 0.30` on most pages
- Consistent page dimensions (slides are uniform)

**Best extraction:** `qwen2.5vl:7b` with `_SLIDE_PROMPT` per slide
**Models:** `qwen2.5vl:7b`
**Quality risks:** Speaker notes absent, animations baked into background

---

### Type 7: `scanned`
**What it is:** Image-only pages, no embedded text. Scanned physical documents.
**Detection signals:**
- `text_length < 100` (pdfplumber extracts nothing)
- `image_area_ratio > 0.40`
- Already handled by current `scanned` type

**Best extraction:** `glm-ocr` → surya → tesseract fallback chain
**Models:** `glm-ocr`, surya (GPU), tesseract (CPU fallback)
**Quality risks:** Image quality, handwriting, unusual fonts

---

### Type 8: `image_figure_heavy`
**What it is:** Reports with many large figures — NASA reports, annual reports, image-heavy books.
**Detection signals:**
- `image_area_ratio > 0.40` per page
- `text_length > 100` (has some text, not scanned)
- Docling finds many `picture` elements

**Best extraction:** docling path for text sections + `qwen2.5vl:7b` for figure crops
**Models:** `qwen2.5vl:7b` for figures
**Quality risks:** Figure captions, cross-references to figure numbers

---

### Type 9: `form`
**What it is:** Structured forms — IRS forms, registration forms, applications.
**Detection signals:**
- Many short isolated text fragments
- Low running text (no paragraphs > 2 lines)
- Likely many tables with few rows but structured layout
- `text_length / table_count` ratio is low

**Best extraction:** pdfplumber + docling tables; form fields as key:value pairs
**Models:** `glm-ocr` for complex form sections
**Quality risks:** Checkbox states, signature fields, overlapping elements

---

### Type 10: `mixed_standard`
**What it is:** The catch-all for documents with mixed content that don't match above types. Textbooks, technical manuals, complex reports.
**Detection signals:** Does not match any specific type above.

**Best extraction:** docling path (handles mixed content best)
**Models:** `qwen2.5vl:7b` for figures; `glm-ocr` for tables
**Quality risks:** Depends on specific content

---

## Detection Signal Table

| Signal | How to compute | Used for |
|---|---|---|
| `text_length` | `len(pdfplumber text)` | current — basic |
| `image_area_ratio` | image block area / page area | current — basic |
| `table_count` | pdfplumber tables found | current — basic |
| `avg_element_text_len` | `mean(len(e.text) for docling elements)` | poster detection |
| `docling_coverage` | `sum(len(e.text)) / text_length` | poster detection (Signal B) |
| `column_count` | X-bbox clustering of text blocks | multicolumn detection |
| `avg_text_per_page` | total text / page count | slide detection |
| `formula_count` | docling FormulaItem count | exam detection |
| `page_width_uniformity` | std dev of page dimensions | slide detection |
| `has_exam_keywords` | existing `_detect_exam_paper()` regex | exam detection |

---

## Routing Decision Tree

```
PDF loaded
  │
  ├─ Has exam keywords? → exam_paper
  │
  ├─ page_count >= 5 AND avg_text_per_page < 300 AND image_ratio > 0.3?
  │    └─ slide_deck
  │
  ├─ page_count <= 5 AND text_length > 500 AND docling_coverage < 0.50?
  │    └─ poster_flowchart
  │
  ├─ text_length < 100 AND image_ratio > 0.40?
  │    └─ scanned
  │
  ├─ table_count >= 3?
  │    └─ table_dominant
  │
  ├─ image_ratio > 0.40 AND text_length > 100?
  │    └─ image_figure_heavy
  │
  ├─ column_count == 2 (X-bbox clustering)?
  │    └─ text_multicolumn
  │
  ├─ form signals (many short isolated fragments)?
  │    └─ form
  │
  ├─ single-column text?
  │    └─ text_linear
  │
  └─ default → mixed_standard
```

---

## Extraction Strategy per Type

| Type | Phase 1 profiling | Phase 3 extraction | VLM needed? | Key library |
|---|---|---|---|---|
| `text_linear` | docling + glm-ocr | docling path | No | pdfplumber |
| `text_multicolumn` | docling + column-sort | column-aware docling | For figures only | custom sort |
| `poster_flowchart` | docling + glm-ocr | qwen2.5vl:7b → glm-ocr+qwen3 | Yes | glm-ocr |
| `table_dominant` | docling TableFormer | camelot + pdfplumber | No | camelot-py |
| `exam_paper` | docling + pix2tex | qwen2.5vl:7b full-page | Yes | pix2tex |
| `slide_deck` | docling (sparse) | qwen2.5vl:7b per slide | Yes | — |
| `scanned` | — | glm-ocr → surya → tesseract | No (OCR) | surya |
| `image_figure_heavy` | docling + figure detect | docling + figure crops | For figures | — |
| `form` | docling + pdfplumber | pdfplumber key:value | No | pdfplumber |
| `mixed_standard` | docling full | docling path | For figures | — |

---

## Libraries to Add

| Library | Size | Use case | Priority |
|---|---|---|---|
| `camelot-py[cv]` | small | Grid table extraction (table_dominant, poster tables) | HIGH |
| `mineru` | large | Comparison baseline, may replace docling for some types | MEDIUM |
| `paddleocr` | large | Multilingual OCR (Hindi text in ICMR headers) | LOW |

Install camelot:
```powershell
pip install camelot-py[cv]
```

---

## Post-Processing Needed per Type

### ALL types — add to Phase 8.5
```python
# Promote ALL-CAPS lines to ## headings (fixes 0-heading problem on GLM-OCR fallback)
def promote_allcaps_headings(text: str) -> str:
    lines = text.splitlines()
    out = []
    for line in lines:
        s = line.strip()
        # ALL-CAPS line, 4+ chars, not a table row, not a number
        if (s and s == s.upper() and len(s) > 3
                and not s.startswith('|') and not s.isdigit()):
            out.append(f"\n## {s}")
        else:
            out.append(line)
    return "\n".join(out)
```

### poster_flowchart — specific post-processing
- Collapse `RL` + `10-15ml/kg/hr` fragments that wrap across lines
- Detect "No Improvement / Improvement" pairs → format as indented branches
- Merge fragmented sentences (lines ending mid-sentence)

### text_multicolumn — specific post-processing  
- Re-sort elements by column then Y position (fixes docling reading order bug)
- Detect and merge footnote references
- Fix hyphenated line breaks

---

## Implementation Priority

```
1. Switch VISION_PRIMARY to qwen2.5vl:7b    10 min   (.cloak_local.json)
2. Add promote_allcaps_headings() to Phase 8.5  20 min  (postprocess.py)
3. Add camelot-py for table_dominant pages      1 hr   (ocr_tools.py + parser_agent.py)
4. Add column_count detection signal            2 hr   (page_profiler.py)
5. Add text_multicolumn column-aware sort       3 hr   (parser_agent.py)
6. Add two-stage structuring for poster fallback 2 hr  (parser_agent.py + vision_tools.py)
7. Expand page_type from 5 → 10 types          4 hr   (page_profiler.py + doc_profiler.py)
```

Items 1 and 2 give immediate wins on every ICMR STW. Items 3-7 are the bigger architectural work.
