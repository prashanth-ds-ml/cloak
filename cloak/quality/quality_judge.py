"""
quality_judge.py — typed scoring layer on top of vision_tools.judge_quality().
Converts raw model JSON into PageScore and decides the next action.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from cloak.config import QUALITY_THRESHOLD, VISION_PRIMARY, VISION_TIMEOUT
from cloak.vision import vision_tools


@dataclass
class PageScore:
    page_num: int
    score: float
    confidence: str    # "High" | "Medium" | "Low"
    gaps: list[str]
    action: str        # "accept" | "patch" | "fallback"
    round_num: int
    model: str


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
    Action thresholds (D3):
      score >= QUALITY_THRESHOLD (8.0) → "accept"
      score >= 5.0                     → "patch"
      score <  5.0                     → "fallback"
    """
    try:
        raw = vision_tools.judge_quality(page_image, extracted_md, model=model, timeout=timeout)
    except (vision_tools.VisionTimeoutError, vision_tools.VisionCallError):
        return PageScore(
            page_num=page_num, score=5.0, confidence="Medium",
            gaps=["Vision judge unavailable — scored by text fallback"],
            action="patch", round_num=round_num, model=model,
        )

    score  = max(0.0, min(10.0, float(raw.get("score", 0.0))))
    gaps   = [str(g) for g in raw.get("gaps", [])]
    action = _decide_action(score, raw.get("action", ""))
    confidence = "High" if score >= QUALITY_THRESHOLD else "Medium" if score >= 5.0 else "Low"

    return PageScore(
        page_num=page_num, score=score, confidence=confidence,
        gaps=gaps, action=action, round_num=round_num, model=model,
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
