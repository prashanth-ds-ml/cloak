# CLOAK

## Context Aware Local Ollama Agentic Knowledge Parser

**CLOAK** is a local-first, Markdown-focused, agentic document parsing CLI/toolkit designed to convert PDFs and document images into clean, structured, confidence-scored Markdown using local tools, local OCR, and local Ollama models.

The name expands to:

> **Context Aware Local Ollama Agentic Knowledge Parser**

CLOAK is inspired by the strengths of systems like LandingAI Agentic Document Extraction and LlamaParse, but its core goal is different:

> Build a privacy-friendly, open-source, local-first parser that can run on modest hardware and still produce high-quality Markdown outputs with transparent confidence reporting.

---

# 1. Why CLOAK Exists

Most document parsing systems fail because they treat every PDF the same.

But real PDFs are messy:

- Clean digital PDFs
- Scanned PDFs
- Textbooks
- Research papers
- Multi-column documents
- Government reports
- Posters
- Forms
- Invoices
- Question papers
- Slide exports
- Legal documents
- Table-heavy reports
- Image-heavy medical reports
- Mixed PDFs with both digital and scanned pages

A single parser cannot handle all of these well.

CLOAK solves this by becoming a **parser orchestrator**, not just a parser.

Bad approach:

```text
PDF → Extract text → Save
```

CLOAK approach:

```text
PDF → Profile → Route → Parse → Verify → Repair → Validate → Save Markdown
```

---

# 2. Core Philosophy

CLOAK follows five principles.

## 2.1 Local-first

Documents stay on the user’s machine.

No cloud API is required for the default system.

## 2.2 Markdown-first

The primary output is readable, structured Markdown.

Markdown is better than raw JSON for:

- Human review
- Obsidian usage
- Future RAG chunking
- Long-form knowledge documents
- Preserving headings, sections, tables, and citations

## 2.3 Agentic routing

CLOAK does not blindly parse.

It decides:

- Which page is easy
- Which page is hard
- Which tool should be used
- Whether OCR is needed
- Whether vision parsing is needed
- Whether repair is needed

## 2.4 Confidence-aware parsing

Production-level quality does not mean the parser never fails.

It means the parser knows when it may have failed.

CLOAK must always answer:

```text
How confident am I in this page?
What tool parsed it?
What problems were detected?
Does this page need review?
```

## 2.5 Multi-tool verification

No single parser is trusted.

For difficult documents, CLOAK compares outputs from multiple tools before saving final Markdown.

---

# 3. Target Users

CLOAK is useful for:

- AI engineers
- RAG developers
- Researchers
- Students
- Legal-tech builders
- Medical AI builders
- Government document processors
- Obsidian/second-brain users
- Open-source local AI users
- People building document intelligence agents

---

# 4. Target Hardware

CLOAK should support multiple modes depending on hardware.

## 4.1 Lite Mode

Minimum:

```text
CPU only
8–16 GB RAM
No GPU required
```

Use cases:

- Clean text PDFs
- Simple reports
- Basic Markdown extraction
- No heavy vision model

## 4.2 Standard Mode

Recommended minimum:

```text
Intel i7 / Ryzen equivalent
16 GB RAM
NVIDIA RTX 3060 6 GB GPU
512 GB SSD
```

Example target machine:

```text
MSI Katana GF76
RTX 3060 6 GB
16 GB RAM
Intel i7
512 GB SSD
```

Capabilities:

- Text PDFs
- Table extraction
- OCR
- Selective vision parsing
- Markdown repair
- Confidence scoring

## 4.3 Pro Local Mode

Better local setup:

```text
24–32 GB RAM
8–12 GB NVIDIA GPU
1 TB SSD
```

Example:

```text
HP Omen 16
24 GB RAM
RTX 5050 8 GB GPU
1 TB SSD
```

Capabilities:

- Slower but more accurate parsing
- High-DPI OCR
- Vision verification
- Complex tables/forms
- More repair passes

---

