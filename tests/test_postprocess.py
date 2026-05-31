"""
tests/test_postprocess.py — unit tests for Phase 8.5 post-processing (D47).
Run with: pytest tests/test_postprocess.py -v
"""
import pytest
from cloak.quality.postprocess import (
    strip_html_comments,
    clean_latex_encoding,
    strip_exam_headers,
    strip_think_artifacts,
    deduplicate_consecutive_lines,
    deduplicate_sections,
    normalize_whitespace,
    run,
)


# ── strip_html_comments ───────────────────────────────────────────────────────

class TestStripHtmlComments:
    def test_strips_tables_artifact(self):
        text = "Some content\n<!-- TABLES: structured form of page content — use these, remove any duplicate prose above -->\nMore content"
        result = strip_html_comments(text)
        assert "TABLES:" not in result
        assert "Some content" in result
        assert "More content" in result

    def test_strips_figure_failure(self):
        text = "Before\n<!-- figure 0: vision failed (VisionTimeoutError) -->\nAfter"
        result = strip_html_comments(text)
        assert "figure 0" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_region_failure(self):
        text = "Text\n<!-- region 1: diagram — description failed: TimeoutError -->\nMore"
        result = strip_html_comments(text)
        assert "region 1" not in result

    def test_strips_multiline_comment(self):
        text = "Start\n<!-- This is a\nmultiline\ncomment -->\nEnd"
        result = strip_html_comments(text)
        assert "multiline" not in result
        assert "Start" in result
        assert "End" in result

    def test_preserves_page_markers(self):
        text = "<!-- page 1 -->\n## Heading\n<!-- page 2 -->\nContent"
        result = strip_html_comments(text)
        assert "<!-- page 1 -->" in result
        assert "<!-- page 2 -->" in result

    def test_preserves_page_markers_strips_other(self):
        text = "<!-- page 3 -->\n<!-- TABLES: remove this -->\nText"
        result = strip_html_comments(text)
        assert "<!-- page 3 -->" in result
        assert "TABLES:" not in result

    def test_no_comments_unchanged(self):
        text = "## Heading\n\nSome paragraph text.\n\n| col |\n|---|\n| val |"
        assert strip_html_comments(text) == text


# ── clean_latex_encoding ─────────────────────────────────────────────────────

class TestCleanLatexEncoding:
    def test_strips_cjk_from_inline_math(self):
        result = clean_latex_encoding(r"The formula $\mathbb定{R}$ is real numbers")
        assert "定" not in result
        assert r"\mathbb" in result

    def test_strips_cjk_from_block_math(self):
        result = clean_latex_encoding("$$\n\\frac{x定}{y}\n$$")
        assert "定" not in result
        assert "\\frac" in result

    def test_clean_math_unchanged(self):
        text = r"Let $x = \frac{a}{b}$ and $$E = mc^2$$"
        result = clean_latex_encoding(text)
        assert r"\frac{a}{b}" in result
        assert r"E = mc^2" in result

    def test_strips_multiple_cjk(self):
        result = clean_latex_encoding(r"$\alpha定\beta大\gamma$")
        assert "定" not in result
        assert "大" not in result
        assert r"\alpha" in result
        assert r"\beta" in result

    def test_no_latex_unchanged(self):
        text = "Plain text with no math at all."
        assert clean_latex_encoding(text) == text


# ── strip_exam_headers ────────────────────────────────────────────────────────

class TestStripExamHeaders:
    def test_removes_repeated_gate_header(self):
        text = (
            "GATE 2024\n"
            "## Q.1\nWhat is...\n"
            "GATE 2024\n"
            "## Q.2\nSolve...\n"
            "GATE 2024\n"
        )
        result = strip_exam_headers(text)
        assert result.count("GATE 2024") == 1

    def test_removes_all_page_numbers(self):
        # Page N of M are always stripped regardless of value — all are noise
        text = "Page 1 of 36\n## Q.1\nText\nPage 2 of 36\n## Q.2\nMore\nPage 3 of 36\n"
        result = strip_exam_headers(text)
        assert "Page" not in result

    def test_keeps_first_occurrence(self):
        text = "GATE 2024\n## Introduction\nGATE 2024\n## Questions\n"
        result = strip_exam_headers(text)
        assert "GATE 2024" in result
        assert result.count("GATE 2024") == 1

    def test_removes_iit_header(self):
        text = "IIT Bombay\n## Section A\nText\nIIT Bombay\n## Section B\nMore\n"
        result = strip_exam_headers(text)
        assert result.count("IIT Bombay") == 1

    def test_non_exam_doc_unchanged(self):
        text = "## Introduction\n\nThis is a research paper.\n\n## Methodology\n"
        assert strip_exam_headers(text) == text


# ── strip_think_artifacts ─────────────────────────────────────────────────────

class TestStripThinkArtifacts:
    def test_strips_think_block(self):
        text = "<think>This is my reasoning</think>\nActual content"
        result = strip_think_artifacts(text)
        assert "<think>" not in result
        assert "Actual content" in result

    def test_strips_think_inline(self):
        text = "Some text /think and more reasoning here\nNext line"
        result = strip_think_artifacts(text)
        assert "/think" not in result
        assert "Some text" in result

    def test_clean_text_unchanged(self):
        text = "## Heading\n\nNormal paragraph text."
        assert strip_think_artifacts(text) == text


