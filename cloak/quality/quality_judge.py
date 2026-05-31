"""
quality_judge.py — typed scoring layer on top of vision_tools.judge_quality().
Converts raw model JSON into PageScore and decides the next action.

Judge is 4-level escalating (D47):
  L1 docling_coverage_score()  — deterministic, element inventory vs markdown
  L2 heuristic_judge()         — word recall + hallucination rate (pdfplumber independent)
  L3 glm_crosscheck()          — independent model cross-validation (Sprint 1+)
  L4 judge()                   — gemma4 constrained by docling checklist (image/scanned only)
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PIL import Image

from cloak.config import QUALITY_THRESHOLD, VISION_PRIMARY, VISION_TIMEOUT
from cloak.vision import vision_tools

if TYPE_CHECKING:
    from cloak.profiling.doc_profiler import DoclingElement


@dataclass
class PageScore:
    page_num:           int
    score:              float          # combined: 0.7*content + 0.3*structure (D31)
    confidence:         str            # "High" | "Medium" | "Low"
    gaps:               list[str]
    action:             str            # "accept" | "patch" | "fallback"
    round_num:          int
    model:              str
    content_score:      float = 0.0   # raw vision/recall completeness score
    structure_score:    float = 0.0   # heuristic structural fidelity 0–10 (D31)
    hallucination_rate: float = 0.0   # L2: fraction of md words not in pdfplumber
    coverage_score:     float | None = None  # L1: docling element coverage (0–1)
    judge_level:        str = "L4"    # which level produced the final score


def _compute_structure_score(markdown: str) -> float:
    """
    Heuristic structural fidelity score 0–10 (D31).
    Checks heading hierarchy, table formatting, and absence of extraction artifacts.
    Combined with content_score as: final = 0.7 * content + 0.3 * structure.
    """
    score = 8.0   # base — assume reasonable structure

    # Bonus: ## or ### headings present (proper heading hierarchy)
    if _re.search(r'^#{1,4}\s+\S', markdown, _re.MULTILINE):
        score += 1.0

    # Bonus: markdown tables with correct separator row
    if _re.search(r'^\|[\s:]*[-:]{3,}[\s:]*\|', markdown, _re.MULTILINE):
        score += 1.0

    # Penalty: repeating "Page N of M" or similar — page header/footer pollution
    page_num_hits = len(_re.findall(r'\bpage\s+\d+\s+of\s+\d+\b', markdown, _re.IGNORECASE))
    if page_num_hits > 1:
        score -= 2.0

    # Penalty: long content with no ## headings (likely failed heading extraction)
    if len(markdown) > 500 and not _re.search(r'^##\s+', markdown, _re.MULTILINE):
        score -= 1.0

    # Penalty: code fence blocks present (vision model artifact — content should be inline)
    fence_count = len(_re.findall(r'^```', markdown, _re.MULTILINE))
    if fence_count > 2:   # allow up to 1 genuine code block (opens + closes = 2 markers)
        score -= min(3.0, (fence_count - 2) * 0.5)

    # Penalty: vision meta-headers leaked into document (from region_describe)
    artifact_headers = len(_re.findall(
        r'^#{1,3}\s+(?:Visual Content|Concept or Information Illustrated|'
        r'Text Transcription|Visual Description|Concept Illustrated)',
        markdown, _re.MULTILINE | _re.IGNORECASE
    ))
    if artifact_headers > 0:
        score -= min(2.0, artifact_headers * 0.5)

    return max(0.0, min(10.0, score))


# ── L1: Docling element coverage check ───────────────────────────────────────

def docling_coverage_score(
    page_elements: list[DoclingElement],  # type: ignore[valid-type]
    extracted_md: str,
) -> tuple[float, list[str]]:
    """
    L1 judge: verify docling-detected elements appear in extracted markdown.
    Deterministic — no model call. Uses the element inventory built in Phase 1.

    Returns (coverage_score 0.0–1.0, gaps list).
    coverage_score = weighted fraction of expected elements found.
    Weights: table > formula > picture > section_header > text
    """
    if not page_elements:
        return 1.0, []

    weights = {
        "table":          3.0,
        "formula":        2.5,
        "picture":        2.0,
        "section_header": 1.5,
        "title":          1.5,
        "text":           1.0,
        "list_item":      0.8,
    }
    total_weight = 0.0
    found_weight = 0.0
    gaps: list[str] = []

    # Count expected vs found by element type
    from collections import Counter
    expected = Counter(el.label for el in page_elements if el.label in weights)

    for label, count in expected.items():
        w = weights.get(label, 1.0)
        total_weight += w * count
        found = _count_element_in_md(label, extracted_md, count)
        found_weight += w * found
        if found < count:
            gaps.append(f"L1: {label} expected {count}, found ~{found}")

    coverage = found_weight / total_weight if total_weight > 0 else 1.0
    return round(coverage, 3), gaps


def _count_element_in_md(label: str, md: str, expected: int) -> int:
    """Heuristic count of a docling element type in extracted markdown."""
    if label in ("table",):
        found = len(_re.findall(r"^\|[\s:]*[-:]{3,}[\s:]*\|", md, _re.MULTILINE))
        return min(found, expected)
    if label in ("formula",):
        found = len(_re.findall(r"\$\$|\$[^$\n]+\$|`[^`]+`", md))
        return min(found, expected)
    if label in ("picture",):
        found = len(_re.findall(r"!\[", md))
        return min(found, expected)
    if label in ("section_header", "title"):
        found = len(_re.findall(r"^#{1,4}\s+\S", md, _re.MULTILINE))
        return min(found, expected)
    # text, list_item — assume present if markdown has any content at all
    return expected if len(md.strip()) > 20 else 0


# ── L2: Heuristic judge — word recall + hallucination rate ───────────────────

_HALLUCINATION_PENALTY_THRESHOLD = 0.20   # >20% fabricated words → penalise


def heuristic_judge(
    page_num: int,
    page_text: str,
    extracted_md: str,
    round_num: int,
    model: str = "heuristic",
    page_elements: list | None = None,   # DoclingElement list for L1 (optional)
) -> PageScore:
    """
    L2 judge: word recall + hallucination rate against pdfplumber ground truth (D33, D47).
    Optionally combines with L1 docling coverage when page_elements provided.
    No vision call — runs in microseconds.
    """
    structure_score = _compute_structure_score(extracted_md)

    raw_words = _word_set(page_text)
    md_words  = _word_set(extracted_md)
    gaps: list[str] = []

    if raw_words:
        recall = len(raw_words & md_words) / len(raw_words)
        content_score = min(10.0, recall * 10.0)

        # L2: hallucination rate — words in markdown not in pdfplumber source
        if md_words:
            hallucination_rate = len(md_words - raw_words) / len(md_words)
        else:
            hallucination_rate = 0.0

        if hallucination_rate > _HALLUCINATION_PENALTY_THRESHOLD:
            penalty = hallucination_rate * 4.0   # e.g. 30% hallucination → -1.2 pts
            content_score = max(0.0, content_score - penalty)
            gaps.append(
                f"L2: high hallucination rate {hallucination_rate:.0%} "
                f"— {len(md_words - raw_words)} words not in source"
            )
    else:
        content_score = 5.0        # no pdfplumber text — neutral
        hallucination_rate = 0.0

    # L1: docling coverage — blend in when elements available
    coverage_score: float | None = None
    judge_level = "L2"
    if page_elements:
        coverage_score, l1_gaps = docling_coverage_score(page_elements, extracted_md)
        gaps.extend(l1_gaps)
        # Blend: coverage acts as a floor — if L1 says 60% of elements missing, cap score
        coverage_as_score = coverage_score * 10.0
        content_score = min(content_score, coverage_as_score + 1.0)  # allow 1pt grace
        judge_level = "L1+L2"

    score      = round(0.7 * content_score + 0.3 * structure_score, 1)
    confidence = "High" if score >= QUALITY_THRESHOLD else "Medium" if score >= 5.0 else "Low"
    action     = _decide_action(score, "")

    return PageScore(
        page_num=page_num, score=score, confidence=confidence,
        gaps=gaps, action=action, round_num=round_num, model=model,
        content_score=content_score, structure_score=structure_score,
        hallucination_rate=hallucination_rate,
        coverage_score=coverage_score,
        judge_level=judge_level,
    )


def judge(
    page_num: int,
    page_image: Image.Image,
    extracted_md: str,
    round_num: int,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
    page_elements: list | None = None,   # DoclingElement list for grounded prompt (L4)
) -> PageScore:
    """
    L4 judge: gemma4 constrained by docling checklist (D47).
    Used only for image_heavy / mixed / scanned / exam pages where L1+L2 can't cover.
    When page_elements provided, prompt is grounded: model verifies a checklist it didn't write.
    Combined score = 0.7 * content_score + 0.3 * structure_score (D31).
    """
    structure_score = _compute_structure_score(extracted_md)

    try:
        raw = vision_tools.judge_quality(
            page_image, extracted_md,
            model=model, timeout=timeout,
            page_elements=page_elements,
        )
    except (vision_tools.VisionTimeoutError, vision_tools.VisionCallError):
        fallback_combined = round(0.7 * 5.0 + 0.3 * structure_score, 1)
        return PageScore(
            page_num=page_num, score=fallback_combined, confidence="Medium",
            gaps=["Vision judge unavailable — scored by text fallback"],
            action="patch", round_num=round_num, model=model,
            content_score=5.0, structure_score=structure_score,
            judge_level="L4-fallback",
        )

    content_score = max(0.0, min(10.0, float(raw.get("score", 0.0))))
    score  = round(0.7 * content_score + 0.3 * structure_score, 1)
    gaps   = [str(g) for g in raw.get("gaps", [])]
    action = _decide_action(score, raw.get("action", ""))
    confidence = "High" if score >= QUALITY_THRESHOLD else "Medium" if score >= 5.0 else "Low"
    judge_level = "L1+L4" if page_elements else "L4"

    return PageScore(
        page_num=page_num, score=score, confidence=confidence,
        gaps=gaps, action=action, round_num=round_num, model=model,
        content_score=content_score, structure_score=structure_score,
        judge_level=judge_level,
    )


def _decide_action(score: float, model_action: str) -> str:
    if score >= QUALITY_THRESHOLD:
        return "accept"
    if score >= 5.0:
        if model_action == "fallback":
            return "fallback"
        return "patch"
    return "fallback"


def aggregate_page_results(results: list[PageScore]) -> tuple[float, list[str], str]:
    """
    Combine per-page PageScores into a single (avg_score, all_gaps, action).
    Action escalates: if any page returns "fallback", the whole round is "fallback".
    """
    if not results:
        return 0.0, [], "patch"

    avg_score = round(sum(r.score for r in results) / len(results), 1)
    all_gaps  = [gap for r in results for gap in r.gaps]

    if any(r.action == "fallback" for r in results):
        action = "fallback"
    elif avg_score >= QUALITY_THRESHOLD:
        action = "accept"
    else:
        action = "patch"

    return avg_score, all_gaps, action


# ── Quality metrics ───────────────────────────────────────────────────────────

@dataclass
class QualityMetrics:
    judge_score:        float       # avg vision judge 0–10 (0.0 when judge didn't run)
    coverage_rate:      float       # fraction of pages scoring ≥ QUALITY_THRESHOLD (0–1)
    completeness_ratio: float       # word overlap: final_md vs pdfplumber raw (0–1)
    heading_count:      int         # ## / ### headings found in final markdown
    table_count:        int         # distinct markdown tables found
    judged:             bool        # False when vision judge didn't run (text-only path)
    review_score:       float | None = None  # Phase 9 model score; None if not run


def _word_set(text: str) -> set[str]:
    """Lowercase alphabetic words of 3+ chars — used for completeness ratio."""
    return set(_re.findall(r'\b[a-z]{3,}\b', text.lower()))


def compute_metrics(
    page_scores: list[PageScore],
    pages,           # list[PageData] — only .text property accessed
    final_md: str,
    review_score: float | None = None,
) -> QualityMetrics:
    """Compute all quality dimensions from page scores + document text."""
    n = len(page_scores)
    judged = n > 0
    judge_score = round(sum(ps.score for ps in page_scores) / n, 1) if judged else 0.0
    coverage    = sum(1 for ps in page_scores if ps.score >= QUALITY_THRESHOLD) / n if judged else 0.0

    raw_text  = " ".join(getattr(pg, "text", "") or "" for pg in pages)
    raw_words = _word_set(raw_text)
    md_words  = _word_set(final_md)
    completeness = round(len(raw_words & md_words) / len(raw_words), 2) if raw_words else 1.0

    headings = len(_re.findall(r"^#{1,6}\s+\S", final_md, _re.MULTILINE))
    tables   = len(_re.findall(r"^\|[\s:]*[-:]+[\s:]*\|", final_md, _re.MULTILINE))

    return QualityMetrics(
        judge_score        = judge_score,
        coverage_rate      = round(coverage, 2),
        completeness_ratio = completeness,
        heading_count      = headings,
        table_count        = tables,
        judged             = judged,
        review_score       = review_score,
    )