# 5. What CLOAK Should Produce

For every input document, CLOAK should create:

```text
outputs/
└── document_name/
    ├── final.md
    ├── profile.json
    ├── page_report.json
    ├── confidence_report.json
    ├── low_confidence_pages.md
    ├── routing_plan.json
    ├── logs.txt
    ├── original.pdf
    ├── page_images/
    ├── extracted_images/
    ├── extracted_tables/
    └── debug/
```

## 5.1 final.md

The main clean Markdown output.

## 5.2 profile.json

Document-level profile.

## 5.3 page_report.json

Page-by-page parsing report.

## 5.4 confidence_report.json

Confidence scores and warnings.

## 5.5 low_confidence_pages.md

Human-review queue.

## 5.6 routing_plan.json

Records which tools/models were selected for each page.

## 5.7 logs.txt

Detailed execution logs.

---

# 6. Main System Architecture

```text
Input Folder / PDF
        ↓
Intake Manager
        ↓
Document Profiler
        ↓
Page Profiler
        ↓
Router Agent
        ↓
Parsing Tool Layer
        ↓
OCR Layer
        ↓
Vision Layer
        ↓
Markdown Builder
        ↓
Context Repair Agent
        ↓
Verification Agent
        ↓
Repair / Retry Agent
        ↓
Confidence Scorer
        ↓
Final Markdown Saver
```

---

# 7. Core Skills CLOAK Needs

## 7.1 PDF Understanding Skill

CLOAK must understand the type of PDF before parsing.

It should classify documents as:

```text
clean_text_pdf
scanned_pdf
mixed_pdf
textbook
research_paper
government_document
legal_document
invoice
form
poster
brochure
slide_deck
question_paper
table_heavy_document
image_heavy_document
multi_column_document
technical_manual
medical_report
```

Why this matters:

A textbook, poster, invoice, and scanned government circular all need different parsing strategies.

---

## 7.2 Document Profiling Skill

Document profiling happens before parsing.

CLOAK should detect:

```text
page_count
document_title
file_hash
file_size
metadata_available
text_layer_available
scanned_page_percentage
table_density
image_density
layout_complexity
language_or_languages
estimated_difficulty
recommended_mode
```

Example:

```yaml
document_type: government_guideline
page_count: 42
has_text_layer: true
scanned_pages: 6
table_heavy_pages: 9
image_heavy_pages: 4
difficulty: medium
recommended_mode: accurate
```

---

## 7.3 Page Profiling Skill

CLOAK must profile each page separately.

Page types:

```text
normal_text
multi_column
scanned
table_heavy
form_page
poster_page
diagram_page
equation_heavy
image_heavy
toc_page
index_page
references_page
question_page
legal_clause_page
cover_page
blank_page
```

Page metrics:

```text
text_length
word_count
image_count
table_likelihood
ocr_needed
vision_needed
rotation_detected
language_detected
difficulty_score
recommended_parser
recommended_model
```

Example:

```yaml
page: 12
type: table_heavy
text_length: 780
tables_detected: 2
images_detected: 1
ocr_needed: false
vision_needed: true
difficulty: medium
```

---

## 7.4 Routing Skill

The router decides how to parse each page.

Examples:

```text
Clean text page
→ PyMuPDF + Docling

Table page
→ pdfplumber + Camelot + Docling

Scanned page
→ image preprocessing + OCR

Broken scanned table
→ OCR + qwen2.5vl:7b

Poster page
→ qwen2.5vl:7b vision-first parsing

Research paper page
→ layout-aware parsing + multi-column correction
```

Routing questions:

```text
Which parser should handle this page?
Is OCR needed?
Is a vision model needed?
Should multiple tools be used?
Should the output be verified visually?
Should this page go to review?
```

---

## 7.5 Text Extraction Skill

CLOAK must extract normal digital text while preserving:

```text
headings
paragraphs
lists
numbered lists
footnotes
captions
references
page order
section order
```

---

## 7.6 Table Extraction Skill

