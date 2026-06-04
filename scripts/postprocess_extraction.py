"""
postprocess_extraction.py -- Post-processor for the extraction pipeline.

Takes raw markdown from extractor.py (GLM-OCR + pdfplumber combined)
and produces clean, structured output that closely matches Landing.ai quality.

Problems addressed:
  1. Merge GLM-OCR + pdfplumber sections (remove --- separator, deduplicate)
  2. OCR noise: single chars, bad encodings, artifact lines
  3. Heading normalization: remove ICD codes as headings, fix hierarchy
  4. Table cleanup: remove empty rows, fix cell formatting
  5. Duplicate content: same section in both GLM and pdfplumber sections

Usage:
  from scripts.postprocess_extraction import run
  clean_md = run(raw_md, strategy="text_mode")
"""
from __future__ import annotations
import re
from collections import Counter

# ── 1. Split and merge GLM-OCR / pdfplumber sections ─────────────────────────

def merge_sections(text: str, strategy: str = "text_mode") -> str:
    """
    Split on '---' separator that divides GLM-OCR (above) from pdfplumber (below).
    For text_mode: pdfplumber section is primary (cleaner text), keep GLM tables.
    For poster_mode: GLM-OCR section is primary (has the content), supplement with pdf.
    """
    if "\n---\n" not in text:
        return text

    parts = text.split("\n---\n", 1)
    glm_section  = parts[0].strip()
    pdf_section  = parts[1].strip() if len(parts) > 1 else ""

    if not pdf_section:
        return glm_section

    if strategy in ("poster_mode", "hybrid"):
        # GLM-OCR is primary — use it, supplement with unique pdfplumber sections
        primary   = glm_section
        secondary = pdf_section
    else:
        # text_mode: pdfplumber has cleaner text — use as primary
        # Extract any markdown TABLES from GLM-OCR section (these are valuable)
        glm_tables = _extract_tables(glm_section)
        primary    = pdf_section
        secondary  = "\n\n".join(glm_tables)  # only keep tables from GLM

    # Add unique sections from secondary that aren't in primary
    primary_headings  = set(_extract_heading_words(primary))
    secondary_lines   = secondary.splitlines()
    unique_secondary  = []
    in_unique_section = False

    for line in secondary_lines:
        stripped = line.strip()
        # Check if this line is a heading
        if stripped.startswith("##"):
            heading_words = set(re.findall(r'\b[A-Z]{3,}\b', stripped))
            # Add section only if its heading words aren't already in primary
            overlap = heading_words & primary_headings
            in_unique_section = len(overlap) == 0 and len(heading_words) > 0
        if in_unique_section:
            unique_secondary.append(line)

    result = primary
    if unique_secondary:
        result += "\n\n" + "\n".join(unique_secondary)

    return result.strip()


def _extract_tables(text: str) -> list[str]:
    """Extract all markdown table blocks from text."""
    tables = []
    lines  = text.splitlines()
    current = []
    in_table = False

    for line in lines:
        if line.strip().startswith("|"):
            in_table = True
            current.append(line)
        elif in_table:
            if current:
                tables.append("\n".join(current))
                current = []
            in_table = False
        # Handle non-table lines after table
        elif not in_table and current:
            tables.append("\n".join(current))
            current = []

    if current:
        tables.append("\n".join(current))
    return [t for t in tables if t.strip() and "---" in t]


def _extract_heading_words(text: str) -> list[str]:
    """Get all meaningful words from ## headings."""
    words = []
    for line in text.splitlines():
        if line.strip().startswith("##"):
            words.extend(re.findall(r'\b[A-Z]{3,}\b', line))
    return words


# ── 2. OCR noise removal ──────────────────────────────────────────────────────

# Single character lines that are OCR artifacts from flowchart arrows/connectors
_NOISE_LINE_RE = re.compile(r'^\s*[ignorvuhl\.•\-\*]\s*$', re.IGNORECASE)

