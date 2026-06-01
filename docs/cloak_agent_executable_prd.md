# CLOAK Agent-Executable PRD

**Project Name:** CLOAK
**Expansion:** Context Aware Local Ollama Agentic Knowledge Parser
**Project Type:** Local-first, Docling-first, agentic document parsing CLI/toolkit
**Primary Output:** Clean, structured, confidence-scored Markdown
**Secondary Outputs:** JSON profiles, routing plan, confidence report, human review queue, debug artifacts
**Target Builder:** Opus (orchestrator) + Sonnet (implementation agent), human in the loop

---

## 0. How to Use This PRD

This PRD is **agent-executable** and **continuation-aware**.

- Sessions 1–13 of CLOAK are already built. Read §8 (Current Implementation State) before planning anything.
- Locked decisions (D1–D32) must not be changed without explicit user approval. Read §19 before touching any pipeline logic.
- Anti-patterns are listed in §20. They represent real failures from Sessions 1–13.
- The next build targets are in §28, ordered by priority. Start there.

**Opus role:** Read all docs (this PRD + PROGRESS.md + MODULES.md + DECISIONS.md). Understand current state. Decompose the next build target into atomic Sonnet tasks. Verify each task's output matches this PRD's spec before moving on.

**Sonnet role:** Implement one atomic task at a time. After every code change, output exact CLI commands the user must run to verify. Wait for user confirmation before proceeding to the next task. Never claim success without a reproducible command.

---

## 1. Product Vision

CLOAK is a local-first, Markdown-focused, agentic document parsing toolkit designed to convert PDFs and document images into clean, structured, confidence-scored Markdown using local tools, local OCR, Docling, and local Ollama models.

CLOAK exists because real-world documents are messy.

A single extraction method is not enough for:

- clean digital PDFs
- scanned PDFs
- mixed PDFs
- research papers
- textbooks
- government reports
- legal documents
- medical reports
- question papers
- slide exports
- table-heavy reports
- image-heavy documents
- posters, forms, invoices
- bilingual or multilingual documents

**CLOAK approach:**

```text
PDF → Profile → Docling-first parse → Verify → Fallback/Repair → Validate → Score → Save Markdown
```

---

## 2. Product Positioning

CLOAK is a **Docling-first local agentic quality layer**.

```text
Docling = primary parser and document structure engine
CLOAK = profiler, orchestrator, verifier, repairer, confidence layer, and CLI workflow
Specialized tools = fallback and verification helpers
```

---

## 3. Core Principles

### 3.1 Local-first
No cloud API required for the core workflow.

### 3.2 Docling-first
Docling is the default parser. Docling's element map (SectionHeaderItem, TableItem, FigureItem, FootnoteItem, TextItem) drives extraction — not pdfplumber heuristics.

### 3.3 Markdown-first
`final.md` is the primary output. Human-readable, RAG-ready, Obsidian-compatible.

### 3.4 Confidence-aware
Every document and page must have confidence reporting. CLOAK must never silently fail.

### 3.5 Agentic, but not LLM-everywhere
Use deterministic tools whenever possible. Use local models only where judgment, repair, or visual understanding is required.

### 3.6 Human-in-the-loop development
After every implementation task, provide exact manual CLI commands. Include expected outputs. Ask user to paste results back.

---

## 4. Non-Goals

CLOAK is not:
- a cloud SaaS product or hosted API
- a replacement for Docling
- a handwriting-first OCR product
- a RAG system or vector database ingestion engine by default
- a fully autonomous second-brain system
- a guaranteed diagnostic or legal compliance parser

---

## 5. Target Users

AI engineers, RAG developers, researchers, students, legal-tech builders, medical AI builders, government document processors, Obsidian/second-brain users, open-source local AI users.

---

## 6. Target Hardware Modes

### 6.1 Lite Mode
```text
CPU only · 8–16 GB RAM · No GPU
```
Use cases: clean text PDFs, simple reports, basic Markdown extraction.

### 6.2 Standard Mode
```text
16 GB RAM · NVIDIA RTX 3060 6 GB GPU optional
```
Use cases: digital PDFs, research papers, textbooks, table extraction, selective OCR.

### 6.3 Pro Local Mode (current dev hardware)
```text
24–32 GB RAM · 8–12 GB NVIDIA GPU · 1 TB SSD
```
RTX 5050 8 GB VRAM + 24 GB RAM — all three pipeline models fit in VRAM simultaneously. Gemma4 (Phase 9) uses CPU+GPU split after pipeline teardown.

---

## 7. Supported Document Types

```text
clean_text_pdf, scanned_pdf, mixed_pdf, research_paper, textbook,
government_document, legal_document, medical_report, lab_report,
invoice, receipt, form, question_paper, answer_sheet, slide_deck,
poster, brochure, technical_manual, financial_report,
table_heavy_document, image_heavy_document, multi_column_document,
bilingual_document, multilingual_document, handwritten_document
```

---

## 8. Current Implementation State

> **Read this before planning any task.**

Sessions 1–13 are complete. The pipeline is fully functional and has been validated on `stemi.pdf`.

### 8.1 What is done

| Stage | Name | Status | Notes |
|---|---|---|---|
| 0 | Project Scaffold | **Done** | pyproject.toml, venv, config.py, folder structure |
| 1 | Doctor Command | **Missing** | `cloak doctor` not yet implemented |
| 2 | Intake + File Profiler | **Partial** | Intake done; file_profile.json not produced |
| 3 | Basic Page Profiler | **Done** | PageProfile (7 fields), DocProfile (5 fields) |
| 4 | Document Profiler + Aggregation | **Done** | DocProfile + ParsePlan (D28) |
| 5 | Routing Plan | **Done** | ParsePlan in memory — not yet serialized to JSON |
| 6 | Docling Parser Wrapper | **Done + Surpassed** | Full element-map extraction (D29) |
| 7 | Markdown Builder | **Done** | No YAML frontmatter yet |
| 8 | Markdown Validator | **Missing** | `cloak validate` not implemented |
| 9 | Confidence Scorer | **Partial** | `_confidence.md` written; no JSON output |
| 10 | Human Review Queue | **Missing** | No `human_review_queue.json` or `low_confidence_pages.md` |
| 11 | Table Fallback | **Partial** | pdfplumber done; Camelot excluded (D25) |
| 12 | OCR Fallback | **Done** | Surya primary + Tesseract fallback (D30) |
| 13 | Markdown Repair Agent | **Done** | qwen3:8b (not qwen2.5-coder — D25) |
| 14 | Vision Fallback | **Done** | qwen3-vl:4b (D15) |

### 8.2 Current file structure