# ── deduplicate_consecutive_lines ─────────────────────────────────────────────

class TestDeduplicateConsecutiveLines:
    def test_removes_consecutive_duplicates(self):
        text = "Line A\nLine A\nLine B\n"
        result = deduplicate_consecutive_lines(text)
        assert result.count("Line A") == 1

    def test_keeps_non_consecutive_duplicates(self):
        text = "Line A\nLine B\nLine A\n"
        result = deduplicate_consecutive_lines(text)
        assert result.count("Line A") == 2

    def test_blank_lines_not_deduplicated(self):
        text = "Para 1\n\n\nPara 2"
        result = deduplicate_consecutive_lines(text)
        assert "Para 1" in result
        assert "Para 2" in result

    def test_no_duplicates_unchanged(self):
        text = "A\nB\nC\nD"
        assert deduplicate_consecutive_lines(text) == text


# ── normalize_whitespace ──────────────────────────────────────────────────────

class TestNormalizeWhitespace:
    def test_collapses_excess_blank_lines(self):
        text = "Para 1\n\n\n\n\nPara 2"
        result = normalize_whitespace(text)
        assert "\n\n\n" not in result
        assert "Para 1" in result
        assert "Para 2" in result

    def test_strips_trailing_spaces(self):
        text = "Line with spaces   \nAnother line  "
        result = normalize_whitespace(text)
        for line in result.split("\n"):
            assert line == line.rstrip()

    def test_two_blank_lines_preserved(self):
        text = "Para 1\n\nPara 2"
        result = normalize_whitespace(text)
        assert "Para 1\n\nPara 2" in result


# ── deduplicate_sections ─────────────────────────────────────────────────────

class TestDeduplicateSections:
    def test_removes_shorter_duplicate_heading(self):
        text = (
            "## GENERAL MEASURES\n\nStep 1\nStep 2\n\n"
            "## OTHER SECTION\n\nSome content\n\n"
            "## GENERAL MEASURES\n\nStep 1\nStep 2\nStep 3\nStep 4\n\n"  # longer
        )
        result = deduplicate_sections(text)
        assert result.count("## GENERAL MEASURES") == 1
        # Should keep the longer version (4 steps)
        assert "Step 4" in result

    def test_keeps_longer_duplicate_heading(self):
        text = (
            "## DRUGS\n\nAspirin 325mg\nClopidogrel 300mg\nPrasugrel 60mg\n\n"
            "## OTHER\n\nContent\n\n"
            "## DRUGS\n\nAspirin\n\n"   # shorter — should be dropped
        )
        result = deduplicate_sections(text)
        assert result.count("## DRUGS") == 1
        assert "Clopidogrel" in result   # longer version kept

    def test_no_duplicates_unchanged(self):
        text = "## Section A\n\nContent A\n\n## Section B\n\nContent B\n"
        result = deduplicate_sections(text)
        assert "## Section A" in result
        assert "## Section B" in result
        assert "Content A" in result
        assert "Content B" in result

    def test_pre_heading_content_preserved(self):
        text = "Title line\nSome intro\n\n## Section\n\nBody\n"
        result = deduplicate_sections(text)
        assert "Title line" in result
        assert "Some intro" in result

    def test_real_stemi_pattern(self):
        # Simulates the actual stemi duplication pattern
        text = (
            "## GENERAL MEASURES\n\nAdmit in ICU\nPain relief\n\n"
            "## PCI CAPABLE HOSPITAL\n\nProceed for PCI\n\n"
            "## GENERAL MEASURES\n\nAdmit in ICU\nPain relief\nO2 if saturation below 90%\nAspirin 325mg\n\n"
        )
        result = deduplicate_sections(text)
        assert result.count("## GENERAL MEASURES") == 1
        assert "Aspirin 325mg" in result   # longer version with aspirin kept
        assert "## PCI CAPABLE HOSPITAL" in result


# ── run (integration) ─────────────────────────────────────────────────────────

class TestRun:
    def test_run_cleans_all_artifacts(self):
        text = (
            "<!-- TABLES: use these -->\n"
            "## Section\n\n"
            "$\\mathbb定{R}$\n\n"
            "GATE 2024\nContent\nGATE 2024\n\n"
            "<think>reasoning</think>\n"
            "Same line\nSame line\n\n\n\n\n"
            "End"
        )
        result = run(text)
        assert "TABLES:" not in result
        assert "定" not in result
        assert result.count("GATE 2024") == 1
        assert "<think>" not in result
        assert result.count("Same line") == 1
        assert "\n\n\n" not in result
        assert "## Section" in result
        assert "End" in result

    def test_run_clean_doc_unchanged_structure(self):
        text = "## Introduction\n\nThis is body text.\n\n## Methods\n\nMore text."
        result = run(text)
        assert "## Introduction" in result
        assert "## Methods" in result
        assert "body text" in result