# HTML entity cleanup
_ENTITY_MAP = {
    '&amp;': '&', '&gt;': '>', '&lt;': '<', '&ge;': '≥', '&le;': '≤',
    '&ne;': '≠', '&deg;': '°', '&nbsp;': ' ', '&#39;': "'",
    '&65': '≥65',   # common OCR error: &65 instead of ≥65
    '& 2 ': '₂ ',  # subscript artifacts
}

def clean_ocr_noise(text: str) -> str:
    """Remove OCR artifacts: single-char lines, bad encodings, flowchart fragments."""
    # Fix HTML entities
    for entity, replacement in _ENTITY_MAP.items():
        text = text.replace(entity, replacement)

    # Remove lines that are clearly OCR noise (single chars, arrows, etc.)
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip single-char noise lines
        if _NOISE_LINE_RE.match(stripped):
            continue
        # Skip lines that are ONLY pipe chars and spaces (empty table rows)
        if re.fullmatch(r'[\|\s]+', stripped) and stripped.count('|') >= 2:
            continue
        # Fix common OCR substitutions
        line = re.sub(r'\bjuris\s+M(\d)', r'prior MI', line)  # "juris M1" → "prior MI"
        line = re.sub(r'&(\d+)', r'≥\1', line)                # &65 → ≥65
        cleaned.append(line)

    # Remove runs of 3+ blank lines → single blank line
    text = "\n".join(cleaned)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── 3. Heading normalization ──────────────────────────────────────────────────

# Patterns that should NOT be headings despite being ALL-CAPS
_NOT_HEADING_PATTERNS = [
    r'^ICD[-\s]?\d+',               # ICD codes: ICD-11-BC81.31
    r'^\d+\s*[\./]\s*\d+',          # dates/fractions
    r'^[A-Z]{1,3}$',                 # abbreviations: OAC, BB, HR
    r'^\d',                          # starts with number
    r'^(YES|NO|AND|OR|THE|FOR|OF)$', # common words
]
_NOT_HEADING_RE = re.compile('|'.join(f'({p})' for p in _NOT_HEADING_PATTERNS),
                              re.IGNORECASE)


def normalize_headings(text: str) -> str:
    """Fix heading hierarchy: remove ICD codes as headings, deduplicate, set levels."""
    lines = text.splitlines()
    result = []
    seen_headings: set[str] = set()

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("##"):
            # Extract heading text (remove leading #)
            heading_text = re.sub(r'^#{1,4}\s*', '', stripped).strip()

            # Remove headings that shouldn't be headings
            if _NOT_HEADING_RE.match(heading_text):
                result.append(heading_text)  # keep as plain text
                continue

            # Deduplicate headings
            heading_key = heading_text.upper()[:30]
            if heading_key in seen_headings:
                continue  # skip duplicate
            seen_headings.add(heading_key)

            # Normalize level: most ICMR sections are ## or ###
            # Use ## for main clinical sections, ### for sub-sections
            is_subsection = any(s in heading_text.upper() for s in
                                ["ESSENTIAL", "DESIRABLE", "OPTIONAL",
                                 "AT PHC", "AT DISTRICT", "AT TERTIARY",
                                 "BASIC", "PRIMARY", "SECONDARY"])
            prefix = "###" if is_subsection else "##"
            result.append(f"{prefix} {heading_text}")
        else:
            result.append(line)

    return "\n".join(result)


# ── 4. Table cleanup ──────────────────────────────────────────────────────────

def clean_tables(text: str) -> str:
    """Clean up markdown tables: remove empty rows, fix alignment."""
    lines = text.splitlines()
    result = []
    in_table = False
    table_buffer = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and "|" in stripped[1:]:
            in_table = True
            table_buffer.append(stripped)
        else:
            if in_table and table_buffer:
                cleaned_table = _clean_table_block(table_buffer)
                result.extend(cleaned_table)
                table_buffer = []
                in_table = False
            result.append(line)

    if table_buffer:
        result.extend(_clean_table_block(table_buffer))

    return "\n".join(result)