```
cloak/
├── config.py                          ← all tunable constants
├── cli/
│   ├── main.py                        ← typer CLI: parse, status, list, clean
│   └── system_check.py               ← hardware probe, startup screen
├── profiling/
│   ├── page_profiler.py              ← PageProfile, RouteMap
│   └── doc_profiler.py              ← DocProfile, ParsePlan (D28)
├── extraction/
│   ├── pdf_tools.py                  ← PageData, load_pages, spatial_sort
│   └── ocr_tools.py                  ← Surya primary, Tesseract fallback (D30)
├── vision/
│   └── vision_tools.py               ← full_page_extract, region_describe, judge_quality
├── quality/
│   ├── quality_judge.py              ← PageScore, content+structure scoring (D31)
│   └── deep_review.py               ← Phase 9, gemma4:latest (D27)
├── orchestration/
│   ├── model_router.py               ← phase-based routing, total-memory (D14/D32)
│   ├── context_manager.py           ← history compression (D6)
│   └── parser_agent.py              ← 9-phase orchestrator
├── registry.py                        ← workspace-local JSON registry for cloak list
└── ingestion/                         ← legacy read-only, do not modify
    ├── pdf_extractor.py
    ├── pdf_classifier.py
    ├── vision.py
    └── markdown_builder.py
```

### 8.3 Current output paths (pre-refactor)

```text
data/raw/{specialty}/{stem}.pdf          ← input
data/markdown/{specialty}/{stem}.md      ← final markdown
data/markdown/{specialty}/{stem}_confidence.md
data/markdown/{specialty}/{stem}_review.md
data/markdown/{specialty}/{stem}_images/
```

> **Note:** The output path refactor (→ `data/outputs/{specialty}/{doc_name}/`) is a planned stage. See §15 and §28. Do not change output paths until that stage is explicitly activated.

### 8.4 Current model stack

| Role | Model | VRAM | Status |
|---|---|---|---|
| Orchestrator + repair | `qwen3:8b` | 5.2 GB | Required |
| Vision primary (figures + judge) | `qwen2.5vl:7b` | 7.3 GB | Required |
| Vision fallback | `qwen3-vl:4b` | 3.3 GB | Optional |
| Deep review (Phase 9) | `gemma4:latest` | 9.6 GB | Optional |

### 8.5 Current CLI commands

```powershell
cloak                              # startup screen
cloak parse <pdf>                  # full pipeline (includes Phase 9)
cloak parse <pdf> --no-review      # skip Phase 9
cloak parse <pdf> --dry-run        # list pages without running
cloak parse <dir>                  # parse all PDFs in directory
cloak status                       # hardware + model status
cloak list                         # show parsed documents (registry)
cloak clean                        # remove data/markdown/ outputs (with confirmation)
cloak clean --yes                  # clean without confirmation
```

---

## 9. High-Level System Architecture

```text
Input PDF / Folder
      ↓
Phase 0  Intake Manager
      ↓
Phase 1  Docling Layout Pass → Element Map → DocProfile → ParsePlan
      ↓
Phase 2  Model Staging (from ParsePlan.model_tier)
      ↓
Phase 3  Element-Aware Extraction (D29)
      ↓
Phase 4  FORMAT Session (qwen3:8b — once)
      ↓
Phase 5  Judge Session (vision model — sampled)
      ↓
Phase 6  Patch Session (qwen3:8b) → back to Phase 5 until threshold or max rounds
      ↓
Phase 8  Output → final.md + confidence_report + flagged + images
      ↓
         model_router.teardown_pdf()
      ↓
Phase 9  Deep Review (gemma4:latest — after teardown)
      ↓
Registry update
```

---

## 10. Agentic Parsing Loop

```text
Observe:  Profile file, document, and pages.
Decide:   Choose model tier, round budget, sample rate (from ParsePlan).
Act:      Run Docling element-map extraction + selected tools.
Evaluate: Judge sampled pages — content score + structural fidelity score.
Repair:   qwen3:8b fills gaps if score < threshold.
Finalize: Save best round, write JSON reports, run Phase 9 review.
```

---

## 11. Core Agents and Components

### 11.1 Intake Agent
**Needs model:** No
**Tools:** pathlib, shutil, hashlib, PyMuPDF
**Responsibilities:** validate PDF, copy original, calculate hash, create output directory

---

### 11.2 Profiler Agent
**Needs model:** No
**Tools:** Docling metadata, PyMuPDF, pdfplumber, Pillow
**Responsibilities:** file profiling, page classification, DocProfile, ParsePlan, OCR-need prediction, table/image risk detection

---

### 11.3 Router Agent
**Needs model:** No (rules-based; ParsePlan drives all routing)
**Responsibilities:** choose extraction strategy per element type, generate routing_plan.json

---