CLOAK must handle:

```text
simple tables
bordered tables
borderless tables
multi-page tables
merged cells
financial tables
eligibility tables
medical dosage tables
schedule tables
comparison tables
```

Output should be Markdown tables when reliable.

Example:

```markdown
| Field | Value |
|---|---|
| Age | 18+ |
| Eligibility | Registered MSME |
```

When uncertain:

```markdown
> Table confidence: Low. Needs review.
```

---

## 7.7 OCR Skill

CLOAK needs OCR for:

```text
scanned books
old government PDFs
forms
receipts
low-quality scans
rotated pages
blurred documents
mixed-language pages
```

OCR should run only where needed, not on every page.

---

## 7.8 Image Preprocessing Skill

Before OCR or vision parsing, CLOAK should improve images using:

```text
deskewing
denoising
contrast enhancement
binarization
rotation correction
cropping
sharpening
page border detection
```

---

## 7.9 Vision Parsing Skill

CLOAK uses a local vision model only for hard pages.

Use vision for:

```text
forms
diagrams
posters
infographics
complex tables
bad OCR pages
image-only pages
charts
medical report screenshots
layout-heavy pages
```

Recommended local model:

```bash
ollama pull qwen2.5vl:7b
```

---

## 7.10 Context Awareness Skill

This is one of CLOAK’s main differentiators.

CLOAK should use previous and next pages to fix context.

Examples:

### Cross-page table

```text
Page 10: table starts
Page 11: table continues
```

CLOAK should merge or mark table continuation.

### Heading continuation

```text
Page 5: Treatment Guidelines
Page 6: continuation text
```

CLOAK should attach page 6 content to the correct heading.

### Figure reference

```text
Text says: See Figure 3
Figure appears later
```

CLOAK should preserve the relationship.

---

## 7.11 Markdown Formatting Skill

CLOAK’s final output should be clean Markdown.

It must preserve:

```text
headings
sections
subsections
tables
lists
captions
figures
source pages
confidence notes
references
```

Example:

```markdown
---
title: Hypertension Guidelines
source_file: hypertension.pdf
parser: cloak
confidence: 0.91
---

# Hypertension Guidelines

## Diagnosis

Diagnosis is based on repeated blood pressure measurements.

> Source: page 4  
> Confidence: High

## Treatment Table

| Treatment | Recommendation |
|---|---|
| Lifestyle changes | First-line |
| Medication | If BP remains high |

> Source: page 7  
> Confidence: Medium
```

---

## 7.12 Markdown Validation Skill

CLOAK should validate:

```text
only one H1
heading hierarchy is correct
no empty headings
tables are valid Markdown
source pages are present
paragraphs are readable
repeated headers/footers removed
OCR garbage detected
hallucinated sections avoided
```

---

## 7.13 Repair and Retry Skill

If a page fails, CLOAK should retry using another method.

Retry strategies:

```text
try another parser
increase image DPI
run OCR again
use another OCR engine
send page to vision model
repair Markdown formatting
mark page as low confidence
```

---

## 7.14 Confidence Scoring Skill

Each page should get a confidence score.

Example:

```yaml
page: 12
confidence: 0.71
status: medium
issues:
  - table structure uncertain
  - OCR text partially noisy
```

Confidence levels:

```text
High → safe output
Medium → usable but marked
Low → needs review
Failed → manual review required
```

---

## 7.15 Logging and Audit Skill

CLOAK should record:

```text
which parser was used
which model was used
which pages failed
which pages needed OCR
which pages needed vision
confidence score per page
warnings
errors
retry attempts
```

This is essential for production-level trust.

---

# 8. Tools CLOAK Needs

## 8.1 PDF Inspection and Extraction

### PyMuPDF

Use for:

```text
opening PDFs
counting pages
extracting text
extracting blocks
rendering pages as images
extracting images
reading metadata
```

Core functions:

```python
open_pdf()
get_page_count()
extract_text_pymupdf()
extract_blocks_pymupdf()
render_page_to_image()
extract_images_from_page()
read_pdf_metadata()
```

### pdfplumber

Use for:

```text
layout-aware text extraction
simple table extraction
detecting table-like structures
understanding page layout
```

Core functions:

```python
extract_text_pdfplumber()
extract_tables_pdfplumber()
detect_table_regions()
extract_words_with_positions()
```

### Docling

Use for:

```text
structured document conversion
reading order
headings
tables
figures
RAG-friendly extraction
```

Core functions:

```python
extract_with_docling()
detect_document_structure()
convert_docling_to_markdown()
```

### pypdf

Use for:

```text
fallback text extraction
metadata extraction
PDF page operations
```

Core functions:

```python
extract_text_pypdf()
read_pdf_info()
```

---

# 9. Table Extraction Tools

## 9.1 pdfplumber

Best for many normal text-based tables.

## 9.2 Camelot

Use for:

```text
text-based tables
financial tables
structured tables
```

Core functions:

```python
extract_tables_camelot()
convert_camelot_table_to_markdown()
```

## 9.3 Tabula

Optional fallback for table extraction.

## 9.4 Vision model fallback

For complex scanned or visual tables, use qwen2.5vl:7b.

---

# 10. OCR Tools

## 10.1 Surya OCR

Use for:

```text
high-quality OCR
layout analysis
reading order
equations
tables
multilingual documents
```

## 10.2 PaddleOCR

Use for:

```text
general OCR
multilingual OCR
scanned PDFs
forms
receipts
```

## 10.3 Tesseract OCR

Use as a lightweight fallback.

OCR functions:

```python
run_tesseract_ocr()
run_paddle_ocr()
run_surya_ocr()
compare_ocr_outputs()
clean_ocr_text()
detect_ocr_garbage()
```

---

# 11. Image Processing Tools

## 11.1 OpenCV

Use for:

```text
deskewing
denoising
contrast enhancement
binarization
rotation correction
cropping
detecting page borders
```

Core functions:

```python
deskew_image()
denoise_image()
increase_contrast()
binarize_image()
detect_rotation()
crop_page_content()
```

## 11.2 Pillow

Use for:

```text
image loading
saving page screenshots
resizing
format conversion
```

Core functions:

```python
load_image()
save_image()
resize_image()
convert_image_format()
```

---

# 12. Ollama Models

## 12.1 Fast Local Text Model

```bash
ollama pull llama3.2:3b
```

Use for:

```text
document type classification
page type classification
quick heading cleanup
simple summaries
small routing decisions
```

## 12.2 Main Vision Model

```bash
ollama pull qwen2.5vl:7b
```

Use for:

```text
complex page understanding
forms
tables
posters
scanned pages
diagram explanation
visual verification
```

## 12.3 Markdown/Repair Model

```bash
ollama pull qwen2.5-coder:7b
```

Use for:

```text
Markdown repair
table cleanup
format validation
structured output repair
schema-like corrections
```

## 12.4 Optional Embedding Model

Not needed for parsing-only phase, but useful later.

```bash
ollama pull nomic-embed-text
```

Use later for:

```text
semantic search
duplicate detection
chunking
RAG indexing
```

---

# 13. Markdown Tools

## 13.1 mdformat

Use for:

```text
formatting Markdown consistently
clean spacing
valid Markdown style
```

Function:

```python
format_markdown()
```

## 13.2 markdown-it-py

Use for:

```text
parsing Markdown
checking heading structure
validating Markdown blocks
```

Functions:

```python
parse_markdown_ast()
validate_heading_hierarchy()
detect_empty_headings()
```

## 13.3 python-frontmatter

Use for YAML metadata at the top of Markdown files.

Example:

```markdown
---
title: Document Title
source_file: sample.pdf
confidence: 0.91
---
```

Functions:

```python
add_frontmatter()
read_frontmatter()
update_frontmatter()
```

---

# 14. Text Cleaning Tools

## 14.1 ftfy

