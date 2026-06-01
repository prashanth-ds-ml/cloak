# Mixed PDF Samples — Notes (Manual Download Required)

A mixed PDF has some pages with a digital text layer and others that are pure scanned images.
These are the hardest type to find because most archives either go fully digital or fully scanned.

## Best sources

### Option 1 — HathiTrust Digital Library
Browse: https://www.hathitrust.org/
Search for any book published 1960–1990 where some pages were typeset
and others (like plates, photographs, maps) were photographically reproduced.
Click "Get PDF" or "Download" (must be public domain).

### Option 2 — Archive.org mixed government documents
Many older US government reports from the 1970s–1990s mix digital text
pages with scanned charts/maps/plates.
Browse: https://archive.org/search?query=government+report&mediatype=texts&year=1975-1995

### Option 3 — Old technical reports from DTIC
DTIC (Defense Technical Information Center) has thousands of old military
and engineering reports where the cover pages are typeset (digital) but
appendices are scanned photographs or hand-drawn diagrams.
Browse: https://apps.dtic.mil/sti/

## What to look for

- Text pages: `pymupdf.get_text()` returns > 200 chars
- Scanned pages: `pymupdf.get_text()` returns < 50 chars, but page has visible content
- Both types must appear in the SAME PDF (not two separate files)
- Ideal: 10–30 pages, at least 2 digital + 2 scanned

## Why this type matters for CLOAK

Tests the page-routing logic in `profiling/page_profiler.py`:
- Some pages → digital extraction path (docling + pdfplumber)
- Other pages → OCR path (Surya)
- The pipeline must correctly identify which pages are scanned
  and switch extraction strategy per-page, not per-document.
