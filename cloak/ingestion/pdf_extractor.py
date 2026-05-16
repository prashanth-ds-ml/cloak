"""
Extract clean, well-structured text from ICMR STW PDFs.

Strategy:
- pymupdf raw text is already reasonably ordered (top-to-bottom within columns)
- Strip boilerplate with regex before any further processing
- Detect section headers with a general ALL-CAPS heuristic (not a fixed list)
- Split content into named sections
- Extract tables with pdfplumber
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import fitz
import pdfplumber


# ── Boilerplate patterns to strip ─────────────────────────────────────────────

_BOILERPLATE_PATTERNS = [
    # Ministry / ICMR header
    r"Department of Health Research\s*\n?",
    r"Ministry of Health and Family Welfare,?\s*Government of India\s*\n?",
    r"Standard Treatment Work\w*\s*\(STW\)[^\n]*\n?",
    # Date stamps
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)[/ ]+\d{4}\s*\n?",
    # Disclaimer block (multi-line)
    r"This STW has been prepared by national experts.*?web portal[^\n]*\(stw\.icmr\.org\.in\)[^\n]*\n?",
    r"© Indian Council of Medical Research[^\n]*\n?",
    r"Kindly visit our web portal[^\n]*\n?",
    r"stw\.icmr\.org\.in\s*\n?",
    # Bibliography / numbered reference citations — strip individual lines,
    # NOT everything after REFERENCES (some PDFs put REFERENCES early due to column order)
    r"^\s*\d+\.\s+\d+\.\s+[A-Z][a-z]+[^\n]+\n?",          # "1. 1. Byrne RA et al..."
    r"^\s*\d+\.\s+[A-Z][a-z]+[^\n]+(doi|J Am|European|Lancet|Am Fam|uptodate\.com|ncbi|pubmed)[^\n]*\n?",
    r"^\s*\d+\.\s+https?://[^\n]+\n?",                      # numbered URL references
    r"^\s*https?://[^\n]+\n?",                               # bare URL lines
]

# Known slogans/taglines that are ALL-CAPS but are NOT section headers
_SLOGAN_LINES = {
    # Cardiology
    "MISSION: LIFELINE",
    "RESTORING RHYTHM, RESTORING FREEDOM",
    "LISTEN TO YOUR HEART: PREDICTABLE PAIN NEEDS PREDICTABLE CARE",
    "LISTEN TO YOUR HEART",
    "ASSESS SWIFTLY, TREAT PRECISELY",
    "BRADYARRHYTHMIA MANAGEMENT: RESTORING LIFE'S NATURAL RHYTHM",
    "MONITOR, MANAGE, AND MAINTAIN HEART HEALTH",
    # Neurology
    "KEEP A HIGH THRESHOLD FOR INVASIVE PROCEDURES",
    "MULTIDISCIPLINARY CARE",
    "ABBREVIATIONS",   # handled separately at the end
}

# Lines to always drop even if not caught by regex
_DROP_EXACT = {
    "REFERENCES", "ABBREVIATIONS",
    "ECG: SINUS BRADYCARDIA", "ECG: THIRD DEGREE HEART BLOCK",
    "LEGEND/ INDEX/ KEY", "LEGEND/INDEX/KEY", "LEGEND", "INDEX/ KEY",
}

# Drug dose / IV infusion unit pattern — lines like "CIV 1-15 MG/KG/HR" are not headers
_DOSE_UNIT_PAT = re.compile(
    r"\d+\s*[-–]?\s*\d*\s*(MG|ML|MCG|MG/KG|U/KG|MEQ|UNITS)(/KG)?(/HR|/MIN|/DAY)?",
    re.IGNORECASE,
)


# ── Section header detection ───────────────────────────────────────────────────

def _is_section_header(line: str) -> bool:
    """
    General heuristic: a section header is a short ALL-CAPS phrase (≤ 9 words)
    that is at least 85% uppercase alphabetic characters.
    """
    s = line.strip()
    if not s or len(s) < 4:
        return False
    # Lines starting with bullets or punctuation are content, not headers
    if s[0] in "•-*→·1234567890(":
        return False
    if s in _SLOGAN_LINES or s in _DROP_EXACT:
        return False
    # Strip trailing colons and asterisks for analysis
    s_check = s.rstrip(":* ")
    if not s_check:
        return False
    alpha = [c for c in s_check if c.isalpha()]
    if not alpha:
        return False
    uc_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
    if uc_ratio < 0.85:
        return False
    words = s_check.split()
    if len(words) > 9:          # too long to be a header
        return False
    # Single-word short abbreviations (≤6 chars) are not headers (CAD, CHF, etc.)
    if len(words) == 1 and len(s_check) <= 6:
        return False
    # Comma-separated abbreviation lists are not headers (e.g. "CAD, LVH")
    if "," in s and len(words) <= 4:
        return False
    # Exclude lines that look like drug doses ("ASPIRIN 325 MG/DAY", "CIV 1-15 MG/KG/HR")
    digit_count = sum(1 for c in s_check if c.isdigit())
    if digit_count > 3 and digit_count / len(s_check) > 0.2:
        return False
    if _DOSE_UNIT_PAT.search(s_check):
        return False
    return True


def _clean_header(raw: str) -> str:
    """Strip trailing noise characters from a confirmed section header."""
    return raw.strip().rstrip(":* ").strip()


# ── Boilerplate removal ────────────────────────────────────────────────────────

_LIGATURE_MAP = str.maketrans({
    "ﬀ": "ff",  # ﬀ
    "ﬁ": "fi",  # ﬁ
    "ﬂ": "fl",  # ﬂ
    "ﬃ": "ffi", # ﬃ
    "ﬄ": "ffl", # ﬄ
    "’": "'",   # right single quote
    "‘": "'",   # left single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "–": "-",   # en dash
    "—": "-",   # em dash
})


_BIBLIOGRAPHY_LINE = re.compile(
    r"^\s*\d+\.\s+[A-Z][a-z]+.*(?:19|20)\d{2}",   # numbered citation with a year
    re.IGNORECASE,
)


def _strip_boilerplate(text: str) -> str:
    # Normalize ligatures and typographic characters first
    text = text.translate(_LIGATURE_MAP)
    for pat in _BOILERPLATE_PATTERNS:
        text = re.sub(pat, "", text, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
    lines = text.splitlines()
    cleaned = []
    in_references = False
    for line in lines:
        s = line.strip()
        if s in _SLOGAN_LINES:
            continue
        # Stateful: once we hit a REFERENCES heading, skip numbered citations
        # until we reach a non-citation line (clinical content in next column)
        if s == "REFERENCES":
            in_references = True
            continue
        if in_references:
            if not s or re.match(r"^\s*\d+\.\s+", s) or re.match(r"^\s*https?://", s) or _BIBLIOGRAPHY_LINE.match(s):
                continue
            in_references = False  # non-citation line → back to clinical content
        cleaned.append(line)
    return "\n".join(cleaned).strip()


# ── Section splitting ──────────────────────────────────────────────────────────

_ABBREV_LINE = re.compile(r"^[A-Z]{2,8}:\s+[A-Z][a-z]")


def _is_abbrev_only_body(body: str) -> bool:
    """Return True when a section body contains nothing but abbreviation definitions."""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    return bool(lines) and all(_ABBREV_LINE.match(l) for l in lines)


def _split_into_sections(text: str) -> Dict[str, str]:
    lines = text.splitlines()
    sections: Dict[str, str] = {}
    current = "OVERVIEW"
    buf: List[str] = []
    abbrev_buf: List[str] = []

    for line in lines:
        s = line.strip()

        # Collect abbreviation definitions (e.g. "ECG: Electrocardiogram")
        # Short lines only — avoids matching clinical sentences like "ECG: If ST Elevation..."
        if _ABBREV_LINE.match(s) and len(s) < 55:
            abbrev_buf.append(s)
            continue

        if _is_section_header(s):
            if buf:
                sections[current] = "\n".join(buf).strip()
            current = _clean_header(s)
            buf = []
        else:
            if s:
                buf.append(s)

    if buf:
        sections[current] = "\n".join(buf).strip()

    # Post-process: move abbreviation-only bodies into ABBREVIATIONS section
    abbrev_section: List[str] = list(abbrev_buf)
    clean_sections: Dict[str, str] = {}
    for name, body in sections.items():
        if body and _is_abbrev_only_body(body):
            abbrev_section.extend(body.splitlines())
        elif body:
            clean_sections[name] = body

    if abbrev_section:
        clean_sections["ABBREVIATIONS"] = "\n".join(abbrev_section)

    return clean_sections


# ── Table extraction ───────────────────────────────────────────────────────────

@dataclass
class ExtractedTable:
    raw: List[List[Optional[str]]]

    def to_markdown(self) -> str:
        if not self.raw:
            return ""
        lines = []
        for i, row in enumerate(self.raw):
            cleaned = [
                str(c).replace("\n", " ").strip() if c else "" for c in row
            ]
            lines.append("| " + " | ".join(cleaned) + " |")
            if i == 0:
                lines.append("| " + " | ".join(["---"] * len(row)) + " |")
        return "\n".join(lines)


def _is_useful_table(tbl: List[List]) -> bool:
    """Skip single-cell footer/disclaimer tables."""
    flat = [c for row in tbl for c in row if c and str(c).strip()]
    if len(flat) <= 2:
        return False
    # Skip tables whose only content is a slogan or short phrase
    combined = " ".join(str(c) for c in flat).strip()
    if len(combined) < 20:
        return False
    return True


def _extract_tables(pdf_path: Path) -> List[ExtractedTable]:
    results = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pg in pdf.pages:
            for tbl in pg.extract_tables():
                if tbl and _is_useful_table(tbl):
                    results.append(ExtractedTable(raw=tbl))
    return results


# ── ICD / condition parsing ────────────────────────────────────────────────────

def _parse_icd(text: str) -> str:
    m = re.search(r"ICD[-\s]?(?:10|11)?[-\s]?[\w.]+", text, re.IGNORECASE)
    return m.group(0).strip() if m else "Unknown"


def _parse_condition_from_filename(pdf_path: Path) -> str:
    return pdf_path.stem.replace("_", " ").title()


# ── Public API ─────────────────────────────────────────────────────────────────

@dataclass
class ExtractedContent:
    condition:  str
    icd_code:   str
    full_text:  str           # cleaned full text
    sections:   Dict[str, str]
    tables:     List[ExtractedTable] = field(default_factory=list)


def extract_pdf(pdf_path: Path) -> ExtractedContent:
    doc  = fitz.open(str(pdf_path))
    page = doc[0]
    raw  = page.get_text("text")
    doc.close()

    icd_code  = _parse_icd(raw)
    cleaned   = _strip_boilerplate(raw)
    sections  = _split_into_sections(cleaned)
    tables    = _extract_tables(pdf_path)

    return ExtractedContent(
        condition = _parse_condition_from_filename(pdf_path),
        icd_code  = icd_code,
        full_text = cleaned,
        sections  = sections,
        tables    = tables,
    )