Use for:

```text
fixing broken Unicode
cleaning weird characters
repairing OCR text issues
```

Function:

```python
fix_unicode_text()
```

## 14.2 regex

Use for:

```text
header/footer removal
duplicate line detection
hyphenation repair
page number cleanup
spacing normalization
```

Functions:

```python
remove_repeated_headers()
remove_repeated_footers()
fix_hyphenated_words()
remove_duplicate_lines()
normalize_spacing()
```

## 14.3 rapidfuzz

Use for:

```text
duplicate detection
near-duplicate paragraph detection
header/footer similarity detection
```

Functions:

```python
detect_duplicate_paragraphs()
detect_repeated_headers_footers()
```

---

# 15. Validation Tools

## 15.1 Pydantic

Use for validating:

```text
profile outputs
page reports
routing plans
confidence reports
metadata
```

Functions:

```python
validate_profile_schema()
validate_page_report()
validate_routing_plan()
validate_metadata()
```

## 15.2 jsonschema

Optional schema validation tool.

---

# 16. CLI and Developer Tools

## 16.1 Typer

Use for CLI commands.

Example commands:

```bash
cloak profile ./pdfs
cloak parse ./pdfs --mode accurate
cloak parse file.pdf --vision selective
cloak inspect output/page_report.json
cloak validate output/final.md
```

Functions:

```python
cli_profile()
cli_parse()
cli_validate()
cli_inspect()
```

## 16.2 Rich

Use for:

```text
beautiful CLI logs
progress bars
tables
warnings
colored output
```

Functions:

```python
show_progress()
print_profile_summary()
print_page_report()
print_errors()
```

## 16.3 Loguru or standard logging

Use for:

```text
clean logs
error tracking
debug files
```

---

# 17. Main CLOAK Functions

## 17.1 Intake Functions

```python
scan_folder()
find_pdf_files()
create_output_dir()
copy_original_file()
calculate_file_hash()
```

## 17.2 Profiling Functions

```python
profile_document()
profile_page()
detect_text_layer()
detect_scanned_page()
detect_tables()
detect_images()
detect_multicolumn_layout()
detect_document_type()
estimate_difficulty()
```

## 17.3 Routing Functions

```python
create_routing_plan()
choose_text_parser()
choose_table_parser()
choose_ocr_engine()
choose_vision_model()
decide_retry_strategy()
```

## 17.4 Extraction Functions

```python
extract_with_pymupdf()
extract_with_pdfplumber()
extract_with_docling()
extract_tables()
extract_images()
extract_captions()
```

## 17.5 OCR Functions

```python
preprocess_page_image()
run_ocr()
compare_ocr_results()
clean_ocr_text()
detect_ocr_noise()
```

## 17.6 Vision Functions

```python
render_page_for_vision()
send_page_to_qwen_vl()
extract_visual_page_to_markdown()
verify_markdown_against_page_image()
```

## 17.7 Markdown Functions

```python
build_page_markdown()
normalize_headings()
convert_tables_to_markdown()
add_page_source()
merge_page_markdown()
format_markdown()
save_final_markdown()
```

## 17.8 Validation Functions

```python
validate_markdown_structure()
validate_heading_hierarchy()
validate_tables()
check_source_pages()
detect_repeated_content()
detect_empty_sections()
```

## 17.9 Repair Functions

```python
retry_with_alternate_parser()
retry_with_higher_dpi()
retry_with_ocr()
retry_with_vision()
repair_markdown()
mark_low_confidence()
```

## 17.10 Report Functions

```python
save_profile_report()
save_page_report()
save_confidence_report()
save_low_confidence_report()
save_logs()
```

---

# 18. PDF Scenarios and Edge Cases

## 18.1 Clean Text PDFs

Examples:

```text
reports
manuals
digital books
```

Risks:

```text
repeated headers
footers
bad line breaks
broken headings
```

Best route:

```text
PyMuPDF + Docling + Markdown cleanup
```

---