def _clean_table_block(rows: list[str]) -> list[str]:
    """Clean a single table block."""
    if not rows:
        return []

    cleaned = []
    for row in rows:
        cells = [c.strip() for c in row.split("|")]
        cells = [c for c in cells if c or c == ""]  # preserve structure

        # Skip rows where ALL cells are empty
        if not any(c.strip() for c in cells):
            continue

        # Skip separator rows that came out wrong (e.g. "| ---- | ---- |")
        # Keep proper separator rows
        if all(re.fullmatch(r'-+', c.strip()) or not c.strip() for c in cells):
            cleaned.append(row)
            continue

        cleaned.append(row)

    # Ensure there's a separator row after the header
    if len(cleaned) >= 2:
        has_sep = any(all(re.fullmatch(r'-+', c.strip()) or not c.strip()
                          for c in r.split("|") if c.strip())
                      for r in cleaned[1:3])
        if not has_sep and cleaned:
            # Add separator after first row
            first_cells = cleaned[0].split("|")
            n_cols = len([c for c in first_cells if c.strip()])
            if n_cols > 0:
                sep = "| " + " | ".join(["---"] * n_cols) + " |"
                cleaned.insert(1, sep)

    return cleaned


# ── 5. Content deduplication ──────────────────────────────────────────────────

def deduplicate_content(text: str) -> str:
    """
    Remove duplicate paragraphs and sections.
    When the same clinical content appears twice (from GLM + pdfplumber overlap),
    keep the first occurrence.
    """
    paragraphs = re.split(r'\n{2,}', text)
    seen: set[str] = set()
    unique = []

    for para in paragraphs:
        # Create a normalized key for comparison
        key = re.sub(r'\s+', ' ', para.strip().lower())[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(para)

    return "\n\n".join(unique)


# ── 6. Final cleanup ──────────────────────────────────────────────────────────

def final_cleanup(text: str) -> str:
    """Final pass: fix whitespace, encoding, trailing artifacts."""
    # Fix subscript numbers that got separated: "CHA 2 DS 2" → "CHA₂DS₂"
    text = re.sub(r'\bCHA\s*2\s*DS\s*2\s*[-–]\s*VAS\s*[Cc]\b', 'CHA₂DS₂-VASc', text)
    text = re.sub(r'\bCHA\s+2\s+DS\s+2\b', 'CHA₂DS₂', text)

    # Remove trailing whitespace on each line
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)

    # Collapse 3+ blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Remove lines that are just dashes (artifact separators)
    text = re.sub(r'\n-{3,}\n', '\n\n', text)

    return text.strip()


# ── Main entry point ──────────────────────────────────────────────────────────

def run(text: str, strategy: str = "text_mode") -> str:
    """
    Run all post-processing steps in order.

    Steps:
      1. Merge GLM-OCR and pdfplumber sections intelligently
      2. Remove OCR noise and fix encodings
      3. Normalize heading hierarchy
      4. Clean table formatting
      5. Deduplicate repeated content
      6. Final whitespace and artifact cleanup
    """
    if not text or not text.strip():
        return text

    text = merge_sections(text, strategy)
    text = clean_ocr_noise(text)
    text = normalize_headings(text)
    text = clean_tables(text)
    text = deduplicate_content(text)
    text = final_cleanup(text)
    return text


# ── CLI for testing ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    from pathlib import Path

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/extraction/Atrial_Fibrillation.md")
    strategy = sys.argv[2] if len(sys.argv) > 2 else "text_mode"

    raw = path.read_text(encoding="utf-8")
    print(f"Input: {len(raw)} chars")
    clean = run(raw, strategy)
    print(f"Output: {len(clean)} chars")
    print("\n" + "="*60)
    print(clean[:3000])

    out = path.with_name(path.stem + "_clean.md")
    out.write_text(clean, encoding="utf-8")
    print(f"\nSaved: {out}")
