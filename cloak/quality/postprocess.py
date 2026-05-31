"""
postprocess.py — Phase 8.5: deterministic markdown cleanup (D47).

Runs after the quality loop, before final.md is written.
Zero model calls. All regex/deterministic. Fully testable.

Fixes applied in order:
  strip_html_comments          — G1: remove <!-- TABLES: ... --> and failure notices
  clean_latex_encoding         — G3: strip non-ASCII from inside $...$ blocks
  strip_exam_headers           — G2: remove repeated GATE/IISc/Page X of Y lines
  strip_think_artifacts        — remove /think and <think>...</think> leakage
  deduplicate_consecutive_lines — remove runs of identical consecutive non-empty lines
  validate_table_columns       — annotate rows with column count mismatches
  normalize_whitespace         — 3+ blank lines → 2, strip trailing spaces

Entry point: run(text) → cleaned text
"""
from __future__ import annotations

import re

# ── Strip processing artifact HTML comments (G1) ─────────────────────────────

# Page markers are valid output — protect them through the strip pass
_PAGE_MARKER_RE = re.compile(r'<!--\s*page\s+\d+\s*-->', re.IGNORECASE)


def strip_html_comments(text: str) -> str:
    """
    Remove processing artifact HTML comments from extracted markdown.
    Preserves <!-- page N --> markers added during Phase 3.

    Strips:
      <!-- TABLES: structured form of page content ... -->
      <!-- figure N: vision failed (ExceptionType) -->
      <!-- figure N: degenerate bbox -->
      <!-- figure: caption (vision unavailable) -->
      <!-- region N: label — description failed ... -->
      <!-- image region N: label (vision unavailable) -->
      Any other <!-- ... --> comment not matching the page marker pattern
    """
    # Protect page markers with a unique sentinel
    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        protected.append(m.group(0))
        return f"\x00PM{len(protected) - 1}\x00"

    text = _PAGE_MARKER_RE.sub(_protect, text)
    # Strip all remaining HTML comments (including multiline)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Restore page markers
    for i, marker in enumerate(protected):
        text = text.replace(f"\x00PM{i}\x00", marker)
    return text


# ── Clean LaTeX encoding corruption (G3) ─────────────────────────────────────

def clean_latex_encoding(text: str) -> str:
    """
    Strip non-ASCII characters from inside LaTeX delimiters.
    Fixes gemma4:26b tokenizer merging CJK tokens with adjacent LaTeX commands.
    Example: \\mathbb定 → \\mathbb,  \\frac{x}{y定} → \\frac{x}{y}
    """
    def _clean(m: re.Match) -> str:
        open_d, inner, close_d = m.group(1), m.group(2), m.group(3)
        return open_d + re.sub(r"[^\x00-\x7F]", "", inner) + close_d

    # Block math $$...$$ — must come before inline to avoid partial matches
    text = re.sub(r"(\$\$)(.*?)(\$\$)", _clean, text, flags=re.DOTALL)
    # Inline math $...$ — cap at 300 chars to avoid greedily consuming prose
    text = re.sub(r"(\$)([^$\n]{1,300})(\$)", _clean, text)
    return text


# ── Strip repeated exam header/footer lines (G2) ─────────────────────────────