## 18.2 Textbooks

Risks:

```text
chapter hierarchy loss
figures detached from captions
exercises mixed with content
index/reference noise
```

Needed:

```text
heading detection
caption extraction
exercise formatting
context continuity
```

---

## 18.3 Research Papers

Risks:

```text
multi-column reading order issues
abstract/body confusion
references clutter
figure/table captions lost
```

Needed:

```text
multi-column detection
section recognition
caption preservation
reference section handling
```

---

## 18.4 Multi-column Documents

Risks:

```text
left column and right column mixed incorrectly
sidebars inserted into main text
footnotes inserted mid-paragraph
```

Needed:

```text
layout-aware parsing
reading-order validation
vision verification for hard pages
```

---

## 18.5 Poster / Infographic PDFs

Risks:

```text
non-linear layout
visual blocks
large titles
arrows and callouts
icons with meaning
```

Needed:

```text
vision-first extraction
layout summary
section grouping
confidence notes
```

---

## 18.6 Table-heavy PDFs

Risks:

```text
broken rows
missing columns
merged cells lost
multi-page tables split badly
```

Needed:

```text
pdfplumber
Camelot
Docling
vision fallback
manual review flags
```

---

## 18.7 Scanned PDFs

Risks:

```text
OCR errors
rotation
blur
low contrast
noise
missing words
```

Needed:

```text
image preprocessing
OCR
higher DPI retry
vision verification
```

---

## 18.8 Government Documents

Risks:

```text
repeated headers/footers
stamps
signatures
circular numbers
tables
mixed scan quality
```

Needed:

```text
noise removal
page citations
OCR fallback
signature/stamp awareness
```

---

## 18.9 Forms

Risks:

```text
label-value mismatch
checkboxes lost
blank fields confused as missing data
```

Needed:

```text
OCR
vision extraction
key-value mapping
checkbox detection
```

---

## 18.10 Question Papers

Risks:

```text
question numbers lost
options merged
formulas broken
marks lost
```

Needed:

```text
question-aware formatting
numbering preservation
option preservation
```

---

## 18.11 Legal Documents

Risks:

```text
clause hierarchy corruption
numbering loss
sub-clause confusion
```

Needed:

```text
strict numbering preservation
heading hierarchy validation
```

---

## 18.12 Medical Reports

Risks:

```text
abnormal values missed
tables broken
units lost
reference ranges lost
image-based reports
```

Needed:

```text
table extraction
unit preservation
confidence flags
vision fallback
```

---

## 18.13 Slide Deck PDFs

Risks:

```text
fragmented text boxes
bad reading order
icons/diagrams lost
```

Needed:

```text
layout parsing
vision verification
slide-wise Markdown
```

---

## 18.14 Bilingual / Multilingual PDFs

Risks:

```text
language mixing
OCR mistakes
translation-like cleanup errors
```

Needed:

```text
language detection
multilingual OCR
no aggressive cleanup
```

---

## 18.15 Corrupt / Password-Protected PDFs

Risks:

```text
file cannot open
text extraction blocked
pages corrupted
```

Needed:

```text
safe failure
clear error message
unsupported file report
```

---

# 19. Parsing Modes

## 19.1 Fast Mode

Command:

```bash
cloak parse file.pdf --mode fast
```

Uses:

```text
PyMuPDF
basic cleanup
basic Markdown
```

Best for:

```text
clean digital PDFs
quick previews
```

---

## 19.2 Balanced Mode

Command:

```bash
cloak parse file.pdf --mode balanced
```

Uses:

```text
PyMuPDF
pdfplumber
Docling
basic validation
```

Best default mode.

---

## 19.3 Accurate Mode

Command:

```bash
cloak parse file.pdf --mode accurate
```

Uses:

```text
multi-parser comparison
OCR when needed
selective vision fallback
validation
repair loop
```

Best for production-quality parsing.

---

## 19.4 Forensic Mode

Command:

```bash
cloak parse file.pdf --mode forensic
```

