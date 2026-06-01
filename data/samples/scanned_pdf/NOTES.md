# Scanned PDF Samples — Notes

## What makes a good scanned PDF test case

A scanned PDF has no digital text layer — every page is an image.
`pymupdf.get_text()` returns empty or near-empty strings.
CLOAK must route all pages to Surya OCR (primary) or Tesseract (fallback).

Good scanned samples:
- Old government documents from the 1970s–1990s (pre-digital)
- Scanned textbook chapters (archive.org has thousands)
- Handwritten lecture notes (hardest case)
- Old legal documents (court records, old contracts)

## Key things to verify

- `page_type = "scanned"` for all pages (text_length < 100 chars)
- `ocr_profile.ocr_required = true` in page_profiles.json
- Surya OCR runs (check logs for "surya" in tools_used)
- Reading order is correct (multi-column scans are hardest)
- No garbage characters in final.md

## Where to find scanned PDFs

- https://archive.org — search for old books, click "PDF" download
  Filter: books with "Scanned" badge
- Google Books — older books are scanned; limited download
- HathiTrust Digital Library — academic scanned books (some public domain)

## Ideal sample characteristics

- 5–20 pages (manageable for testing)
- Single-column layout (simplest OCR test)
- Clear scan quality (not too blurry)
- English text
- Has at least one table (tests OCR table reconstruction)
