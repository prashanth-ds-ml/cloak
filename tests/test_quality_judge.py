"""
tests/test_quality_judge.py — unit tests for quality judge pure functions (D47).
Run with: pytest tests/test_quality_judge.py -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from cloak.quality.quality_judge import (
    heuristic_judge,
    docling_coverage_score,
    _compute_structure_score,
    _word_set,
)


# ── _word_set ─────────────────────────────────────────────────────────────────

class TestWordSet:
    def test_extracts_words(self):
        ws = _word_set("The quick brown fox")
        assert "quick" in ws
        assert "brown" in ws

    def test_filters_short_words(self):
        ws = _word_set("I am the one")
        assert "one" in ws
        assert "the" in ws       # 3 chars — included by [a-z]{3,}
        assert "am" not in ws    # 2 chars — excluded
        assert "i" not in ws     # 1 char — excluded

    def test_lowercases(self):
        ws = _word_set("Hello World")
        assert "hello" in ws
        assert "world" in ws
        assert "Hello" not in ws

    def test_empty_string(self):
        assert _word_set("") == set()

    def test_numbers_excluded(self):
        ws = _word_set("value123 test 456")
        assert "test" in ws
        # numbers/mixed tokens not matched by [a-z]{3,}
        assert "value123" not in ws


# ── _compute_structure_score ─────────────────────────────────────────────────

class TestComputeStructureScore:
    def test_headings_give_bonus(self):
        md = "## Section One\n\nSome text here."
        score = _compute_structure_score(md)
        assert score > 8.0

    def test_table_gives_bonus(self):
        md = "| Col A | Col B |\n|---|---|\n| val | val |"
        score = _compute_structure_score(md)
        assert score > 8.0

    def test_page_header_pollution_penalised(self):
        md = "page 1 of 36\nContent\npage 1 of 36\nMore content"
        score = _compute_structure_score(md)
        assert score < 8.0

    def test_long_content_no_headings_penalised(self):
        md = "word " * 200   # long content, no headings
        score = _compute_structure_score(md)
        assert score < 8.0

    def test_empty_md_gets_base(self):
        score = _compute_structure_score("")
        assert 0.0 <= score <= 10.0


# ── docling_coverage_score ────────────────────────────────────────────────────

class TestDoclingCoverageScore:
    def _make_element(self, label: str, text: str = "text"):
        from dataclasses import dataclass
        @dataclass
        class MockElement:
            label: str
            text: str
            level: int = 0
            bbox_norm: tuple = (0, 0, 1, 1)
            table_md: str = ""
            caption: str = ""
        return MockElement(label=label, text=text)

    def test_perfect_coverage(self):
        elements = [
            self._make_element("section_header"),
            self._make_element("text"),
        ]
        md = "## Heading\n\nSome paragraph text here."
        score, gaps = docling_coverage_score(elements, md)
        assert score > 0.8
        assert not gaps

    def test_missing_table_penalised(self):
        elements = [
            self._make_element("section_header"),
            self._make_element("table"),
        ]
        md = "## Heading\n\nSome text but no table here."
        score, gaps = docling_coverage_score(elements, md)
        assert score < 1.0
        assert any("table" in g for g in gaps)

    def test_empty_elements_returns_full_score(self):
        score, gaps = docling_coverage_score([], "Some markdown")
        assert score == 1.0
        assert gaps == []

    def test_missing_figure_penalised(self):
        elements = [
            self._make_element("picture"),
            self._make_element("picture"),
        ]
        md = "Some text without any figures."
        score, gaps = docling_coverage_score(elements, md)
        assert score < 0.5
        assert any("picture" in g for g in gaps)


# ── heuristic_judge ───────────────────────────────────────────────────────────

class TestHeuristicJudge:
    def test_perfect_recall_high_score(self):
        source = "The patient presents with fever and cough and headache"
        md = "## Symptoms\n\nThe patient presents with fever and cough and headache"
        ps = heuristic_judge(page_num=0, page_text=source, extracted_md=md, round_num=1)
        assert ps.score >= 8.0
        assert ps.confidence == "High"
        assert ps.action == "accept"

    def test_low_recall_low_score(self):
        source = "fever cough headache nausea vomiting fatigue dizziness pain swelling redness"
        md = "fever"   # only one word captured
        ps = heuristic_judge(page_num=0, page_text=source, extracted_md=md, round_num=1)
        assert ps.score < 5.0

    def test_high_hallucination_penalised(self):
        source = "patient fever cough"
        # markdown has many words not in source
        md = "## History\n\nThe diagnosis involves complex neurological pathways with extensive documentation"
        ps = heuristic_judge(page_num=0, page_text=source, extracted_md=md, round_num=1)
        assert ps.hallucination_rate > 0.0
        # hallucination penalty should reduce the score
        assert any("hallucination" in g.lower() for g in ps.gaps) or ps.hallucination_rate > 0.2

    def test_empty_source_text_neutral(self):
        ps = heuristic_judge(page_num=0, page_text="", extracted_md="Some content", round_num=1)
        assert ps.score == pytest.approx(0.7 * 5.0 + 0.3 * _compute_structure_score("Some content"), abs=0.5)

    def test_judge_level_is_l2(self):
        ps = heuristic_judge(page_num=0, page_text="text here", extracted_md="text here", round_num=1)
        assert ps.judge_level == "L2"

    def test_judge_level_l1_l2_with_elements(self):
        from dataclasses import dataclass
        @dataclass
        class MockEl:
            label: str
            text: str = "x"
            level: int = 0
            bbox_norm: tuple = (0, 0, 1, 1)
            table_md: str = ""
            caption: str = ""

        elements = [MockEl("section_header"), MockEl("text")]
        ps = heuristic_judge(
            page_num=0,
            page_text="heading content",
            extracted_md="## Heading\n\nContent here",
            round_num=1,
            page_elements=elements,
        )
        assert ps.judge_level == "L1+L2"
        assert ps.coverage_score is not None


# ── _detect_exam_paper (from parser_agent) ────────────────────────────────────

class TestDetectExamPaper:
    def _make_page(self, text: str):
        from dataclasses import dataclass
        @dataclass
        class MockPage:
            page_num: int = 0
            text: str = ""
        return MockPage(text=text)

    def setup_method(self):
        from cloak.orchestration.parser_agent import _detect_exam_paper
        self._detect = _detect_exam_paper

    def test_gate_detected(self):
        pages = [self._make_page("GATE 2024 Computer Science and Information Technology")]
        assert self._detect(pages) is True

    def test_jee_detected(self):
        pages = [self._make_page("JEE Advanced 2023 — Maximum Marks: 180")]
        assert self._detect(pages) is True

    def test_ese_detected(self):
        pages = [self._make_page("ESE 2024 Electrical Engineering UPSC")]
        assert self._detect(pages) is True

    def test_research_paper_not_detected(self):
        pages = [self._make_page(
            "Abstract\nSection 1 Introduction\nSection 2 Related Work\n"
            "We present BERT, a language model trained on Wikipedia."
        )]
        assert self._detect(pages) is False

    def test_legal_doc_not_detected(self):
        pages = [self._make_page(
            "SUPREME COURT OF THE UNITED STATES\n"
            "Section 3 Analysis\nSection 4 Conclusion\n"
            "The judgment is hereby affirmed."
        )]
        assert self._detect(pages) is False

    def test_q_number_triggers_detection(self):
        pages = [self._make_page("Q.1 What is the value of x when Maximum Marks: 60")]
        assert self._detect(pages) is True
