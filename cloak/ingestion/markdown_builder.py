"""
Assemble a structured markdown file from:
  - LLM-cleaned body text (from text_cleaner.py)
  - pdfplumber tables (structured clinical data)
  - gemma4 vision descriptions (ECGs, diagrams)

Output format:
  # {Condition}
  Metadata table
  ---
  {LLM-cleaned sections}
  ## Clinical Reference Tables
  ## Embedded Clinical Images
  ---
  Source footer
"""
from pathlib import Path
from typing import Dict, List, Optional

from cloak.ingestion.pdf_extractor import ExtractedContent


def _render_sections(sections: Dict[str, str]) -> str:
    """Render the programmatic sections dict as markdown."""
    parts = []
    for header, body in sections.items():
        if not body.strip():
            continue
        parts.append(f"## {header.title()}")
        parts.append("")
        # Indent bullet-like lines; preserve plain paragraphs
        for line in body.splitlines():
            parts.append(line)
        parts.append("")
    return "\n".join(parts).strip()


def _pdf_type(image_descriptions: Optional[List[Dict]], tables: list) -> str:
    if image_descriptions and len(image_descriptions) >= 2:
        return "C"
    if image_descriptions:
        return "B"
    return "A"


def _tags(specialty: str, condition: str) -> List[str]:
    base = ["icmr-stw", specialty.lower()]
    cond_lower = condition.lower()
    if "fibrillation" in cond_lower or "arrhythmia" in cond_lower or "bradyarr" in cond_lower:
        base.append("arrhythmia")
    if "stemi" in cond_lower or "nstemi" in cond_lower:
        base += ["acs", "emergency"]
    if "angina" in cond_lower:
        base.append("coronary-artery-disease")
    if "heart failure" in cond_lower:
        base.append("heart-failure")
    if "pacemaker" in cond_lower or "bradyarr" in cond_lower:
        base.append("pacemaker")
    return base


def build_markdown(
    content: ExtractedContent,
    specialty: str,
    volume: str,
    source_url: str,
    condition: Optional[str] = None,
    image_descriptions: Optional[List[Dict]] = None,
) -> str:
    lines = []
    title = condition or content.condition
    pdf_type = _pdf_type(image_descriptions, content.tables)
    tags = _tags(specialty, title)

    # ── YAML frontmatter (Obsidian-compatible) ─────────────────────────────
    lines.append("---")
    lines.append(f"title: {title}")
    lines.append(f"icd_code: {content.icd_code}")
    lines.append(f"specialty: {specialty}")
    lines.append(f"volume: {volume}")
    lines.append(f'source: "{source_url}"')
    lines.append(f"pdf_type: {pdf_type}")
    lines.append(f"has_images: {str(bool(image_descriptions)).lower()}")
    lines.append(f"image_count: {len(image_descriptions) if image_descriptions else 0}")
    lines.append(f"table_count: {len(content.tables)}")
    lines.append("tags:")
    for tag in tags:
        lines.append(f"  - {tag}")
    lines.append("---")
    lines.append("")

    # ── Title + metadata ───────────────────────────────────────────────────
    lines.append(f"# {title}")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| **Specialty** | {specialty} |")
    lines.append(f"| **Volume** | {volume} |")
    lines.append(f"| **ICD Code** | {content.icd_code} |")
    lines.append(f"| **Source** | [{source_url}]({source_url}) |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Programmatic clinical sections ─────────────────────────────────────
    if content.sections:
        lines.append(_render_sections(content.sections))
        lines.append("")
    else:
        lines.append("## Clinical Content")
        lines.append("")
        lines.append(content.full_text.strip())
        lines.append("")

    # ── pdfplumber tables ──────────────────────────────────────────────────
    if content.tables:
        lines.append("---")
        lines.append("")
        lines.append("## Clinical Reference Tables")
        lines.append("")
        lines.append(
            "> The following tables are extracted directly from the PDF "
            "and contain structured clinical data including drug dosages, "
            "scoring systems, and management protocols."
        )
        lines.append("")
        for i, table in enumerate(content.tables):
            md = table.to_markdown()
            if md.strip():
                lines.append(f"### Table {i + 1}")
                lines.append("")
                lines.append(md)
                lines.append("")

    # ── Embedded images / ECGs ─────────────────────────────────────────────
    if image_descriptions:
        lines.append("---")
        lines.append("")
        lines.append("## Embedded Clinical Images")
        lines.append("")
        for item in image_descriptions:
            lines.append(f"### {item['label']}")
            lines.append("")
            lines.append(item["description"])
            lines.append("")

    # ── Footer ─────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append(
        "*Source: ICMR Standard Treatment Workflows — "
        "Indian Council of Medical Research, "
        "Ministry of Health and Family Welfare, Government of India.*"
    )

    return "\n".join(lines)


def write_markdown(
    markdown: str,
    out_dir: Path,
    condition: str,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = condition.lower().replace(" ", "_").replace("/", "_") + ".md"
    path  = out_dir / fname
    path.write_text(markdown, encoding="utf-8")
    return path