Uses:

```text
high DPI rendering
multiple OCR passes
vision verification
full logs
low-confidence review queue
```

Best when speed does not matter.

---

# 20. Confidence Scoring System

CLOAK confidence should be computed from multiple signals.

Signals:

```text
text extraction length
OCR confidence
parser agreement
table extraction success
Markdown validation result
vision verification result
repeated text detection
empty section detection
heading quality
```

Example scoring:

```text
0.90–1.00 → High
0.70–0.89 → Medium
0.40–0.69 → Low
0.00–0.39 → Failed / Review Required
```

Example report:

```yaml
page: 8
confidence: 0.64
status: low
issues:
  - table rows may be incomplete
  - OCR confidence below threshold
  - vision verification found missing content
recommended_action: human_review
```

---

# 21. Markdown Quality Rules

CLOAK should enforce:

```text
one H1 per document
no heading jumps
page citations preserved
tables formatted correctly
captions preserved
lists preserved
repeated headers removed
repeated footers removed
OCR garbage flagged
low-confidence sections marked
```

Good page output:

```markdown
## Eligibility Criteria

The applicant must be registered under the relevant authority.

| Requirement | Details |
|---|---|
| Registration | Required |
| Age | Not specified |

> Source: page 12  
> Confidence: High
```

Low-confidence output:

```markdown
## Table: Dosage Schedule

| Drug | Dose | Frequency |
|---|---|---|
| ... | ... | ... |

> Source: page 21  
> Confidence: Low  
> Note: Table reconstructed from OCR and may need review.
```

---

# 22. Prompt Templates Needed

## 22.1 Vision-to-Markdown Prompt

Purpose:

Convert a page image into Markdown.

Prompt should ask model to:

```text
preserve headings
preserve tables
preserve labels and values
avoid hallucination
mark unclear content
return Markdown only
```

## 22.2 Markdown Repair Prompt

Purpose:

Fix broken Markdown without changing meaning.

Prompt should ask model to:

```text
fix heading hierarchy
fix table formatting
remove duplicate lines
preserve source page notes
not invent content
```

## 22.3 Verification Prompt

Purpose:

Compare image and Markdown.

Prompt should ask:

```text
Is any visible text missing?
Are tables correct?
Are headings correct?
Is any content hallucinated?
Return confidence and issues.
```

## 22.4 Router Prompt

Purpose:

Classify pages when heuristics are uncertain.

Prompt should return:

```text
page_type
recommended_tools
vision_needed
ocr_needed
difficulty
reason
```

---

# 23. CLI Design

## 23.1 Profile PDFs

```bash
cloak profile ./pdfs
```

Creates:

```text
profile.json
folder_summary.csv
```

## 23.2 Parse One PDF

```bash
cloak parse ./sample.pdf --mode balanced
```

## 23.3 Parse Folder

```bash
cloak parse ./pdfs --mode accurate
```

## 23.4 Validate Output

```bash
cloak validate ./outputs/sample/final.md
```

## 23.5 Inspect Report

```bash
cloak inspect ./outputs/sample/page_report.json
```

## 23.6 Review Low-confidence Pages

```bash
cloak review ./outputs/sample/low_confidence_pages.md
```

## 23.7 Clean Output

```bash
cloak clean ./outputs/sample/final.md
```

---

# 24. Suggested Project Structure