# Matches exam branding lines that repeat on every page in exam_mode output
_EXAM_HEADER_LINE_RE = re.compile(
    r"^(?:"
    r"GATE\s+\d{4}"
    r"|JEE\s*(?:Advanced|Main)?"
    r"|ESE\s+\d{4}|IES\s+\d{4}"
    r"|IISc(?:\s+Bengaluru)?"
    r"|IIT\s+\w+"
    r"|NIT\s+\w+"
    r"|Organizing\s+Institute\s*:.*"
    r"|Computer\s+Science\s*\(?CS\d*\)?"
    r"|Electrical\s+Engineering\s*\(?EE\d*\)?"
    r")\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Page numbers are always noise in output — strip ALL occurrences regardless of value
_PAGE_NUMBER_LINE_RE = re.compile(
    r"^Page\s+\d+\s+of\s+\d+\s*$",
    re.IGNORECASE,
)


def strip_exam_headers(text: str) -> str:
    """
    Remove exam header/footer lines from exam_mode output.
    Page numbers (Page N of M): ALL occurrences stripped — never useful in markdown.
    Other exam headers (GATE, IISc, etc.): keep first occurrence, strip subsequent.
    """
    lines = text.split("\n")
    seen: dict[str, int] = {}
    result: list[str] = []
    for line in lines:
        key = line.strip()
        if not key:
            result.append(line)
            continue
        # Page numbers: always strip
        if _PAGE_NUMBER_LINE_RE.match(key):
            continue
        # Other exam branding: keep first, drop rest
        if _EXAM_HEADER_LINE_RE.match(key):
            count = seen.get(key, 0)
            seen[key] = count + 1
            if count == 0:
                result.append(line)
        else:
            result.append(line)
    return "\n".join(result)


# ── Strip think artifacts ─────────────────────────────────────────────────────

_THINK_BLOCK_RE  = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_INLINE_RE = re.compile(r"/think\b.*$", re.MULTILINE)


def strip_think_artifacts(text: str) -> str:
    """Remove gemma4/qwen3 thinking-chain fragments that leak into output."""
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_INLINE_RE.sub("", text)
    return text


# ── Deduplicate consecutive identical lines ───────────────────────────────────

def deduplicate_consecutive_lines(text: str) -> str:
    """
    Remove runs of identical consecutive non-empty lines.
    Blank lines are not deduplicated here — normalize_whitespace handles those.
    """
    lines = text.split("\n")
    result: list[str] = []
    prev: str | None = None
    for line in lines:
        if line.strip() and line == prev:
            continue
        result.append(line)
        prev = line
    return "\n".join(result)


# ── Validate markdown table column counts ─────────────────────────────────────

_TABLE_ROW_RE = re.compile(r"^\|.+\|$")
_SEP_ROW_RE   = re.compile(r"^\|[\s|:\-]+\|$")


def validate_table_columns(text: str) -> str:
    """
    Find markdown tables with inconsistent column counts and annotate bad rows.
    Does not delete content — appends a HTML comment so the issue is visible
    without corrupting the data.
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _TABLE_ROW_RE.match(line.strip()):
            result.append(line)
            i += 1
            continue
        # Found a table row — look ahead for separator to get expected col count
        sep_idx = None
        for j in range(i + 1, min(i + 4, len(lines))):
            if _SEP_ROW_RE.match(lines[j].strip()):
                sep_idx = j
                break
        if sep_idx is None:
            # No separator found — not a proper table, leave as-is
            result.append(line)
            i += 1
            continue
        expected_cols = lines[sep_idx].count("|") - 1
        # Consume entire table block
        while i < len(lines) and _TABLE_ROW_RE.match(lines[i].strip()):
            row = lines[i]
            if not _SEP_ROW_RE.match(row.strip()):
                actual = row.count("|") - 1
                if actual != expected_cols:
                    row = row + f"  <!-- col mismatch: expected {expected_cols} got {actual} -->"
            result.append(row)
            i += 1
        continue
    return "\n".join(result)


# ── Deduplicate repeated sections ────────────────────────────────────────────

_HEADING_RE = re.compile(r"^#{1,4}\s+\S", re.MULTILINE)


def deduplicate_sections(text: str) -> str:
    """
    Remove duplicate ## heading blocks from the document.
    When the same heading appears more than once, keep the occurrence with
    the most content (longest block body). This handles the case where the
    patch agent re-adds content that was already partially captured in extraction.
    """
    # Split document into blocks by heading
    lines = text.split("\n")
    blocks: list[tuple[str, list[str]]] = []   # (heading_key, lines_including_heading)
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if _HEADING_RE.match(line):
            if current_lines:
                blocks.append((current_heading or "", current_lines))
            current_heading = line.strip().lower()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        blocks.append((current_heading or "", current_lines))

    # For each heading, keep the block with the most non-empty content lines
    seen: dict[str, int] = {}          # heading_key → index in kept_blocks
    kept_blocks: list[list[str]] = []

    for heading_key, block_lines in blocks:
        if not heading_key:
            # Pre-heading content (before any ##) — always keep
            kept_blocks.append(block_lines)
            continue
        content_len = sum(len(l.strip()) for l in block_lines[1:] if l.strip())
        if heading_key in seen:
            existing_idx = seen[heading_key]
            existing_len = sum(
                len(l.strip()) for l in kept_blocks[existing_idx][1:] if l.strip()
            )
            if content_len > existing_len:
                kept_blocks[existing_idx] = block_lines   # replace with longer version
            # else: discard this shorter/equal duplicate
        else:
            seen[heading_key] = len(kept_blocks)
            kept_blocks.append(block_lines)

    return "\n".join(line for block in kept_blocks for line in block)


# ── Normalize whitespace ──────────────────────────────────────────────────────

def normalize_whitespace(text: str) -> str:
    """
    Strip trailing whitespace from each line.
    Collapse 3+ consecutive blank lines to 2.
    """
    lines = [ln.rstrip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Main entry point ──────────────────────────────────────────────────────────

def run(text: str) -> str:
    """
    Phase 8.5: apply all post-processing steps in order.
    Called on best_round.markdown after the quality loop, before final.md is written.
    """
    text = strip_html_comments(text)
    text = clean_latex_encoding(text)
    text = strip_exam_headers(text)
    text = strip_think_artifacts(text)
    text = deduplicate_sections(text)          # section-level: same ## heading kept once
    text = deduplicate_consecutive_lines(text) # line-level: consecutive identical lines
    text = validate_table_columns(text)
    text = normalize_whitespace(text)
    return text