### 11.4 Docling Executor Agent
**Needs model:** No separate LLM (Docling's layout model runs internally)
**Responsibilities:** run docling layout analysis pass, produce element map per page, extract SectionHeaders as headings, Tables as markdown, FigureItem bboxes for vision, FootnoteItems collected per section, discard PageHeader/PageFooter

---

### 11.5 Fallback Tool Agent
**Needs model:** No
**Tools (in priority order):**
1. Docling
2. PyMuPDF
3. pdfplumber
4. Surya OCR
5. Tesseract OCR
6. Pillow (image preprocessing)

> Note: Camelot excluded (D25). PaddleOCR excluded (D22). See §19.

---

### 11.6 Verification Agent
**Needs model:** No (deterministic)
**Tools:** regex, markdown-it-py, Pydantic
**Responsibilities:** validate Markdown syntax, detect broken tables, empty headings, repeated headers/footers, OCR garbage

---

### 11.7 Markdown Repair Agent
**Needs model:** Yes — `qwen3:8b`
**Responsibilities:** fill content gaps, normalize headings, fix bullet lists, never invent content, preserve page markers

---

### 11.8 Vision Agent
**Needs model:** Yes — `qwen2.5vl:7b` (primary) / `qwen3-vl:4b` (fallback)
**Role:** figure description + quality judge + patches only (D29)
**Not for:** text layout, heading extraction, table parsing

---

### 11.9 Confidence Agent
**Needs model:** No
**Responsibilities:** compute content_score (0.7 weight) + structure_score (0.3 weight) → combined PageScore; assign High/Medium/Low; write confidence_report.json

---

### 11.10 Review Agent
**Needs model:** No
**Responsibilities:** create low_confidence_pages.md, create human_review_queue.json, group by review type, include score + reason per page

---

### 11.11 Registry
**Needs model:** No
**Location:** `cloak/registry.py`
**Responsibilities:** track all parsed documents, store status (done/flagged/error/processing), last score, output paths; powers `cloak list`

---

## 12. Docling-First Routing Philosophy

### Route types

```text
docling_only              ← digital PDF, text_rich pages
docling_plus_pdfplumber   ← tables requiring char-level fidelity
docling_plus_surya        ← scanned pages
docling_plus_vision       ← figure-heavy pages
docling_repair_required   ← low-quality docling output detected
manual_review             ← very low confidence, unresolvable
skip_blank                ← blank pages
```

### Tool priority order (locked)

```text
1. Docling (element-map extraction)
2. PyMuPDF (text blocks, region crops)
3. pdfplumber (tables, character-level text)
4. Surya OCR (scanned pages — primary)
5. Tesseract OCR (scanned pages — fallback)
6. qwen2.5vl:7b (figures, quality judge)
7. qwen3-vl:4b (vision fallback)
8. Human review
```

---

## 13. CLOAK Profiler — v1 Spec (Minimal First)

The full 25-sub-profiler spec is the long-term target. **Build v1 first.** v2+ extend incrementally.

### 13.1 File Profiler (v1 — new)

Produces `file_profile.json`.

```json
{
  "file_name": "stemi.pdf",
  "file_path": "data/raw/cardiology/stemi.pdf",
  "file_size_bytes": 2048000,
  "file_hash_sha256": "abc123...",
  "is_pdf": true,
  "is_corrupt": false,
  "is_encrypted": false,
  "password_required": false,
  "page_count": 44,
  "pdf_version": "1.6",
  "creator": "Adobe InDesign",
  "producer": "Adobe PDF Library",
  "author": "",
  "title": "Management of STEMI",
  "creation_date": "2024-01-15",
  "modification_date": "2024-01-20"
}
```

**Tools:** PyMuPDF (`doc.metadata`), `hashlib.sha256`, `pathlib.Path.stat()`

---

### 13.2 Page Profiler (v1 — extend existing)

Extends current `PageProfile` (7 fields) with 3 new risk fields. Produces `page_profiles.json` (array of per-page objects).

```json
[
  {
    "page_num": 0,
    "text_length": 1842,
    "image_area_ratio": 0.12,
    "table_count": 2,
    "page_type": "table_heavy",
    "needs_ocr": false,
    "needs_vision": false,
    "table_risk": "medium",
    "ocr_risk": "low",
    "reading_order_risk": "low"
  }
]
```

**table_risk rules:** `high` if table_count ≥ 3 or any table spans > 50% page width; `medium` if table_count ≥ 1; `low` otherwise.
**ocr_risk rules:** `high` if needs_ocr=true; `medium` if text_length < 300 and image_area_ratio > 0.2; `low` otherwise.
**reading_order_risk rules:** `high` if image_area_ratio > 0.4 and text_length > 200 (mixed layout); `medium` if table_count ≥ 2; `low` otherwise.

---

### 13.3 Routing Plan (v1 — serialize existing ParsePlan)

Produces `routing_plan.json` from the existing `ParsePlan` dataclass.

```json
{
  "document_id": "stemi",
  "primary_strategy": "docling_first",
  "model_tier": "primary",
  "max_rounds": 3,
  "judge_sample_rate": 0.6,
  "use_docling": true,
  "size_tier": "medium",
  "vision_dependency": "medium",
  "complexity_score": 0.42,
  "pages": [
    {
      "page": 0,
      "route_type": "docling_plus_pdfplumber",
      "reason": "table_risk_medium",
      "confidence_threshold": 8.0
    }
  ]
}
```

---

### 13.4 Full 25-Profiler (v2+ — future)

The full profiler spec (text layer, layout, reading order, OCR, image, table, form, figure/caption, equation, language, domain, risk, complexity, noise, security, continuity, structure, human review, runtime, tool recommendation, routing, confidence expectation, kv) is preserved as a future expansion target. Do not build during v1.

---

## 14. Final Profiler Output Files (v1)

Per document, produce:

```text
data/outputs/{specialty}/{doc_name}/
├── file_profile.json          ← §13.1
├── page_profiles.json         ← §13.2
├── routing_plan.json          ← §13.3
├── confidence_report.json     ← §17
└── human_review_queue.json    ← §17
```

Optional (future):
```text
├── document_profile.json      ← full DocProfile (currently in memory)
├── layout_profile.json
├── risk_profile.json
└── language_profile.json
```

---

## 15. Output Folder Structure

### 15.1 Target structure (post-refactor)

```text
data/
├── raw/
│   └── {specialty}/
│       └── {stem}.pdf
└── outputs/
    └── {specialty}/
        └── {stem}/
            ├── final.md
            ├── file_profile.json
            ├── page_profiles.json
            ├── routing_plan.json
            ├── confidence_report.json
            ├── low_confidence_pages.md
            ├── human_review_queue.json
            ├── {stem}_review.md          ← Phase 9
            ├── original.pdf              ← copy of source
            ├── docling/
            │   ├── docling_output.md
            │   ├── docling_output.json
            │   └── docling_warnings.log
            ├── page_images/              ← region crops (ECG, figure, diagram)
            └── debug/                    ← raw extraction per round (optional)
```

### 15.2 Path derivation rule

```python
# Input:  data/raw/cardiology/stemi.pdf
# Output: data/outputs/cardiology/stemi/
specialty = pdf_path.parent.name     # "cardiology"
stem = pdf_path.stem                 # "stemi"
output_dir = DATA_DIR / "outputs" / specialty / stem
```

If no `raw/` directory in path → `data/outputs/{stem}/` (no specialty subdirectory).

### 15.3 Migration note

**The output path refactor is a named stage.** Do not change output paths until the "Output Refactor" stage is explicitly activated (see §28). During this stage:
- All output path logic in `parser_agent.py`, `deep_review.py`, `registry.py`, `main.py`, `confidence_report` must be updated together atomically.
- **Clear the registry** before refactor — do not attempt entry-by-entry migration. Old entries point to `data/markdown/` paths that will no longer exist. `cloak clean --yes` should delete both `data/markdown/` and `data/outputs/` and reset the registry to empty.
- `cloak list` must be updated to read from `data/outputs/`.
- After refactor, `data/markdown/` is fully removed from config.py and `.gitignore`d.

---

## 16. Final Markdown Format

`final.md` must include YAML frontmatter.

### 16.1 YAML frontmatter spec

```yaml
---
title: Management of STEMI
source_file: stemi.pdf
source_path: data/raw/cardiology/stemi.pdf
parser: cloak
primary_engine: docling
confidence: 8.3
confidence_status: high
pages: 44
needs_review: false
parse_date: 2026-05-21
cloak_version: 0.1.0
---
```

**Field sources:**
- `title`: PDF metadata `doc.metadata.get('title')` → first `# heading` in final.md → `pdf_path.stem`
- `confidence`: `best_round.score` (0–10 scale)
- `confidence_status`: `"high"` (≥8.0) / `"medium"` (≥5.0) / `"low"` (<5.0)
- `needs_review`: `True` if any page has `PageScore.confidence == "Low"`
- `parse_date`: ISO date at time of parse
- `cloak_version`: from `pyproject.toml`

### 16.2 Body format

Headings from Docling SectionHeaderItem at correct hierarchy levels (##, ###, ####). Tables in GFM format. Figures as `![label](relative_path)` with caption on next line. Footnotes appended at section end as numbered references.

---

### 16.3 Markdown Quality Standards (Industry-Standard Requirements)

> These standards exist because `final.md` is consumed by downstream tools: RAG chunkers, Obsidian vaults, LLMs, search indexes. Poor structure breaks all of them silently.

#### Document structure

- **One H1 per document** — always the document title, always first
- **Heading levels never skipped** — H2 follows H1; H3 follows H2; never H1 → H3
- **Blank line before and after every heading** — ensures all Markdown parsers render correctly
- **YAML frontmatter is the very first content** — nothing before the opening `---`

#### Tables

- **GFM format with header separator row** — every table must have `| --- |` as second row
- **Equal column count across all rows** — unequal columns break renderers and RAG chunkers
- **Blank line before and after every table**
- **No merged cells in Markdown** — represent merged cells as repeated value or footnote reference; never leave blank cells mid-row

#### Lists

- **Consistent marker within a list** — all `-` or all `*`, never mixed in the same list
- **Blank line before and after a list block**
- **Nested lists indented with 2 spaces** — not tabs, not 4 spaces (consistency matters for parsers)

#### Code blocks

- **Always fenced with triple backticks** — never indented code blocks
- **Language annotation where identifiable** — ` ```python `, ` ```json `, ` ```bash ` etc.
- **Blank line before and after every code block**

#### Images

- **Descriptive alt text** — `![ECG showing ST elevation in leads II, III, aVF]` not `![image]` or `![figure]`
- **Relative paths only** — paths must work from the document's output directory

#### Text quality

- **No page header/footer artifacts** — repeated running heads like "CHAPTER 3" or page numbers mid-paragraph must be stripped
- **No ligature errors** — ﬂ → fl, ﬁ → fi, ﬀ → ff (normalised at extraction in pdf_tools.py)
- **No OCR garbage** — `l0rem` style noise, broken unicode, control characters
- **No soft-hyphen line-break artifacts** — `treat-\nment` → `treatment`
- **No duplicate consecutive sections** — same heading appearing twice in a row

#### Encoding and file format

- **UTF-8 encoding** — enforced at write time
- **Unix line endings (LF)** — consistent across platform
- **Single trailing newline** at end of file

#### RAG-readiness rules

These directly affect chunking quality in downstream RAG pipelines:

- **Each section starts with its heading** — no content floating before the first heading of a section
- **No cross-section content bleed** — a paragraph must not continue across a heading boundary
- **Captions immediately follow their figure or table** — never separated by other content
- **Footnotes at end of section, not mid-paragraph** — inline footnote markers (`[1]`) link to end-of-section block
- **Abbreviation/term definitions in a consistent block** — not scattered mid-text

#### FORMAT phase enforcement (Phase 4)

The FORMAT step (qwen3:8b, Phase 4) must enforce all of the above. The FORMAT prompt must instruct the model to:
1. Fix heading hierarchy — promote or demote headings to restore H1→H2→H3 order
2. Normalize table separators — add missing `| --- |` rows, fix column count mismatches
3. Remove page header/footer artifacts — lines that match running header/footer patterns
4. Fix list markers — standardize to `-` throughout
5. Remove orphaned content before first heading — move it after the H1 or discard if it is artifact
6. Never invent content — structural fixes only, no new text

---

## 17. Confidence Scoring

All scores use the **0–10 scale** throughout the system. No conversion to 0.0–1.0.

### 17.1 confidence_report.json

```json
{
  "document": "stemi.pdf",
  "document_confidence": 8.3,
  "confidence_status": "high",
  "quality_threshold": 8.0,
  "rounds_run": 1,
  "pages_judged": 27,
  "pages_total": 44,
  "needs_review": false,
  "pages": [
    {
      "page": 0,
      "score": 9.1,
      "content_score": 9.3,
      "structure_score": 8.7,
      "confidence": "High",
      "tools_used": ["docling", "pdfplumber"],
      "gaps": [],
      "needs_review": false
    },
    {
      "page": 11,
      "score": 4.8,
      "content_score": 5.1,
      "structure_score": 4.1,
      "confidence": "Low",
      "tools_used": ["docling", "surya"],
      "gaps": ["drug dosage table lost structure", "some list items missing"],
      "needs_review": true
    }
  ]
}
```

### 17.2 Confidence levels

```text
≥ 8.0 → High   (quality threshold met)
≥ 5.0 → Medium (acceptable, may have gaps)
< 5.0 → Low    (needs human review)
```

### 17.3 human_review_queue.json

```json
{
  "document": "stemi.pdf",
  "total_pages": 44,
  "review_needed": 3,
  "pages": [
    {
      "page": 11,
      "score": 4.8,
      "review_type": "table_review",
      "reason": "drug dosage table lost structure — complex multi-column layout",
      "tools_used": ["docling", "surya"]
    }
  ]
}
```

**review_type mapping:**
- `table_review` — page_type=table_heavy AND score < 5.0
- `ocr_review` — needs_ocr=true AND score < 5.0
- `layout_review` — reading_order_risk=high AND score < 5.0
- `visual_review` — needs_vision=true AND score < 5.0
- `general_review` — score < 5.0 with no specific type match

### 17.4 low_confidence_pages.md

Human-readable version of review queue. One section per page with score, reason, and raw extracted text snippet.

---

## 18. CLI Requirements

### 18.1 doctor (NEW)

```bash
cloak doctor
```

Checks local environment. Runs in under 5 seconds. No model load tests.

Expected output:
```text
  Python           3.12.3        ✓
  docling          2.94.0        ✓
  surya-ocr        0.17.1        ✓
  pytesseract      0.3.13        ✓
  pymupdf          1.24.x        ✓
  pdfplumber       0.11.x        ✓
  Ollama           running       ✓
  qwen3:8b         installed     ✓  [required]
  qwen2.5vl:7b     installed     ✓  [required]
  qwen3-vl:4b      installed     ✓  [optional]
  gemma4:latest    installed     ✓  [optional]
  Tesseract bin    found         ✓
  NVIDIA GPU       RTX 5050      ✓  8.0 GB VRAM
  Free VRAM                      6.2 GB
  Free RAM                       18.4 GB
```

Acceptance criteria:
- Prints each dependency with version and status
- Marks required vs optional
- Does not crash if optional dependencies are missing
- Returns exit code 1 if any required dependency is missing
- Checks Ollama via GET /api/tags (not a model load test)
- Checks Tesseract binary path

---

### 18.2 profile (NEW)

```bash
cloak profile <pdf>
```

Runs Phase 0 (intake) + Phase 1 (docling layout pass + profiling) only. Does not run any LLM. Produces all JSON profile files.

Expected output files:
```text
data/outputs/{specialty}/{stem}/file_profile.json
data/outputs/{specialty}/{stem}/page_profiles.json
data/outputs/{specialty}/{stem}/routing_plan.json
```

Acceptance criteria:
- Runs in under 60 seconds for a 50-page PDF
- Does not load any Ollama model
- Produces valid JSON for all three files
- `cloak parse` checks for existing `file_profile.json` and compares `file_hash_sha256` against the current file's SHA256. If hash matches → skip Phase 1 (profile is still valid). If hash differs or file is missing → re-profile. Never use modification time for cache invalidation.

---

### 18.3 parse (existing — update after refactor)

```bash
cloak parse <pdf>
cloak parse <pdf> --no-review
cloak parse <pdf> --dry-run
cloak parse <dir>
```

After output refactor: outputs go to `data/outputs/{specialty}/{stem}/`. Before refactor: unchanged.

---

### 18.4 validate (NEW)

```bash
cloak validate data/outputs/cardiology/stemi/final.md
```

Deterministic Markdown validation only. No LLM. All checks are derived from §16.3 quality standards.

Expected output:
```text
  YAML frontmatter         present              ✓
  H1 heading               present (1)          ✓
  Heading hierarchy        valid                ✓
  Empty headings           0                    ✓
  Broken GFM tables        0                    ✓
  Missing table separators 0                    ✓
  Duplicate headings       0                    ✓
  Page header artifacts    0                    ✓
  List marker consistency  consistent           ✓
  UTF-8 encoding           valid                ✓
  Overall                  VALID
```

**Checks (all deterministic — no LLM):**

| Check | Pass condition | Fail example |
|---|---|---|
| YAML frontmatter | Present and parseable by PyYAML | File starts with `# Title` instead of `---` |
| One H1 | Exactly one `# ` line | Zero H1s, or two H1s |
| Heading hierarchy | No level skipped (H1→H2→H3 only) | `# Title` followed by `### Section` |
| Empty headings | No `## ` with blank or whitespace-only content | `## ` with nothing after |
| GFM table column count | All rows in a table have equal `\|` count | Header has 4 cols, data row has 3 |
| Table separator row | Every table has `\| --- \|` as second row | Table with no separator |
| Duplicate consecutive headings | No two identical headings adjacent | `## Diagnosis` twice in a row |
| Page artifact lines | No lines matching running header/footer pattern (ALL CAPS short lines, page number-only lines) | `CHAPTER 3` mid-paragraph |
| List marker consistency | Each list uses one marker type (`-` or `*`) | Mixed `-` and `*` in same list |
| UTF-8 | File decodes cleanly with `errors='strict'` | Binary garbage or control chars |

**Exit codes:**
- `0` — all checks pass
- `1` — one or more checks failed (list failures explicitly)

**Future flag (planned — not in v1):**
```bash
cloak validate data/outputs/cardiology/stemi/final.md --deep
```
Deep validate compares the Markdown against the source PDF: verifies heading count matches docling's SectionHeader count, table count matches docling's TableItem count. Requires source PDF to be present. Planned for after the output refactor stage.

---

### 18.5 inspect (NEW)

```bash
cloak inspect data/outputs/cardiology/stemi/confidence_report.json
cloak inspect data/outputs/cardiology/stemi/routing_plan.json
cloak inspect data/outputs/cardiology/stemi/human_review_queue.json
```

Pretty-prints JSON profile files with rich formatting. Shows summary statistics.

---

### 18.6 status (existing)

```bash
cloak status
```

Hardware + model status. Unchanged.

---

### 18.7 list (existing — update after refactor)

```bash
cloak list
```

After output refactor: reads from `data/outputs/` via registry. Before refactor: reads from `data/markdown/` via registry. Unchanged behavior.

---

### 18.8 clean (existing — update behavior)

```bash
cloak clean
cloak clean --yes
```

**Before output refactor:** removes `data/markdown/` + clears registry.

**After output refactor:** removes `data/outputs/` + clears registry. Also removes `data/markdown/` if it still exists (cleans up both old and new paths so no stale files are left behind).

`cloak clean` must always:
1. Show a count of what will be deleted (dirs + files) before asking for confirmation
2. Clear the registry JSON to `{"documents": []}` after deletion
3. Never delete `data/raw/` — source PDFs are never touched

---

## 19. Locked Architecture Decisions (D1–D32)

> **Do not change these without explicit user approval. Read before touching any pipeline logic.**

| Decision | Rule | Why |
|---|---|---|
| D1 | Iterative quality loop — extract → judge → patch, up to max_rounds | Complex PDFs need multi-pass scoring |
| D2 | Best round wins — return highest-scoring round, not last | Patching can degrade; keep peak |
| D3 | Quality threshold 8.0 — stop early when score ≥ 8.0 | ≤20% content missing is acceptable |
| D4 | Spatial sort by bbox, not PDF draw order | Multi-column PDFs have wrong draw order |
| D5 | Content-loss guard 35% — revert if new_md < 0.65 × old_md | qwen3 occasionally over-compresses |
| D6 | Context compression at 8K tokens between rounds | Keeps prompts snappy |
| D11 | MODEL_KEEP_ALIVE = -1 — models stay loaded until explicit phase-boundary unload | keep_alive=0 caused 10 cold reloads per round |
| D14 | Phase-based model lifecycle — before_vision_phase() / before_orchestrator_phase() are explicit boundaries | Never unload models mid-round |
| D15 | VISION_FALLBACK = qwen3-vl:4b (not llama3.2-vision) | llama3.2-vision:11b timed out on full-page OCR |
| D16 | General-purpose prompts — no domain-specific language | Prompts work for any PDF type |
| D17 | Startup screen only on bare `cloak` and `cloak status` | Avoids cluttering parse output |
| D18 | Total-memory gate: MIN_FREE_RAM_GB = 9.0 | Gate for vision model enable |
| D19 | Extract once — rounds 2+ judge+patch only, no re-extraction | Extraction is expensive; only judgement iterates |
| D20 | FORMAT before PATCH — Phase 4 cleans up first | Structure must be correct before gap-filling |
| D21 | Page profiler — heuristic 5-type classification with docling element map | Determines extraction strategy per page |
| D22 | OCR: Surya primary, Tesseract fallback. No PaddleOCR | Surya better on RTX 5050; PaddleOCR install is heavy |
| D23 | Vision for text_rich pages too — headings from visual layout | pdfplumber flat text loses heading hierarchy |
| D24 | Per-page confidence output — High/Medium/Low | Never silently fail |
| D25 | No Camelot | Windows installation complexity; pdfplumber sufficient for current docs |
| D26 | Folder structure: profiling/, extraction/, vision/, quality/, orchestration/ | Clean separation of concerns |
| D27 | Phase 9 deep review — gemma4:latest loads after teardown_pdf() | gemma4 (9.6 GB) needs all pipeline VRAM freed first |
| D28 | DocProfile + ParsePlan — profiling before model load | Adaptive round budget, model tier, judge sampling rate |
| D29 | Docling element map drives extraction | SectionHeader→heading, Table→markdown, Figure→vision crop, PageHeader→DISCARD |
| D30 | Surya replaces Tesseract as primary OCR for scanned pages | Better reading order + GPU acceleration |
| D31 | Structural fidelity as second judge axis — 0.7 content + 0.3 structure | Content completeness alone misses layout bugs |
| D32 | Total-memory routing: free_vram + free_ram determines model viability | Ollama auto-splits across GPU+CPU RAM |
| D33 | final.md must meet §16.3 industry-standard Markdown quality rules | RAG chunkers, Obsidian, LLMs, search indexes all depend on clean structure; poor Markdown breaks downstream tools silently |

---

## 20. Anti-Patterns

> **These are real failures from Sessions 1–13. Do not repeat them.**

| Anti-pattern | What happens | Correct approach |
|---|---|---|
| Using `llama3.2-vision:11b` for full-page OCR | Times out consistently (180s+) on large pages | Use `qwen2.5vl:7b` primary, `qwen3-vl:4b` fallback |
| `MODEL_KEEP_ALIVE = 0` | Up to 10 cold model reloads per judge round → 5–10 min overhead | `MODEL_KEEP_ALIVE = -1`, unload only at phase boundaries |
| Running vision probe for every PDF regardless of content | 30s overhead even for text-only PDFs | Check `DocProfile.vision_dependency` first (D28) |
| Using Camelot for tables | Difficult Windows install; ghostscript dependency | pdfplumber is sufficient for current document types (D25) |
| Using PaddleOCR | Heavy install, redundant with Surya | Surya primary, Tesseract fallback (D22/D30) |
| Adding domain-specific language to prompts | Breaks on non-medical PDFs | Domain-neutral prompts only (D16) |
| Extracting text in rounds 2+ | Slow; extraction is idempotent | Extract once in Phase 3; rounds 2+ judge+patch only (D19) |
| Loading gemma4 before teardown_pdf() | OOM — 9.6 GB + 12.5 GB exceeds 24 GB RAM | Always call teardown_pdf() before Phase 9 (D27) |
| Unloading qwen3:8b mid-patch loop | Session state lost; next call re-loads cold | Only unload at before_vision_phase() boundary (D14) |
| Writing output to disk before best round is chosen | Saves suboptimal round | Phase 8 output only after all rounds complete; write best_round (D2) |
| Using `FORMAT_NUM_CTX = 4096` for FORMAT step | qwen3 thinking tokens truncate the actual output | `FORMAT_NUM_CTX = 8192` (D20) |
| Using pdfplumber for heading extraction | Returns flat text with no heading hierarchy | Docling SectionHeaderItem is the heading source (D29) |
| Hardcoding `marginal` VRAM band (85%) | Misleads user; model is either viable or not | Binary: viable (total_free ≥ model_weight) or unavailable (D32) |
| Skipping heading levels in output (H1 → H3) | Breaks RAG chunkers, Obsidian, LLM parsing; heading hierarchy silently wrong | FORMAT phase must enforce H1→H2→H3 order; validate catches regressions (§16.3) |
| Writing page header/footer artifacts into final.md | "CHAPTER 3", "Page 12" appear mid-document; corrupt RAG chunks | Docling PageHeader/PageFooter items are DISCARDED (D29); FORMAT phase strips residuals |
| Inconsistent list markers (-/*/+) in same list | Markdown renders inconsistently across tools | FORMAT phase normalizes all lists to `-` |
| Using time-based cache invalidation for profiles | Stale profile used when PDF is replaced with same filename | Always compare SHA256 hash from file_profile.json against current file (§18.2) |

---

## 21. Recommended Python Project Structure

```text
cloak/
├── __init__.py
├── config.py
├── registry.py                        ← workspace-local JSON registry
├── cli/
│   ├── __init__.py
│   ├── main.py                        ← typer CLI
│   └── system_check.py
├── profiling/
│   ├── __init__.py
│   ├── page_profiler.py
│   ├── doc_profiler.py
│   └── file_profiler.py              ← NEW (Stage 2 v1)
├── extraction/
│   ├── __init__.py
│   ├── pdf_tools.py
│   └── ocr_tools.py
├── vision/
│   ├── __init__.py
│   └── vision_tools.py
├── quality/
│   ├── __init__.py
│   ├── quality_judge.py
│   └── deep_review.py
├── orchestration/
│   ├── __init__.py
│   ├── model_router.py
│   ├── context_manager.py
│   └── parser_agent.py
├── output/                            ← NEW (refactor stage)
│   ├── __init__.py
│   ├── paths.py                       ← output path derivation logic
│   ├── writer.py                      ← write final.md + all JSON reports
│   └── validator.py                   ← cloak validate logic
└── ingestion/                         ← legacy read-only
    ├── pdf_extractor.py
    ├── pdf_classifier.py
    ├── vision.py
    └── markdown_builder.py
```

---

## 22. AGENTS.md Requirements

```markdown
# CLOAK Agent Instructions

You are helping build CLOAK, a local-first Docling-first agentic document parser.

## Core Rules

1. Do not change locked decisions D1–D32 without flagging it explicitly to the user.
2. Do not introduce cloud APIs.
3. Keep Markdown as the primary output.
4. Keep confidence reporting and review queue as first-class outputs.
5. Do not silently assume tests passed — always provide CLI verification commands.
6. After every implementation task, provide manual CLI commands for the user to run.
7. Prefer deterministic code before LLM calls.
8. Use local Ollama models only for orchestration, repair, judging, and vision fallback.
9. Do not touch ingestion/ legacy files.
10. Do not change output paths until the Output Refactor stage is explicitly activated.

## Manual Testing Rule

After every feature, provide:
1. Exact CLI commands to run.
2. Expected output files and terminal signs.
3. What result the user should paste back.
4. Troubleshooting steps if the command fails.
```

---

## 23. Manual Test Protocol

Every coding-agent task must end with a Manual Verification block:

```markdown
## Manual Verification

### Commands to Run
\`\`\`powershell
.\.venv\Scripts\Activate.ps1
cloak doctor
\`\`\`

### Expected Output
\`\`\`
Python     3.12.3   ✓
docling    2.94.0   ✓
...
\`\`\`

### Expected Files
None for this command.

### Paste Back
Please paste:
1. Full terminal output
2. Any error traceback
```

---

## 24. Bug Report Template

```markdown
# CLOAK Bug Report

## Command Run
\`\`\`powershell
cloak parse data/raw/cardiology/stemi.pdf
\`\`\`

## Expected Result
...

## Actual Result
...

## Error Traceback
\`\`\`text
...
\`\`\`

## Output Files Created
\`\`\`text
...
\`\`\`

## Environment
- OS: Windows 11
- Python version:
- GPU: RTX 5050 8 GB
- VRAM free at start:
- RAM free at start:
- Ollama version:
- Installed models:

## Notes
...
```

---

## 25. Next Build Targets (Ordered)

> **Opus: use this list to plan. Do not skip stages. Do not parallelize stages that depend on each other.**

| # | Stage | Effort | Depends on | Value |
|---|---|---|---|---|
| 1 | `cloak doctor` command | Small | Nothing | Verifies env; critical for onboarding |
| 2 | YAML frontmatter in `final.md` | Small | Nothing | Enables RAG, Obsidian, downstream tools |
| 3 | `file_profiler.py` → `file_profile.json` | Small | Nothing | Completes Stage 2 |
| 4 | Serialize PageProfile → `page_profiles.json` | Small | Nothing | Unlocks profile command |
| 5 | Serialize ParsePlan → `routing_plan.json` | Small | Nothing | Unlocks profile command |
| 6 | `confidence_report.json` (alongside existing `_confidence.md`) | Small | Nothing | Structured confidence output |
| 7 | `human_review_queue.json` + `low_confidence_pages.md` | Medium | Steps 4 + 6 | Completes review workflow |
| 8 | `cloak profile <pdf>` command | Medium | Steps 3–5 | Standalone profiler |
| 9 | `cloak validate <md>` command | Medium | Nothing | Markdown quality gate |
| 10 | **Output Refactor** — migrate to `data/outputs/{specialty}/{doc_name}/` | Large | Steps 3–7 done | Per-doc isolation, clean structure |
| 11 | Update `cloak list` + registry for new paths | Small | Step 10 | Fixes `cloak list` after refactor |
| 12 | `cloak inspect <json>` command | Small | Step 10 | Completes CLI surface |
| 13 | Profiler v2 — extend PageProfile with table_risk/ocr_risk/reading_order_risk | Medium | Step 10 | Better routing decisions |
| 14 | Full 25-sub-profiler (v3+) | Large | Step 13 | Future — not in current sprint |

---

## 26. Updated Build Roadmap

### Stage 1: Doctor Command ← START HERE

Goal: Verify local environment.

Command:
```bash
cloak doctor
```

Implementation:
- New `doctor` command in `cli/main.py`
- Check each dependency with `importlib.metadata.version()` or `importlib.import_module()`
- Check Tesseract binary via `subprocess.run(['tesseract', '--version'])`
- Check Ollama via `system_check.is_ollama_running()` + `get_installed_models()`
- Check GPU via existing `system_check.get_free_vram_gb()`
- Required models: `qwen3:8b`, `qwen2.5vl:7b`
- Optional models: `qwen3-vl:4b`, `gemma4:latest`
- Exit code 1 if any required dependency missing

Acceptance criteria:
- Runs in < 5 seconds
- Prints structured table with version + status per dependency
- Does not crash if optional deps missing
- Marks required vs optional clearly

---

### Stage 2: YAML Frontmatter in final.md

Goal: Add YAML frontmatter to every `final.md`.

Implementation in `parser_agent.py` (Phase 8 output):
```python
def _build_frontmatter(pdf_path, best_round, pages):
    title = _extract_title(pdf_path, best_round.markdown)
    confidence_status = "high" if best_round.score >= 8.0 else "medium" if best_round.score >= 5.0 else "low"
    needs_review = any(s.confidence == "Low" for s in best_round.page_scores)
    return f"""---
title: {title}
source_file: {pdf_path.name}
source_path: {pdf_path}
parser: cloak
primary_engine: docling
confidence: {best_round.score:.1f}
confidence_status: {confidence_status}
pages: {len(pages)}
needs_review: {str(needs_review).lower()}
parse_date: {datetime.date.today().isoformat()}
---

"""

def _extract_title(pdf_path, markdown):
    import fitz
    doc = fitz.open(pdf_path)
    title = doc.metadata.get('title', '').strip()
    if title:
        return title
    for line in markdown.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return pdf_path.stem
```

Acceptance criteria:
- Every `final.md` starts with valid YAML frontmatter
- `title` field is never empty
- `confidence` uses 0–10 scale
- `needs_review` is boolean string (`true`/`false`)

---

### Stage 3: file_profiler.py → file_profile.json

Goal: Extract file metadata and produce `file_profile.json`.

New file: `cloak/profiling/file_profiler.py`

```python
def build_file_profile(pdf_path: Path) -> dict:
    # PyMuPDF metadata + hashlib + pathlib.stat()
    # Returns dict matching §13.1 spec
```

Called in `parser_agent.py` Phase 0 (intake). Save to current output dir (pre-refactor path).

Acceptance criteria:
- Produces valid JSON
- `file_hash_sha256` computed correctly (test with known file)
- `is_encrypted` detected correctly
- Graceful fallback for missing metadata fields (empty string, not crash)

---

### Stage 4–5: Serialize PageProfile and ParsePlan to JSON

Goal: Write `page_profiles.json` and `routing_plan.json` at end of Phase 1.

Changes in `parser_agent.py`:
- After `build_doc_profile()` + `build_parse_plan()`, serialize both to JSON
- `page_profiles.json`: `[asdict(p) for p in page_profiles]`
- `routing_plan.json`: per §13.3 spec — include per-page route_type derived from RouteMap

Acceptance criteria:
- Both files produced on every parse run
- Valid JSON
- Readable by `cloak inspect`

---

### Stage 6: confidence_report.json

Goal: Produce structured JSON confidence report alongside existing `_confidence.md`.

Changes in `parser_agent.py` Phase 8:
- Add `_write_confidence_json(best_round, pdf_path, pages)` function
- Schema: §17.1

Acceptance criteria:
- Produced on every parse run
- `document_confidence` matches judge's best_round.score
- Per-page `score`, `content_score`, `structure_score`, `gaps`, `needs_review`

---

### Stage 7: human_review_queue.json + low_confidence_pages.md

Goal: Identify pages needing human review.

Changes in `parser_agent.py` Phase 8:
- Filter `best_round.page_scores` for pages with confidence == "Low" (score < 5.0)
- Assign `review_type` per §17.3 rules
- Write `human_review_queue.json` per §17.3 spec
- Write `low_confidence_pages.md` with score + reason + raw text snippet per page

Acceptance criteria:
- Empty queue produces valid JSON `{"review_needed": 0, "pages": []}`
- `review_type` correctly assigned
- `low_confidence_pages.md` includes page number, score, reason, and first 500 chars of raw pdfplumber text for that page

---

### Stage 8: cloak profile command

Goal: Standalone profiler — Phase 0 + Phase 1 only, no LLM.

Changes in `cli/main.py`:
- New `profile` command
- Calls: `pdf_tools.load_pages()` → `file_profiler.build_file_profile()` → `page_profiler.profile_all()` → `doc_profiler.build_doc_profile()` + `build_parse_plan()` → serialize all to JSON
- Cache check: if `file_profile.json` exists and is < 24 hours old, skip re-profiling in `cloak parse`

Acceptance criteria:
- Runs in < 60 seconds on a 50-page PDF
- No Ollama calls
- Produces file_profile.json, page_profiles.json, routing_plan.json
- `cloak parse` detects fresh profile and skips Phase 1

---

### Stage 9: cloak validate command

Goal: Deterministic Markdown validation.

New file: `cloak/output/validator.py`
New command in `cli/main.py`.

Checks per §18.4. All deterministic — no LLM.

---

### Stage 10: Output Refactor ← LARGE STAGE — PLAN CAREFULLY

Goal: Migrate all output paths from `data/markdown/{specialty}/stem.*` to `data/outputs/{specialty}/{stem}/`.

Files to change:
- `cloak/orchestration/parser_agent.py` — all output path logic
- `cloak/quality/deep_review.py` — review output path
- `cloak/registry.py` — registry storage paths
- `cloak/cli/main.py` — `cloak list`, `cloak clean` paths
- `cloak/config.py` — `MD_DIR` → introduce `OUTPUTS_DIR`

New file: `cloak/output/paths.py` — single source of truth for path derivation.

Migration strategy:
1. Add `OUTPUTS_DIR = DATA_DIR / "outputs"` to config.py
2. Create `output/paths.py` with `get_output_dir(pdf_path) → Path`
3. Update parser_agent to use `output/paths.py`
4. Update registry to record new paths; **clear the registry** (`{"documents": []}`) — do not migrate old entries
5. Update `cloak list` to read from `data/outputs/`
6. Update `cloak clean` to remove both `data/outputs/` AND `data/markdown/` (belt-and-suspenders; keeps only `data/raw/`)
7. Test full parse run end-to-end including `cloak list`, `cloak clean`, `cloak profile`
8. Only then: remove `MD_DIR` from `config.py`, add `data/markdown/` to `.gitignore`

Do NOT do this stage while any other changes are in flight. Commit atomically. Run `cloak doctor` after to verify clean state.

---

### Stages 11–14: Inspect, Profiler v2, Full 25-Profiler

See §25 build targets table for sequencing.

---

## 27. Acceptance Criteria for CLOAK as a Whole

CLOAK is successful when:

```text
1.  cloak doctor        — prints clean status for all deps, exits 1 if required dep missing
2.  cloak profile       — produces all JSON profile files without LLM
3.  cloak parse         — produces final.md with valid YAML frontmatter
4.  cloak parse         — final.md passes cloak validate (§16.3 quality rules)
5.  cloak parse         — produces confidence_report.json (0–10 scale)
6.  cloak parse         — produces human_review_queue.json with review_type per page
7.  cloak parse         — produces routing_plan.json
8.  cloak validate      — detects all §16.3 violations: skipped headings, broken tables,
                          page artifacts, list inconsistency, encoding errors
9.  cloak list          — shows all parsed docs with scores from registry
10. cloak inspect       — pretty-prints any JSON output file
11. cloak clean         — removes both data/outputs/ and data/markdown/, clears registry,
                          never touches data/raw/
12. confidence scores   — document + per-page, reasons for low scores
13. profile caching     — cloak parse skips Phase 1 if file hash unchanged
14. local-only          — no cloud API calls at any stage
15. agents can build    — each stage verifiable with exact CLI commands
```

---

## 28. Quality Requirements

### 28.1 Reliability
CLOAK should fail gracefully. If parsing fails, always produce at minimum: `file_profile.json`, `logs.txt`, registry entry with `status=error`.

### 28.2 Transparency
Every output should explain: tool used, confidence, warnings, fallbacks attempted, review required.

### 28.3 Reproducibility
File hash + routing_plan.json + logs.txt are sufficient to reproduce a parse run.

### 28.4 Local-first privacy
No document content leaves the machine by default.

### 28.5 Extensibility
New tools should be easy to add as adapters in `extraction/` without touching the orchestrator.

---

## 29. Prompt Requirements

### 29.1 FORMAT Prompt (qwen3:8b — Phase 4)

The FORMAT step runs once after extraction. Its job is structural cleanup, not gap-filling. It must enforce the §16.3 quality standards.

```text
/no_think
You are a Markdown formatter. Clean up the structure of the extracted document below.

RULES — apply all of them, in order:
1. HEADING HIERARCHY: Fix any skipped heading levels. H2 must follow H1. H3 must follow H2. Never H1 → H3. Promote or demote headings to restore correct order without changing their text.
2. ONE H1: The document must have exactly one H1 heading (the document title). If multiple H1s exist, demote all but the first to H2.
3. TABLES: Every table must have a `| --- |` separator as its second row. If missing, add it. Fix any rows with unequal column counts by padding with empty cells.
4. LISTS: Normalize all list markers to `-`. Remove mixed markers within the same list.
5. ARTIFACTS: Remove lines that are page headers, page footers, or page numbers appearing mid-document (e.g. standalone "CHAPTER 3", standalone "12", "Page 12 of 44").
6. SPACING: Add a blank line before and after every heading, table, list block, and code block. Remove multiple consecutive blank lines (max one blank line between blocks).
7. CONTENT: Do NOT add, remove, or paraphrase any content. Structural fixes only.
8. OUTPUT: Return the cleaned Markdown only. No preamble. No code fences wrapping the whole document.
```

### 29.2 Markdown Repair Prompt (qwen3:8b — Phase 6)

The PATCH step fills content gaps after FORMAT has fixed structure.

```text
/no_think
You are a Markdown content repair agent. The document below has been extracted from a PDF.
The judge has identified specific gaps. Fill only those gaps using the source text provided.

RULES:
1. Do not invent content not present in the source text.
2. Do not remove any existing content.
3. Preserve all heading levels exactly as they are.
4. Preserve tables — repair structure if broken, never drop rows.
5. If a gap cannot be filled from available source text, add `[content unavailable]` as a placeholder.
6. Return the complete repaired Markdown. No preamble. No code fences.
```

### 29.3 Vision Extraction Prompt (qwen2.5vl:7b — Phase 3)

```text
Extract all visible document content from this page image into Markdown.

RULES:
1. Preserve the exact heading hierarchy you see — use #, ##, ### levels matching the visual prominence of headings.
2. Extract tables as GFM Markdown tables with a `| --- |` separator row.
3. Preserve numbered lists, bullet lists, and their nesting exactly.
4. For figures and diagrams: write a factual description of what is shown. Do not guess values you cannot read.
5. Mark any text you cannot read clearly as [unreadable].
6. Do NOT hallucinate. Do NOT summarize.
7. Return Markdown only — no code fences, no preamble, no explanation.
```

### 29.4 Deep Review Prompt (gemma4:latest — Phase 9)

```text
You are reviewing a PDF extraction for completeness and quality.
Compare the raw source text against the extracted Markdown.
Fill in all 8 sections of the review template.
Be specific: quote page numbers and missing content.
Use the 0–10 quality score scale (10 = perfect, 8+ = acceptable, below 5 = needs human review).
```

---

## 30. Development Philosophy for Coding Agents

Build in small slices. Preferred pattern:

```text
Read PRD §8 (current state) + §19 (locked decisions)
↓
Pick next task from §25 (ordered list)
↓
Implement minimal working version
↓
Provide manual CLI commands
↓
Wait for user results
↓
Fix based on actual output
↓
Update PROGRESS.md
```

**Do not build multiple stages simultaneously.**
**Do not change locked decisions without flagging to user.**
**Do not claim success without reproducible commands.**

---

## 31. Opus+Sonnet Agent Contract

### Opus (Orchestrator) responsibilities:
1. Read PROGRESS.md, MODULES.md, DECISIONS.md, and this PRD before planning.
2. Identify the correct next stage from §25.
3. Decompose the stage into ≤5 atomic Sonnet tasks.
4. Verify each task's output against this PRD's acceptance criteria before assigning the next.
5. Flag any locked decision that a task might need to change — never silently override.
6. Update PROGRESS.md after each stage completes.

### Sonnet (Implementation) responsibilities:
1. Implement exactly one atomic task per response.
2. After every code change, provide a Manual Verification block (§23 format).
3. Touch only the files listed in the task. State which files were changed.
4. Never remove existing functionality without explicit instruction.
5. Never add features beyond the task scope.
6. Never skip the Manual Verification block — even for "obvious" changes.
7. If a change requires touching a locked decision (D1–D32), stop and flag it to Opus.

### What neither agent should do:
- Add cloud API calls
- Add PaddleOCR or Camelot
- Use llama3.2-vision for full-page OCR
- Change output paths before Stage 10 is explicitly activated
- Change model names from the current stack
- Assume that Python tests passing = feature working (always verify via CLI)

---

## 32. One-Line Final Definition

CLOAK is a **Docling-first, local-first, agentic document parsing CLI** that profiles documents, routes pages intelligently, parses into Markdown, verifies quality, repairs failures, scores confidence, and produces transparent review-ready outputs — all locally, with no cloud dependency.

---

## 33. Final Build Contract

Every agent working on CLOAK must treat this PRD as the source of truth and follow these rules:

```text
1.  Keep Docling first.
2.  Keep Markdown first.
3.  Keep local-first privacy.
4.  Keep confidence/reporting first-class.
5.  Keep the user in the manual test loop.
6.  Build incrementally — one stage at a time.
7.  Update PROGRESS.md after each meaningful task.
8.  Provide CLI commands after every feature.
9.  Never silently assume success.
10. Never change locked decisions (D1–D32) without user approval.
11. Never convert CLOAK into a cloud-only service.
12. Never change output paths before the Output Refactor stage (§26 Stage 10) is activated.
```
