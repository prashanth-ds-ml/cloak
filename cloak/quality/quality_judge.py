"""
quality_judge.py — typed scoring layer on top of vision_tools.judge_quality().
Converts raw model JSON into PageScore and decides the next action.
"""
from __future__ import annotations

import re as _re
from dataclasses import dataclass

from PIL import Image

from cloak.config import QUALITY_THRESHOLD, VISION_PRIMARY, VISION_TIMEOUT
from cloak.vision import vision_tools


@dataclass
class PageScore:
    page_num:        int
    score:           float
    confidence:      str    # "High" | "Medium" | "Low"
    gaps:            list[str]
    action:          str    # "accept" | "patch" | "fallback"
    round_num:       int
    model:           str
    structure_score: float = 0.0   # heuristic structural fidelity 0–10 (D31)


def _compute_structure_score(markdown: str) -> float:
    """
    Heuristic structural fidelity score 0–10 (D31).
    Checks heading hierarchy, table formatting, and absence of page header/footer pollution.
    Combined with content_score as: final = 0.7 * content + 0.3 * structure.
    """
    import re
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

    return max(0.0, min(10.0, score))


def judge(
    page_num: int,
    page_image: Image.Image,
    extracted_md: str,
    round_num: int,
    model: str = VISION_PRIMARY,
    timeout: float = VISION_TIMEOUT,
) -> PageScore:
    """
    Score extracted_md against page_image for a specific page.
    Combined score = 0.7 * content_score + 0.3 * structure_score (D31).
    Action thresholds (D3):
      score >= QUALITY_THRESHOLD (8.0) → "accept"
      score >= 5.0                     → "patch"
      score <  5.0                     → "fallback"
    """
    structure_score = _compute_structure_score(extracted_md)

    try:
        raw = vision_tools.judge_quality(page_image, extracted_md, model=model, timeout=timeout)
    except (vision_tools.VisionTimeoutError, vision_tools.VisionCallError):
        fallback_combined = round(0.7 * 5.0 + 0.3 * structure_score, 1)
        return PageScore(
            page_num=page_num, score=fallback_combined, confidence="Medium",
            gaps=["Vision judge unavailable — scored by text fallback"],
            action="patch", round_num=round_num, model=model,
            structure_score=structure_score,
        )

    content_score = max(0.0, min(10.0, float(raw.get("score", 0.0))))
    score  = round(0.7 * content_score + 0.3 * structure_score, 1)
    gaps   = [str(g) for g in raw.get("gaps", [])]
    action = _decide_action(score, raw.get("action", ""))
    confidence = "High" if score >= QUALITY_THRESHOLD else "Medium" if score >= 5.0 else "Low"

    return PageScore(
        page_num=page_num, score=score, confidence=confidence,
        gaps=gaps, action=action, round_num=round_num, model=model,
        structure_score=structure_score,
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

    avg_score = sum(r.score for r in results) / len(results)
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