```text
cloak/
├── cloak/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── intake/
│   │   ├── scanner.py
│   │   └── hashing.py
│   ├── profiling/
│   │   ├── document_profiler.py
│   │   └── page_profiler.py
│   ├── routing/
│   │   ├── router.py
│   │   └── rules.py
│   ├── parsers/
│   │   ├── pymupdf_parser.py
│   │   ├── pdfplumber_parser.py
│   │   ├── docling_parser.py
│   │   └── table_parser.py
│   ├── ocr/
│   │   ├── preprocess.py
│   │   ├── tesseract_ocr.py
│   │   ├── paddle_ocr.py
│   │   └── surya_ocr.py
│   ├── vision/
│   │   ├── ollama_client.py
│   │   └── qwen_vl_parser.py
│   ├── markdown/
│   │   ├── builder.py
│   │   ├── formatter.py
│   │   └── validator.py
│   ├── verification/
│   │   ├── compare.py
│   │   └── confidence.py
│   ├── repair/
│   │   ├── retry.py
│   │   └── markdown_repair.py
│   ├── reports/
│   │   ├── profile_report.py
│   │   ├── page_report.py
│   │   └── confidence_report.py
│   └── utils/
│       ├── logging.py
│       └── files.py
├── prompts/
│   ├── vision_to_markdown.md
│   ├── markdown_repair.md
│   ├── verify_page.md
│   └── router.md
├── tests/
├── examples/
├── docs/
├── pyproject.toml
└── README.md
```

---

# 25. Installation Stack

## 25.1 First working version

```bash
pip install pymupdf pdfplumber pypdf pillow opencv-python pydantic rich typer mdformat python-frontmatter ftfy rapidfuzz markdown-it-py
```

## 25.2 OCR tools

```bash
pip install pytesseract paddleocr
```

Surya OCR can be added after basic OCR integration.

## 25.3 Table tools

```bash
pip install camelot-py
```

## 25.4 Ollama models

```bash
ollama pull llama3.2:3b
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5vl:7b
```

---

# 26. Benchmarking Strategy

CLOAK should include a small benchmark set.

Test documents:

```text
clean text PDF
research paper
textbook chapter
government circular
table-heavy PDF
scanned PDF
poster PDF
form PDF
question paper
legal contract
medical report
```

Metrics:

```text
text completeness
table correctness
heading quality
reading order correctness
OCR quality
Markdown validity
confidence calibration
manual review rate
```

---

# 27. MVP Roadmap

## v0.1 — Profiler Only

Features:

```text
scan folder
profile PDFs
classify pages
generate routing plan
save profile reports
```

Commands:

```bash
cloak profile ./pdfs
```

---

## v0.2 — Basic Markdown Parser

Features:

```text
PyMuPDF extraction
pdfplumber extraction
basic Markdown builder
page citations
logs
```

Command:

```bash
cloak parse ./pdfs --mode fast
```

---

## v0.3 — Table Handling

Features:

```text
table detection
table extraction
table-to-Markdown conversion
low-confidence table flags
```

---

## v0.4 — OCR Support

Features:

```text
page rendering
image preprocessing
OCR extraction
OCR confidence report
```

---

## v0.5 — Vision Fallback

Features:

```text
qwen2.5vl integration
vision-to-Markdown
visual page parsing
vision verification
```

---

## v0.6 — Verification and Repair

Features:

```text
multi-parser comparison
Markdown validation
retry loop
confidence scoring
low-confidence review queue
```

---

## v1.0 — Production Local CLI

Features:

```text
stable CLI
config file
parsing modes
complete reports
tests
sample benchmark dataset
documentation
open-source release
```

---

# 28. What Makes CLOAK Open-source Worthy

CLOAK’s differentiators:

```text
local-first
Ollama-first
Markdown-first
hardware-aware
agentic profiler/router
confidence reports
low-confidence review queue
transparent tool decisions
Obsidian-friendly output
RAG-ready later, but not RAG-dependent
```

Many tools parse documents.

CLOAK should clearly explain:

```text
what was parsed
how it was parsed
which tool was used
where confidence is low
which pages need review
what failed and why
```

That transparency is its biggest strength.

---

# 29. Final Definition

CLOAK is:

> A local-first, context-aware, Ollama-powered, agentic PDF-to-Markdown parser that profiles, routes, parses, verifies, repairs, and confidence-scores documents for high-quality knowledge extraction on modest hardware.

Its first goal is not RAG.

Its first goal is:

> Produce clean, trustworthy, reviewable Markdown from messy PDFs.

RAG comes later.

Parsing quality comes first.

