# Poster Samples — Notes

## ICMR Standard Treatment Workflows (STWs) — already in this folder

All 10 PDFs currently in this folder are **ICMR Standard Treatment Workflows** published by the Indian Council of Medical Research. They are clinical decision flowchart posters.

**Format confirmed:**
- Size: 297mm × 576mm — custom tall poster (A3 width × ~double A3 height)
- Pages: 1 per file (single large page)
- Blocks: ~95 text blocks + ~69 image blocks per page
- Content: Clinical flowchart decision tree (boxes, arrows, criteria, drug dosages)
- Text layer: Present (digital PDF, not scanned) — 5,000–7,500 chars per page

**Why these are the hardest test case in the entire corpus:**
- Non-linear reading order — flowchart boxes are arranged spatially, not top-to-bottom
- 95 text blocks on one page — Docling receives no reading-order signal from page structure
- 69 image blocks — arrows, box borders, and decorative elements all registered as images
- No SectionHeaderItem — every clinical box is a TextItem at the same logical level
- Clinical decision trees cannot be faithfully represented as linear Markdown
- The "best" output is a structured list of decision nodes, not a prose document

**Expected parse quality:** Medium-Low (5–7/10). These will surface real pipeline gaps.

**Parsing strategy that works:**
- Phase 3: `full_page_extract()` via vision — the VLM sees the spatial layout and can describe it
- Vision output should list the decision nodes in reading order (top → down, following arrow flow)
- FORMAT step must convert the raw VLM output into a structured `## Section\n- criterion\n- criterion` format
- Do NOT expect GFM tables — flowchart nodes should become heading + bullet list blocks

---

## Additional poster types needed (manual download)

## Recommended sources

### Option 1 — F1000Research (open access posters)
Browse: https://f1000research.com/browse/posters
Click any poster → Download PDF
All are CC-BY licensed. Pick any with a clear multi-section layout.

### Option 2 — Zenodo (research posters)
Browse: https://zenodo.org/search?q=poster&f=resource_type%3Aposter
Filter by "Poster" resource type. Download any CC-licensed poster PDF.
Prefer: A0 size, landscape or portrait, multi-column grid layout.

### Option 3 — ESA / NASA posters
- ESA: https://www.esa.int/ESA_Multimedia/Search/poster (filter by Format=PDF)
- NASA: https://www.nasa.gov/news-and-reports/publications/

## What a good poster PDF looks like for testing

- Single large page (A0 or A1 format = 841×1189mm or 594×841mm)
- Multi-section grid (Introduction | Methods | Results | Conclusion boxes)
- Large title at top, author list below title
- Mix of text blocks, figures, and tables within sections
- Small body text (9–11pt equivalent in the PDF)

## Why poster parsing is hard

- Non-linear reading order — sections are spatial boxes, not a flow
- Text in different columns/boxes has no clear docling SectionHeader
- Very small text relative to page size → OCR quality risk
- Figures inside section boxes → caption detection challenge
- Docling may classify the whole poster as a single TextItem
