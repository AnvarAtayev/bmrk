from unittest.mock import MagicMock, patch

import pytest

from bmrk.detector import (
    NoReadableTextError,
    _assign_heading_levels,
    _extract_numbered_depth,
    _infer_numeric_level_offset,
    _is_body_starter_with_continuation,
    _is_colon_math_introducer,
    _is_likely_diagram_label,
    _is_likely_equation_line,
    _is_likely_plain_heading_text,
    _is_math_span,
    _is_noise,
    _is_numeric_continuation_line,
    _is_numeric_table_row,
    _is_toc_continuation_page,
    _is_toc_page,
    _numeric_depth,
    detect_headings,
)

# ---------------------------------------------------------------------------
# _is_noise
# ---------------------------------------------------------------------------


class TestIsNoise:
    def test_empty_string(self):
        assert _is_noise("") is True

    def test_whitespace_only(self):
        assert _is_noise("   ") is True

    def test_single_char(self):
        assert _is_noise("A") is True

    def test_exactly_min_length_not_noise(self):
        assert _is_noise("Hi") is False

    def test_exactly_max_length_not_noise(self):
        assert _is_noise("x" * 200) is False

    def test_over_max_length(self):
        assert _is_noise("x" * 201) is True

    def test_bare_number(self):
        assert _is_noise("42") is True

    def test_page_label(self):
        assert _is_noise("page 5") is True

    def test_figure_caption(self):
        assert _is_noise("figure 3") is True

    def test_table_caption(self):
        assert _is_noise("table 1") is True

    def test_fig_abbreviation(self):
        assert _is_noise("fig. 3") is True

    def test_valid_heading(self):
        assert _is_noise("Introduction") is False

    def test_valid_numbered_heading(self):
        assert _is_noise("1  Introduction") is False

    def test_valid_subsection(self):
        assert _is_noise("2.3  Related Work") is False


# ---------------------------------------------------------------------------
# _is_math_span
# ---------------------------------------------------------------------------


class TestIsMathSpan:
    def test_summation_symbol(self):
        assert _is_math_span("\u2211") is True

    def test_integral_symbol(self):
        assert _is_math_span("\u222b") is True

    def test_product_symbol(self):
        assert _is_math_span("\u220f") is True

    def test_greek_letter(self):
        assert _is_math_span("\u03b1") is True

    def test_arrow_symbol(self):
        assert _is_math_span("\u2192") is True

    def test_ascii_math_expression(self):
        assert _is_math_span("(x+y)") is True

    def test_parenthesized_function(self):
        assert _is_math_span("f(x)") is True

    def test_real_heading_not_filtered(self):
        assert _is_math_span("Introduction") is False

    def test_numbered_heading_not_filtered(self):
        assert _is_math_span("Chapter 1") is False

    def test_long_text_not_filtered(self):
        assert _is_math_span("This is a long heading title") is False

    def test_empty_string(self):
        assert _is_math_span("") is False

    def test_mixed_below_threshold(self):
        # "Results (overview)" -- 2 parens in 18 chars, well below 50%
        assert _is_math_span("Results (overview)") is False

    def test_single_equals(self):
        assert _is_math_span("=") is True


# ---------------------------------------------------------------------------
# _numeric_depth
# ---------------------------------------------------------------------------


class TestNumericDepth:
    def test_single_level(self):
        assert _numeric_depth("1") == 1

    def test_two_levels(self):
        assert _numeric_depth("2.3") == 2

    def test_three_levels(self):
        assert _numeric_depth("1.2.3") == 3

    def test_four_levels(self):
        assert _numeric_depth("1.2.3.4") == 4

    def test_trailing_dot_stripped(self):
        assert _numeric_depth("2.") == 1

    def test_appendix_style(self):
        assert _numeric_depth("A.1") == 2

    def test_appendix_no_number(self):
        assert _numeric_depth("A") == 1


# ---------------------------------------------------------------------------
# _extract_numbered_depth
# ---------------------------------------------------------------------------


class TestExtractNumberedDepth:
    def test_two_space_numeric_heading(self):
        assert _extract_numbered_depth("2.3  Methods") == 2

    def test_single_space_dotted_numeric_heading(self):
        assert _extract_numbered_depth("2.1 Section Title") == 2

    def test_single_space_plain_numeric_rejected(self):
        assert _extract_numbered_depth("1 Introduction") is None

    def test_single_space_appendix_prefix_rejected(self):
        assert _extract_numbered_depth("A Appendix") is None

    def test_numeric_title_after_prefix_rejected(self):
        assert _extract_numbered_depth("0.8 7= 3 months") is None

    def test_equation_like_title_after_prefix_rejected(self):
        assert _extract_numbered_depth("2.1 x= y+1") is None


# ---------------------------------------------------------------------------
# _infer_numeric_level_offset
# ---------------------------------------------------------------------------


class TestInferNumericLevelOffset:
    def test_anchor_keeps_absolute_depth(self):
        assert _infer_numeric_level_offset([2, 2, 3], has_anchor=True) == 0

    def test_no_anchor_normalizes_min_depth_to_level_one(self):
        assert _infer_numeric_level_offset([2, 2, 3], has_anchor=False) == 1

    def test_depth_one_no_shift(self):
        assert _infer_numeric_level_offset([1, 2], has_anchor=False) == 0


# ---------------------------------------------------------------------------
# _is_numeric_table_row
# ---------------------------------------------------------------------------


class TestIsNumericTableRow:
    def test_decimal_table_values_detected(self):
        assert _is_numeric_table_row("1.623 5.018 8.000 10.613 12.199") is True

    def test_numeric_line_with_symbols_detected(self):
        assert _is_numeric_table_row("209.13 59.42 32.66 %") is True

    def test_numbered_heading_not_table_row(self):
        assert _is_numeric_table_row("2.1 Section Title") is False


# ---------------------------------------------------------------------------
# _is_likely_diagram_label
# ---------------------------------------------------------------------------


class TestIsLikelyDiagramLabel:
    def test_symbol_heavy_oversized_text_detected(self):
        assert _is_likely_diagram_label("6H )-O", size=28.0, body_size=9.5) is True

    def test_short_oversized_token_detected(self):
        assert _is_likely_diagram_label("/ \\ IN", size=40.0, body_size=9.5) is True

    def test_normal_heading_not_detected(self):
        assert _is_likely_diagram_label("River crossing", size=11.5, body_size=9.5) is False


# ---------------------------------------------------------------------------
# _is_likely_equation_line
# ---------------------------------------------------------------------------


class TestIsLikelyEquationLine:
    def test_operator_dense_line_detected(self):
        assert _is_likely_equation_line("N=2>5=8a+4b+2c+d") is True

    def test_trailing_equals_detected(self):
        assert _is_likely_equation_line("Solution setup, y_n =") is True

    def test_short_trig_formula_detected(self):
        assert _is_likely_equation_line("sin x cos x tan x = sec x") is True

    def test_question_with_math_tail_detected(self):
        assert _is_likely_equation_line("What is the derivative of y = ln x^x?") is True

    def test_short_ocr_math_fragment_detected(self):
        assert _is_likely_equation_line("Ine 5 Inz") is True

    def test_heading_prefix_with_formula_suffix_detected(self):
        assert _is_likely_equation_line("Example technique: [u dv = uv - [v du") is True

    def test_regular_heading_not_detected(self):
        assert _is_likely_equation_line("Expected Value and Variance") is False


# ---------------------------------------------------------------------------
# _is_likely_plain_heading_text
# ---------------------------------------------------------------------------


class TestIsLikelyPlainHeadingText:
    def test_normal_heading_detected(self):
        assert _is_likely_plain_heading_text("Sample Section Title") is True

    def test_short_formula_token_rejected(self):
        assert _is_likely_plain_heading_text("X2") is False

    def test_equation_like_token_soup_rejected(self):
        assert _is_likely_plain_heading_text("(n+1)! 3! 5!") is False

    def test_inline_question_marker_rejected(self):
        assert _is_likely_plain_heading_text("A. What is i?") is False


# ---------------------------------------------------------------------------
# _is_body_starter_with_continuation
# ---------------------------------------------------------------------------


class TestIsBodyStarterWithContinuation:
    def test_starter_followed_by_short_math_and_lowercase_line_detected(self):
        cur = {"text": "Let A be a sample value and x |", "size": 14.0, "top": 100.0, "page": 0}
        nxt = {"text": "x", "size": 11.0, "top": 106.0, "page": 0}
        nxt2 = {
            "text": "be its corresponding entry under this setup.",
            "size": 9.5,
            "top": 116.0,
            "page": 0,
        }

        assert _is_body_starter_with_continuation(cur, nxt, nxt2) is True

    def test_normal_heading_not_detected(self):
        cur = {"text": "Sample Subsection", "size": 14.0, "top": 100.0, "page": 0}
        nxt = {"text": "Additional Notes", "size": 11.0, "top": 130.0, "page": 0}
        nxt2 = {"text": "More text", "size": 9.5, "top": 145.0, "page": 0}

        assert _is_body_starter_with_continuation(cur, nxt, nxt2) is False


# ---------------------------------------------------------------------------
# _is_numeric_continuation_line
# ---------------------------------------------------------------------------


class TestIsNumericContinuationLine:
    def test_wrapped_question_tail_detected(self):
        prev = {
            "text": "The previous sentence continues onto the next visual line with more detail",
            "size": 9.5,
            "top": 100.0,
            "page": 0,
            "bold": False,
            "italic": False,
        }
        cur = {
            "text": "50 items?",
            "size": 10.2,
            "top": 111.0,
            "page": 0,
            "bold": False,
            "italic": False,
        }

        assert _is_numeric_continuation_line(cur, prev) is True

    def test_numbered_heading_not_detected(self):
        prev = {
            "text": "Closing sentence.",
            "size": 9.5,
            "top": 100.0,
            "page": 0,
            "bold": False,
            "italic": False,
        }
        cur = {
            "text": "12. Example Topic",
            "size": 11.0,
            "top": 140.0,
            "page": 0,
            "bold": False,
            "italic": False,
        }

        assert _is_numeric_continuation_line(cur, prev) is False


# ---------------------------------------------------------------------------
# _is_colon_math_introducer
# ---------------------------------------------------------------------------


class TestIsColonMathIntroducer:
    def test_label_followed_by_math_fragments_detected(self):
        cur = {"text": "Example rule:", "size": 10.1, "top": 100.0, "page": 0}
        nxt = {"text": "x", "size": 12.0, "top": 120.0, "page": 0}
        nxt2 = {"text": "1/2", "size": 12.0, "top": 136.0, "page": 0}

        assert _is_colon_math_introducer(cur, nxt, nxt2) is True

    def test_normal_heading_with_prose_not_detected(self):
        cur = {"text": "Sample Heading:", "size": 10.1, "top": 100.0, "page": 0}
        nxt = {"text": "Additional prose follows here", "size": 9.5, "top": 120.0, "page": 0}
        nxt2 = {"text": "and continues normally.", "size": 9.5, "top": 134.0, "page": 0}

        assert _is_colon_math_introducer(cur, nxt, nxt2) is False


# ---------------------------------------------------------------------------
# _assign_heading_levels
# ---------------------------------------------------------------------------


class TestAssignHeadingLevels:
    def test_empty_input(self):
        assert _assign_heading_levels([]) == {}

    def test_single_size(self):
        result = _assign_heading_levels([16.0])
        assert result == {16.0: 1}

    def test_two_sizes(self):
        result = _assign_heading_levels([14.0, 16.0])
        assert result[16.0] == 1
        assert result[14.0] == 2

    def test_three_sizes(self):
        result = _assign_heading_levels([12.0, 14.0, 16.0])
        assert result[16.0] == 1
        assert result[14.0] == 2
        assert result[12.0] == 3

    def test_four_sizes_capped_at_level_three(self):
        result = _assign_heading_levels([11.0, 12.0, 14.0, 16.0])
        assert result[16.0] == 1
        assert result[14.0] == 2
        assert result[12.0] == 3
        assert result[11.0] == 3  # overflow capped at 3

    def test_duplicate_sizes_treated_as_one(self):
        result = _assign_heading_levels([14.0, 14.0, 16.0])
        assert result[16.0] == 1
        assert result[14.0] == 2
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _is_toc_page
# ---------------------------------------------------------------------------


class TestIsTocPage:
    def _span(self, text: str) -> dict:
        return {"text": text, "size": 12.0, "bold": False, "page": 0}

    def test_toc_page_detected(self):
        # Majority of lines look like "Title .......... 5"
        spans = [
            self._span("Introduction ......... 1"),
            self._span("Methods .............. 5"),
            self._span("Results .............. 12"),
            self._span("Discussion ........... 18"),
            self._span("Conclusion ........... 25"),
        ]
        assert _is_toc_page(spans) is True

    def test_normal_page_not_toc(self):
        spans = [
            self._span("This is a sentence of body text."),
            self._span("Another line of body text here."),
            self._span("Introduction"),
        ]
        assert _is_toc_page(spans) is False

    def test_too_few_spans_not_toc(self):
        spans = [self._span("Intro ........ 1"), self._span("Methods ...... 5")]
        assert _is_toc_page(spans) is False

    def test_toc_heading_detected_even_with_few_lines(self):
        spans = [
            self._span("Table of Contents"),
            self._span("Chapter 2 Topic ... 3"),
        ]
        assert _is_toc_page(spans) is True

    def test_ocr_toc_entry_pattern_detected(self):
        spans = [
            self._span("2.1 Section Title ..................cccccc 3"),
            self._span("2.2 Section Two ............................ 5"),
            self._span("2.3 Section Three .......................... 10"),
            self._span("2.4 Section Four ........................... 15"),
        ]
        assert _is_toc_page(spans) is True

    def test_spaced_contents_heading_detected(self):
        spans = [
            self._span("C O N T E N T S"),
            self._span("Chapter 5"),
            self._span("Example Topic"),
            self._span("73"),
        ]
        assert _is_toc_page(spans) is True

    def test_toc_continuation_page_detected(self):
        spans = [
            self._span("Chapter 5"),
            self._span("Example Topic"),
            self._span("73"),
            self._span("Subtopic A"),
            self._span("81"),
            self._span("Subtopic B"),
            self._span("90"),
            self._span("Chapter 6"),
            self._span("Another Topic"),
            self._span("105"),
        ]
        assert _is_toc_continuation_page(spans) is True


# ---------------------------------------------------------------------------
# detect_headings (fitz/PyMuPDF mocked)
# ---------------------------------------------------------------------------

_FLAGS_REGULAR = 0
_FLAGS_ITALIC = 2


def _make_span(
    text: str,
    size: float,
    flags: int = _FLAGS_REGULAR,
    top: float = 100.0,
    left: float = 0.0,
) -> dict:
    """
    Build a PyMuPDF-style span dict for a single text line.

    The default top=100 places text well inside the body zone for a standard
    792pt page (clear of the 8% header margin at ~63pt).
    """
    return {
        "text": text,
        "size": size,
        "flags": flags,
        "bbox": (left, top, left + len(text) * size * 0.6, top + size),
    }


def _make_line(*spans: dict) -> dict:
    """Build a PyMuPDF-style line dict from one or more spans."""
    x0 = min(s["bbox"][0] for s in spans)
    y0 = min(s["bbox"][1] for s in spans)
    x1 = max(s["bbox"][2] for s in spans)
    y1 = max(s["bbox"][3] for s in spans)
    return {"bbox": (x0, y0, x1, y1), "spans": list(spans)}


def _make_mock_doc(spans: list[list[dict]], page_height: float = 792.0):
    """
    Build a mock fitz document.

    spans: list of span-dict lists, one per page.  Each span dict becomes
    its own line within a single text block on that page.
    page_height: simulated page height in points (default: US Letter).
    """
    mock_doc = MagicMock()
    mock_pages = []
    for span_list in spans:
        page = MagicMock()
        lines = []
        for item in span_list:
            if "spans" in item:
                lines.append({"bbox": item["bbox"], "spans": item["spans"]})
            else:
                lines.append(
                    {
                        "bbox": (
                            item["bbox"][0],
                            item["bbox"][1],
                            500.0,
                            item["bbox"][1] + item["size"],
                        ),
                        "spans": [item],
                    }
                )
        block = {"type": 0, "bbox": (0.0, 0.0, 500.0, page_height), "lines": lines}
        page.get_text.return_value = {"blocks": [block] if lines else []}
        page.rect = MagicMock()
        page.rect.height = page_height
        mock_pages.append(page)

    mock_doc.__len__ = MagicMock(return_value=len(mock_pages))
    mock_doc.__iter__ = MagicMock(side_effect=lambda: iter(mock_pages))
    return mock_doc


class TestDetectHeadings:
    @patch("bmrk.detector.fitz")
    def test_empty_pdf_raises_no_readable_text_error(self, mock_fitz):
        mock_doc = _make_mock_doc([[]])  # one page, no spans
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        with pytest.raises(NoReadableTextError):
            detect_headings("dummy.pdf")

    @patch("bmrk.detector.fitz")
    def test_font_size_heading_detected(self, mock_fitz):
        # Body at 12pt (long text dominates), heading at 18pt
        body = _make_span("body text here and more", 12.0, top=100)
        heading = _make_span("Introduction", 18.0, top=200)
        mock_doc = _make_mock_doc([[body, heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "Introduction"
        assert result[0].level == 1
        assert result[0].page == 0

    @patch("bmrk.detector.fitz")
    def test_numeric_prefix_heading_detected(self, mock_fitz):
        # All same font size -- only numeric prefix triggers detection
        body = _make_span("body text here and more", 12.0, top=100)
        section = _make_span("1  Introduction", 12.0, top=200)
        mock_doc = _make_mock_doc([[body, section]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "1  Introduction"
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_numeric_table_row_not_detected_as_heading(self, mock_fitz):
        body = _make_span("body text here and more", 12.0, top=100)
        table_vals = _make_span("1.623 5.018 8.000 10.613 12.199", 12.0, top=200)
        mock_doc = _make_mock_doc([[body, table_vals]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert result == []

    @patch("bmrk.detector.fitz")
    def test_subsection_numeric_prefix_depth(self, mock_fitz):
        body = _make_span("body text here and more", 12.0, top=100)
        subsection = _make_span("2.3  Methods", 12.0, top=200)
        mock_doc = _make_mock_doc([[body, subsection]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        # Without chapter anchors, depth inference normalizes minimum numeric
        # depth to level 1 for this document.
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_noise_line_not_a_heading(self, mock_fitz):
        # "42" with large font -- should be filtered as noise
        body = _make_span("body text here and more", 12.0, top=100)
        noise = _make_span("42", 18.0, top=200)
        mock_doc = _make_mock_doc([[body, noise]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert result == []

    @patch("bmrk.detector.fitz")
    def test_multiple_heading_levels_assigned(self, mock_fitz):
        h1 = _make_span("Chapter One", 20.0, top=100)
        h2 = _make_span("Section 1.1", 16.0, top=200)
        body = _make_span("body text here and more", 12.0, top=300)
        mock_doc = _make_mock_doc([[h1, h2, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 2
        h1_entry = next(e for e in result if e.title == "Chapter One")
        h2_entry = next(e for e in result if e.title == "Section 1.1")
        assert h1_entry.level == 1
        assert h2_entry.level == 2

    @patch("bmrk.detector.fitz")
    def test_max_depth_filters_deeper_headings(self, mock_fitz):
        # With max_depth=1 only H1 headings should remain; H2 is dropped.
        h1 = _make_span("Chapter One", 20.0, top=100)
        h2 = _make_span("Section 1.1", 16.0, top=200)
        body = _make_span("body text here and more", 12.0, top=300)
        mock_doc = _make_mock_doc([[h1, h2, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05, max_depth=1)

        assert len(result) == 1
        assert result[0].title == "Chapter One"
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_max_depth_2_keeps_h1_and_h2(self, mock_fitz):
        h1 = _make_span("Chapter One", 20.0, top=100)
        h2 = _make_span("Section 1.1", 16.0, top=200)
        h3 = _make_span("Detail", 14.0, top=300)
        body = _make_span("body text here and more", 12.0, top=400)
        mock_doc = _make_mock_doc([[h1, h2, h3, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05, max_depth=2)

        titles = [e.title for e in result]
        assert "Chapter One" in titles
        assert "Section 1.1" in titles
        assert "Detail" not in titles

    @patch("bmrk.detector.fitz")
    def test_adjacent_duplicate_titles_deduplicated(self, mock_fitz):
        # Same heading text twice in a row (running header pattern)
        heading1 = _make_span("Methods", 18.0, top=100)
        heading2 = _make_span("Methods", 18.0, top=200)
        body = _make_span("body text here and more", 12.0, top=300)
        mock_doc = _make_mock_doc([[heading1, heading2, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        methods_entries = [e for e in result if e.title == "Methods"]
        assert len(methods_entries) == 1

    @patch("bmrk.detector.fitz")
    def test_headings_across_multiple_pages(self, mock_fitz):
        body = _make_span("body text here and more", 12.0, top=100)
        h_p0 = _make_span("Introduction", 18.0, top=200)
        h_p1 = _make_span("Conclusion", 18.0, top=200)
        mock_doc = _make_mock_doc([[body, h_p0], [body, h_p1]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 2
        assert result[0].title == "Introduction"
        assert result[0].page == 0
        assert result[1].title == "Conclusion"
        assert result[1].page == 1

    @patch("bmrk.detector.fitz")
    def test_font_size_takes_priority_over_numeric_prefix(self, mock_fitz):
        # A line matching both signals: font-size level should win
        body = _make_span("body text here and more", 12.0, top=100)
        # "1.2  Sub" at 20pt -- font-size gives level 1, numeric gives level 2
        heading = _make_span("1.2  Sub", 20.0, top=200)
        mock_doc = _make_mock_doc([[body, heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_numeric_signal_deepens_level_when_chapter_anchor_present(self, mock_fitz):
        # Chapter and subsection share the same size; numeric depth should still
        # infer subsection nesting when a chapter anchor exists.
        body = _make_span("x" * 200, 12.0, top=300)
        chapter = _make_span("Chapter 2 Topic", 20.0, top=100)
        subsection = _make_span("2.1 Section Title", 20.0, top=200)
        mock_doc = _make_mock_doc([[chapter, subsection, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05, merge_chapter_labels=False)

        levels = {e.title: e.level for e in result}
        assert levels["Chapter 2 Topic"] == 1
        assert levels["2.1 Section Title"] == 2

    @patch("bmrk.detector.fitz")
    def test_numeric_depth_normalized_without_anchor(self, mock_fitz):
        # If only depth-2 numbering exists and no chapter anchor is present,
        # infer level-1 for that depth to avoid over-nesting.
        body = _make_span("x" * 200, 12.0, top=300)
        subsection = _make_span("2.1 Section Title", 20.0, top=200)
        mock_doc = _make_mock_doc([[subsection, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "2.1 Section Title"
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_chapter_anchor_forced_to_level_one(self, mock_fitz):
        # Even if size ranking would place chapter openers lower, chapter
        # anchors should remain top-level and numeric subsections should nest.
        body = _make_span("x" * 200, 12.0, top=400)
        title = _make_span("THE BOOK TITLE", 30.0, top=80)
        chapter = _make_span("Chapter 2 Topic", 20.0, top=140)
        subsection = _make_span("2.1 Section Title", 20.0, top=220)
        mock_doc = _make_mock_doc([[title, chapter, subsection, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05, merge_chapter_labels=False)

        levels = {e.title: e.level for e in result}
        assert levels["Chapter 2 Topic"] == 1
        assert levels["2.1 Section Title"] == 2

    @patch("bmrk.detector.fitz")
    def test_styled_chapter_anchor_is_level_one(self, mock_fitz):
        # Chapter opener rendered at body size but styled should still be top-level.
        body = _make_span("x" * 200, 12.0, top=300)
        chapter = _make_span("Chapter 2 Topic", 12.0, flags=_FLAGS_ITALIC, top=200)
        mock_doc = _make_mock_doc([[body, chapter]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05, merge_chapter_labels=False)

        assert len(result) == 1
        assert result[0].title == "Chapter 2 Topic"
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_skip_pages_excludes_leading_pages(self, mock_fitz):
        body = _make_span("body text here and more", 12.0, top=100)
        cover_heading = _make_span("Cover Title", 24.0, top=200)
        real_heading = _make_span("Introduction", 18.0, top=200)
        mock_doc = _make_mock_doc([[body, cover_heading], [body, real_heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", skip_pages=1)

        titles = [e.title for e in result]
        assert "Cover Title" not in titles
        assert "Introduction" in titles

    @patch("bmrk.detector.fitz")
    def test_margin_header_excluded_from_headings(self, mock_fitz):
        # Running header in the top margin (top=20 on a 792pt page => 2.5%, inside 8% zone)
        body = _make_span("body text here and more", 12.0, top=100)
        running_header = _make_span("FOREWORD", 18.0, top=20)  # inside header margin
        real_heading = _make_span("Introduction", 18.0, top=150)
        mock_doc = _make_mock_doc([[body, running_header, real_heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05, header_margin=0.08)

        titles = [e.title for e in result]
        assert "FOREWORD" not in titles
        assert "Introduction" in titles

    @patch("bmrk.detector.fitz")
    def test_running_header_deduplicated_across_pages(self, mock_fitz):
        # "FOREWORD" appears as a heading on 4 consecutive pages -- only the
        # first occurrence (the actual chapter start) should be kept.
        body = _make_span("body text here and more", 12.0, top=100)
        heading = _make_span("FOREWORD", 18.0, top=150)
        mock_doc = _make_mock_doc(
            [
                [body, heading],
                [body, heading],
                [body, heading],
                [body, heading],
            ]
        )
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        foreword_entries = [e for e in result if e.title == "FOREWORD"]
        assert len(foreword_entries) == 1
        assert foreword_entries[0].page == 0  # only the first page kept

    @patch("bmrk.detector.fitz")
    def test_on_page_callback_called_for_each_page(self, mock_fitz):
        body = _make_span("body text here and more", 12.0, top=400)
        mock_doc = _make_mock_doc([[body], [body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        calls: list[tuple[int, int]] = []
        detect_headings("dummy.pdf", on_page=lambda cur, tot: calls.append((cur, tot)))

        assert calls == [(0, 2), (1, 2)]

    @patch("bmrk.detector.fitz")
    def test_italic_body_size_heading_detected(self, mock_fitz):
        # Italic text at body size should be captured as a level-3 heading
        body = _make_span("body text here and more", 12.0, flags=_FLAGS_REGULAR, top=100)
        italic_heading = _make_span("Abstract", 12.0, flags=_FLAGS_ITALIC, top=200)
        mock_doc = _make_mock_doc([[body, italic_heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "Abstract"
        assert result[0].level == 3

    @patch("bmrk.detector.fitz")
    def test_italic_too_long_not_a_heading(self, mock_fitz):
        # Very long italic line (bibliography, body sentence) must be suppressed
        body = _make_span("body text here and more", 12.0, top=100)
        bib = _make_span(
            "Smith, John. The Long Book Title: A Very Long Subtitle Here.",
            12.0,
            flags=_FLAGS_ITALIC,
            top=200,
        )
        mock_doc = _make_mock_doc([[body, bib]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert result == []

    @patch("bmrk.detector.fitz")
    def test_italic_lowercase_start_not_a_heading(self, mock_fitz):
        # Italic line starting with lowercase is a sentence continuation, not a heading
        body = _make_span("body text here and more", 12.0, top=100)
        fragment = _make_span("continued on the next line", 12.0, flags=_FLAGS_ITALIC, top=200)
        mock_doc = _make_mock_doc([[body, fragment]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert result == []

    @patch("bmrk.detector.fitz")
    def test_italic_ends_period_not_a_heading(self, mock_fitz):
        # Italic line ending with a period is a sentence/dedication, not a heading
        body = _make_span("body text here and more", 12.0, top=100)
        dedication = _make_span("To my family.", 12.0, flags=_FLAGS_ITALIC, top=200)
        mock_doc = _make_mock_doc([[body, dedication]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert result == []

    @patch("bmrk.detector.fitz")
    def test_italic_mid_paragraph_fragment_not_a_heading(self, mock_fitz):
        # Repro for false positives like:
        # "In a classic reference, A History of Ideas, ..."
        # where one line is italic-heavy inside a normal paragraph.
        before = _make_span(
            "innovations in human history were born in the golden era at the end of",
            12.0,
            top=200,
        )
        italic_line = _make_span(
            "In a classic reference, A History of Ideas, Author",
            12.0,
            flags=_FLAGS_ITALIC,
            top=214,
        )
        after = _make_span(
            "and Collaborator compile a list of major milestones",
            12.0,
            top=228,
        )
        mock_doc = _make_mock_doc([[before, italic_line, after]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert italic_line["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_bibliography_italic_entries_not_detected(self, mock_fitz):
        # Italic book titles inside a bibliography section must not be picked
        # up as styled headings.  "Bibliography" itself (large font) is fine.
        body = _make_span("x" * 200, 12.0, top=100)
        bib_heading = _make_span("Bibliography", 24.0, top=200)
        bib_entry = _make_span(
            "From Dawn to Decadence",
            12.0,
            flags=_FLAGS_ITALIC,
            top=300,
        )
        mock_doc = _make_mock_doc([[body, bib_heading, bib_entry]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "Bibliography" in titles
        assert "From Dawn to Decadence" not in titles

    @patch("bmrk.detector.fitz")
    def test_wrapped_italic_heading_merged(self, mock_fitz):
        # An italic subsection title that wraps across two lines should be
        # merged into a single heading.
        body = _make_span("x" * 200, 12.0, top=100)
        line1 = _make_span(
            "Distributed Systems and Their",
            12.0,
            flags=_FLAGS_ITALIC,
            top=300,
        )
        line2 = _make_span(
            "Applications in Practice",
            12.0,
            flags=_FLAGS_ITALIC,
            top=314,
        )
        mock_doc = _make_mock_doc([[body, line1, line2]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == ("Distributed Systems and Their Applications in Practice")
        assert result[0].level == 3

    @patch("bmrk.detector.fitz")
    def test_numeric_prefix_requires_two_spaces(self, mock_fitz):
        # "A sentence" (1 space) must NOT be detected as a heading via numeric prefix.
        # Only "A  Heading" (2+ spaces) qualifies.
        body = _make_span("body text here and more", 12.0, top=100)
        false_positive = _make_span("A sentence starting with capital letter", 12.0, top=200)
        real_heading = _make_span("A  Appendix Title", 12.0, top=300)
        mock_doc = _make_mock_doc([[body, false_positive, real_heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "A sentence starting with capital letter" not in titles
        assert "A  Appendix Title" in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_lowercase_start_not_a_heading(self, mock_fitz):
        # A large-font line starting with a lowercase letter (e.g. "by Author Name")
        # is a byline or sentence fragment, not a heading.
        body = _make_span("body text here and more", 12.0, top=100)
        byline = _make_span("by Jane Smith", 18.0, top=200)
        mock_doc = _make_mock_doc([[body, byline]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert result == []

    @patch("bmrk.detector.fitz")
    def test_pass1_trailing_period_not_a_heading(self, mock_fitz):
        # A large-font line ending with a period is a sentence (dedication, caption),
        # not a heading.
        body = _make_span("body text here and more", 12.0, top=100)
        dedication = _make_span(
            "To my wife and daughter, who give me a reason to write.", 14.0, top=200
        )
        mock_doc = _make_mock_doc([[body, dedication]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert result == []

    @patch("bmrk.detector.fitz")
    def test_pass1_normal_heading_not_filtered(self, mock_fitz):
        # A valid large-font heading with uppercase start and no trailing period
        # must still be detected normally after the new guards.
        body = _make_span("body text here and more", 12.0, top=100)
        heading = _make_span("Introduction", 18.0, top=200)
        mock_doc = _make_mock_doc([[body, heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "Introduction"

    @patch("bmrk.detector.fitz")
    def test_pass1_paragraph_lead_not_detected_as_heading(self, mock_fitz):
        # OCR can inflate the first body line slightly above threshold.
        # Ensure a long lead line followed by lowercase continuation is not
        # treated as a heading.
        section = _make_span("4. Example Topic", 11.4, top=160)
        lead = _make_span(
            "Clearly describe your approach and write down the key steps involved",
            10.0,
            top=200,
        )
        cont = _make_span(
            "to ensure another reader can follow the reasoning process.",
            9.7,
            top=214,
        )
        body = _make_span("x" * 200, 9.4, top=260)
        mock_doc = _make_mock_doc([[section, lead, cont, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "4. Example Topic" in titles
        assert lead["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_sentence_fragment_ending_comma_not_detected(self, mock_fitz):
        # A sentence fragment ending with comma and followed by continuation
        # should not be treated as a heading.
        section = _make_span("5. Example Topic", 11.4, top=160)
        fragment = _make_span(
            "Clearly we are evaluating option A, yet under this setup,",
            10.0,
            top=200,
        )
        cont = _make_span(
            "Metric(X) = 0 because event probability is below threshold.",
            9.7,
            top=214,
        )
        body = _make_span("x" * 200, 9.4, top=260)
        mock_doc = _make_mock_doc([[section, fragment, cont, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "5. Example Topic" in titles
        assert fragment["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_sentence_fragment_with_short_continuation_not_detected(self, mock_fitz):
        # OCR can make a sentence fragment line appear oversized; if followed
        # by a short continuation line, suppress it as body text.
        section = _make_span("6. Example Topic", 11.4, top=160)
        fragment = _make_span(
            "Clearly the expected value is the minimum time for all items to reach the",
            12.8,
            top=200,
        )
        # Larger-than-normal gap mirrors OCR jitter in scanned PDFs.
        cont = _make_span("target.", 9.7, top=232)
        body = _make_span("x" * 200, 9.4, top=260)
        mock_doc = _make_mock_doc([[section, fragment, cont, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "6. Example Topic" in titles
        assert fragment["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_sentence_like_line_before_solution_not_detected(self, mock_fitz):
        # A sentence-like problem statement line can be oversized by OCR and
        # followed by "Solution:" (uppercase), which should still be treated
        # as body text rather than a heading.
        section = _make_span("7. Example Topic", 11.4, top=160)
        sentence = _make_span(
            "We notice when value A exceeds value B, the estimate changes materially.",
            10.8,
            top=200,
        )
        solution = _make_span(
            "Solution: Start with the baseline setup and apply one simplification.",
            9.8,
            top=230,
        )
        body = _make_span("x" * 200, 9.4, top=260)
        mock_doc = _make_mock_doc([[section, sentence, solution, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "7. Example Topic" in titles
        assert sentence["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_diagram_label_artifacts_not_detected(self, mock_fitz):
        # OCR snippets from diagram interiors can be large but should not be
        # treated as headings.
        section = _make_span("8. Example Topic", 11.4, top=160)
        diagram_token_1 = _make_span("6H )-O", 28.0, top=220)
        diagram_token_2 = _make_span("/ \\ IN", 40.0, top=250)
        body = _make_span("x" * 200, 9.4, top=320)
        mock_doc = _make_mock_doc([[section, diagram_token_1, diagram_token_2, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "8. Example Topic" in titles
        assert diagram_token_1["text"] not in titles
        assert diagram_token_2["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_equation_lines_not_detected(self, mock_fitz):
        section = _make_span("9. Example Topic", 11.4, top=160)
        eq1 = _make_span("N=0=>0=d", 10.8, top=220)
        eq2 = _make_span("N=1>1=a+b+c+d N=2>5=8a+4b+2c+d", 10.7, top=240)
        eq3 = _make_span("sin x cos x tan x = sec x", 10.9, top=270)
        eq4 = _make_span("What is the derivative of y = ln x^x?", 10.9, top=300)
        eq5 = _make_span("Example technique: [u dv = uv - [v du", 10.9, top=330)
        body = _make_span("x" * 200, 9.4, top=360)
        mock_doc = _make_mock_doc([[section, eq1, eq2, eq3, eq4, eq5, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "9. Example Topic" in titles
        assert eq1["text"] not in titles
        assert eq2["text"] not in titles
        assert eq3["text"] not in titles
        assert eq4["text"] not in titles
        assert eq5["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_low_quality_plain_fragments_not_detected(self, mock_fitz):
        section = _make_span("10. Example Topic", 11.4, top=160)
        good_subheading = _make_span("Sample Subsection", 10.9, top=200)
        fragment1 = _make_span("X2", 10.9, top=230)
        fragment2 = _make_span("(n+1)! 3! 5!", 10.9, top=260)
        fragment3 = _make_span("A. What is i?", 10.9, top=290)
        body = _make_span("x" * 200, 9.4, top=340)
        mock_doc = _make_mock_doc(
            [[section, good_subheading, fragment1, fragment2, fragment3, body]]
        )
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "10. Example Topic" in titles
        assert good_subheading["text"] in titles
        assert fragment1["text"] not in titles
        assert fragment2["text"] not in titles
        assert fragment3["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_body_starter_with_math_continuation_not_detected(self, mock_fitz):
        section = _make_span("11. Example Topic", 11.4, top=160)
        sentence = _make_span("Let A be a sample value and x |", 14.0, top=210)
        math_fragment = _make_span("x", 11.0, top=216)
        continuation = _make_span(
            "be its corresponding entry under this setup.",
            9.5,
            top=226,
        )
        body = _make_span("x" * 200, 9.4, top=280)
        mock_doc = _make_mock_doc([[section, sentence, math_fragment, continuation, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "11. Example Topic" in titles
        assert sentence["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_numeric_wrapped_question_tail_not_detected(self, mock_fitz):
        section = _make_span("12. Example Topic", 11.4, top=160)
        body1 = _make_span(
            "The question statement continues on the next line and asks for exactly",
            9.4,
            top=220,
        )
        body2 = _make_span("50 items?", 10.2, top=232)
        body3 = _make_span("Solution: Continue with the derivation here.", 9.4, top=270)
        mock_doc = _make_mock_doc([[section, body1, body2, body3]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "12. Example Topic" in titles
        assert body2["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_colon_math_label_not_detected(self, mock_fitz):
        section = _make_span("13. Example Topic", 11.4, top=160)
        label = _make_span("Example rule:", 10.1, top=220)
        math1 = _make_span("x", 12.0, top=240)
        math2 = _make_span("1/2", 12.0, top=255)
        real_heading = _make_span("Real Subheading", 11.4, top=320)
        body = _make_span("x" * 200, 9.4, top=350)
        mock_doc = _make_mock_doc([[section, label, math1, math2, real_heading, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "13. Example Topic" in titles
        assert real_heading["text"] in titles
        assert label["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_table_band_text_not_detected(self, mock_fitz):
        section = _make_span("14. Example Topic", 11.4, top=160)
        intro = _make_span("We arrange values in the sample grid below.", 9.4, top=210)
        table_row = _make_line(
            _make_span("Label ", 14.0, top=250, left=20),
            _make_span("1 ", 14.0, top=250, left=150),
            _make_span("2 ", 14.0, top=250, left=210),
            _make_span("3", 14.0, top=250, left=270),
        )
        fragment_label = _make_span("| Sample label", 14.0, top=290, left=20)
        fragment_c1 = _make_span("1", 14.0, top=300, left=150)
        fragment_c2 = _make_span("2", 14.0, top=300, left=210)
        fragment_tail = _make_span("two", 14.0, top=311, left=20)
        fragment_c3 = _make_span("3", 14.0, top=321, left=270)
        real_heading = _make_span("Sample Heading", 11.4, top=360)
        body = _make_span("x" * 200, 9.4, top=390)
        mock_doc = _make_mock_doc(
            [[
                section,
                intro,
                table_row,
                fragment_label,
                fragment_c1,
                fragment_c2,
                fragment_tail,
                fragment_c3,
                real_heading,
                body,
            ]]
        )
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "14. Example Topic" in titles
        assert real_heading["text"] in titles
        assert "Label 1 2 3" not in titles
        assert fragment_label["text"] not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_table_header_above_caption_not_detected(self, mock_fitz):
        section = _make_span("15. Example Topic", 11.4, top=120)
        header = _make_line(
            _make_span("Name ", 17.0, top=170, left=20),
            _make_span("(abbr)", 17.0, top=170, left=120),
        )
        row1 = _make_line(
            _make_span("Uniform ", 18.0, top=200, left=20),
            _make_span("P(x) ", 18.0, top=200, left=150),
            _make_span("x=a,b ", 18.0, top=200, left=230),
            _make_span("o+a ", 18.0, top=200, left=320),
            _make_span("|", 18.0, top=200, left=390),
        )
        row2 = _make_line(
            _make_span("Poisson ", 18.0, top=235, left=20),
            _make_span("P(x)= ", 18.0, top=235, left=150),
            _make_span("x=0,1 ", 18.0, top=235, left=250),
            _make_span("At", 18.0, top=235, left=340),
        )
        caption = _make_span("Table 2.1 Sample distributions", 9.5, top=280)
        real_heading = _make_span("Sample Heading", 11.4, top=330)
        body = _make_span("x" * 200, 9.4, top=360)
        mock_doc = _make_mock_doc([[section, header, row1, row2, caption, real_heading, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "15. Example Topic" in titles
        assert real_heading["text"] in titles
        assert "Name (abbr)" not in titles

    @patch("bmrk.detector.fitz")
    def test_pass1_table_code_header_not_detected(self, mock_fitz):
        section = _make_span("16. Example Topic", 11.4, top=120)
        header = _make_line(
            _make_span("Probability ", 13.5, top=250, left=20),
            _make_span("AAA ", 13.5, top=250, left=170),
            _make_span("|BBB ", 13.5, top=250, left=220),
            _make_span("[CCC ", 13.5, top=250, left=280),
            _make_span("|DDD ", 13.5, top=250, left=340),
            _make_span("EEE", 13.5, top=250, left=400),
        )
        row1 = _make_line(
            _make_span("CCC ", 15.0, top=320, left=30),
            _make_span("1/2 ", 15.0, top=320, left=170),
            _make_span("1/3 ", 15.0, top=320, left=240),
            _make_span("2/3", 15.0, top=320, left=310),
        )
        row2 = _make_line(
            _make_span("DDD ", 15.0, top=380, left=30),
            _make_span("3/4 ", 15.0, top=380, left=170),
            _make_span("1/2 ", 15.0, top=380, left=240),
            _make_span("5/8", 15.0, top=380, left=310),
        )
        caption = _make_span("Table 3.1 Sample comparison matrix", 9.5, top=430)
        real_heading = _make_span("Next Topic", 11.4, top=480)
        body = _make_span("x" * 200, 9.4, top=510)
        mock_doc = _make_mock_doc([[section, header, row1, row2, caption, real_heading, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "16. Example Topic" in titles
        assert real_heading["text"] in titles
        assert "Probability AAA |BBB [CCC |DDD EEE" not in titles

    @patch("bmrk.detector.fitz")
    def test_wrapped_heading_merged_into_single_entry(self, mock_fitz):
        # A heading split across two PDF lines must appear as one bookmark.
        body = _make_span("body text here and more", 12.0, top=400)
        line1 = _make_span("COMPUTATIONAL", 24.0, top=100)
        line2 = _make_span("METHODS", 24.0, top=130)  # gap=30 < 24*1.8
        mock_doc = _make_mock_doc([[line1, line2, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "COMPUTATIONAL METHODS"

    @patch("bmrk.detector.fitz")
    def test_separate_headings_same_size_not_merged(self, mock_fitz):
        # Two section headings at the same font size but far apart must remain
        # as distinct bookmarks even after the merge pass.
        body = _make_span("body text here and more", 12.0, top=200)
        h1 = _make_span("Introduction", 18.0, top=100)
        h2 = _make_span("Methods", 18.0, top=600)  # gap=500 >> 18*1.8
        mock_doc = _make_mock_doc([[h1, body, h2]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "Introduction" in titles
        assert "Methods" in titles

    @patch("bmrk.detector.fitz")
    def test_chapter_label_merged_with_title(self, mock_fitz):
        # "Chapter 1" followed by "Introduction" on the same page -> merged.
        body = _make_span("body text here and more", 12.0, top=500)
        label = _make_span("Chapter 1", 16.0, top=100)
        title = _make_span("Introduction", 24.0, top=250)
        mock_doc = _make_mock_doc([[label, title, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "Chapter 1 Introduction"
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_chapter_label_merge_disabled(self, mock_fitz):
        # With merge_chapter_labels=False the label and title stay separate.
        body = _make_span("body text here and more", 12.0, top=500)
        label = _make_span("Chapter 1", 16.0, top=100)
        title = _make_span("Introduction", 24.0, top=250)
        mock_doc = _make_mock_doc([[label, title, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05, merge_chapter_labels=False)

        titles = [e.title for e in result]
        assert "Chapter 1" in titles
        assert "Introduction" in titles

    @patch("bmrk.detector.fitz")
    def test_non_label_headings_not_merged(self, mock_fitz):
        # Two consecutive headings on the same page where neither is a
        # chapter/part label must remain as separate bookmarks.
        body = _make_span("body text here and more", 12.0, top=500)
        h1 = _make_span("Abstract", 18.0, top=100)
        h2 = _make_span("Introduction", 18.0, top=300)
        mock_doc = _make_mock_doc([[h1, h2, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "Abstract" in titles
        assert "Introduction" in titles

    @patch("bmrk.detector.fitz")
    def test_chapter_label_merged_with_wrapped_title(self, mock_fitz):
        # "Chapter 5" followed by a title that wraps across two PDF lines.
        body = _make_span("x" * 200, 12.0, top=600)
        label = _make_span("Chapter 5", 16.0, top=100)
        line1 = _make_span("Advances in Modern", 24.0, top=250)
        line2 = _make_span("Computing", 24.0, top=280)  # gap=30 < 24*1.8
        mock_doc = _make_mock_doc([[label, line1, line2, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "Chapter 5 Advances in Modern Computing"
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_toc_neighbor_page_skipped_when_likely_continuation(self, mock_fitz):
        # Page 1 has explicit TOC heading; page 2 is noisy continuation with
        # low but non-trivial TOC-likeness and should be skipped as well.
        p0 = [
            _make_span("Table of Contents", 18.0, top=80),
            _make_span("2.1 Section Title ... 3", 12.0, top=120),
            _make_span("2.2 Section Two ... 5", 12.0, top=140),
        ]
        p1 = [
            _make_span("2.3 Section Three", 12.0, top=100),
            _make_span("Example item A .......... 10", 12.0, top=120),
            _make_span("Example item B .......... 11", 12.0, top=140),
            _make_span("Example item C", 12.0, top=160),
            _make_span("Example item D", 12.0, top=180),
            _make_span("Example item E", 12.0, top=200),
            _make_span("Example item F", 12.0, top=220),
            _make_span("Example item G", 12.0, top=240),
        ]
        p2 = [
            _make_span("Chapter 2 Topic", 20.0, top=100),
            _make_span("x" * 200, 12.0, top=220),
        ]
        mock_doc = _make_mock_doc([p0, p1, p2])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "Table of Contents" not in titles
        assert "2.2 Section Two ... 5" not in titles
        assert "Example item A .......... 10" not in titles
        assert "Chapter 2 Topic" in titles

    @patch("bmrk.detector.fitz")
    def test_chapter_label_merged_when_title_is_one_level_deeper(self, mock_fitz):
        # Repro for chapter-openers: "Chapter 2" and "Title"
        # may be inferred as L1 and L2 but should still merge into one entry.
        body = _make_span("x" * 200, 12.0, top=600)
        label = _make_span("Chapter 2", 24.0, top=100)
        title = _make_span("Title", 20.0, top=260)
        mock_doc = _make_mock_doc([[label, title, body]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "Chapter 2 Title"
        assert result[0].level == 1

    @patch("bmrk.detector.fitz")
    def test_chapter_label_not_demoted_by_previous_page_footer(self, mock_fitz):
        # Numeric footers from the previous page must not contribute math
        # context to the first heading block on the next page.
        footer = _make_span("40", 11.0, top=740, left=243.0)
        body = _make_span("x" * 200, 12.0, top=620)
        chapter = _make_span("Chapter 4", 24.0, top=120, left=185.0)
        title = _make_span("Sample Topic", 30.0, top=200, left=142.0)
        body2 = _make_span("x" * 200, 12.0, top=420)
        mock_doc = _make_mock_doc([[body, footer], [chapter, title, body2]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert any(entry.title == "Chapter 4 Sample Topic" for entry in result)
        assert not any(entry.title == "Sample Topic" for entry in result)

    @patch("bmrk.detector.fitz")
    def test_superscript_footnote_ref_stripped_from_heading(self, mock_fitz):
        # A heading with a superscript footnote ref on the same line must
        # produce a clean title without the footnote number.
        body_span = _make_span("x" * 200, 12.0, top=100)
        heading_sp = {"text": "Distributed Systems", "size": 18.0, "flags": 0}
        super_sp = {"text": "11", "size": 8.0, "flags": 0}
        heading_line = {
            "bbox": (0.0, 200.0, 500.0, 218.0),
            "spans": [heading_sp, super_sp],
        }

        # Build mock doc with a custom block containing the multi-span line
        mock_doc = MagicMock()
        page = MagicMock()
        body_line = {
            "bbox": (0.0, 100.0, 500.0, 112.0),
            "spans": [body_span],
        }
        block = {
            "type": 0,
            "bbox": (0.0, 0.0, 500.0, 792.0),
            "lines": [body_line, heading_line],
        }
        page.get_text.return_value = {"blocks": [block]}
        page.rect = MagicMock()
        page.rect.height = 792.0
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_doc.__iter__ = MagicMock(side_effect=lambda: iter([page]))
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "Distributed Systems" in titles
        assert not any("11" in t for t in titles)

    @patch("bmrk.detector.fitz")
    def test_math_symbol_not_detected_as_heading(self, mock_fitz):
        # A summation sign at large font size should not become a heading
        body = _make_span("body text here and more", 12.0, top=100)
        math_sym = _make_span("\u2211", 24.0, top=200)
        real_heading = _make_span("Introduction", 18.0, top=300)
        mock_doc = _make_mock_doc([[body, math_sym, real_heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        titles = [e.title for e in result]
        assert "\u2211" not in titles
        assert "Introduction" in titles

    @patch("bmrk.detector.fitz")
    def test_math_expression_not_detected_as_heading(self, mock_fitz):
        # Short math expression with operators should be filtered
        body = _make_span("body text here and more", 12.0, top=100)
        math_expr = _make_span("f(x) = \u2211", 18.0, top=200)
        mock_doc = _make_mock_doc([[body, math_expr]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert result == []

    @patch("bmrk.detector.fitz")
    def test_heading_with_greek_letter_not_filtered(self, mock_fitz):
        # A real heading that happens to contain a Greek letter should NOT
        # be filtered -- it exceeds _MATH_SPAN_MAX_LEN.
        body = _make_span("x" * 200, 12.0, top=100)
        heading = _make_span("The \u03b1-Particle Experiment", 18.0, top=200)
        mock_doc = _make_mock_doc([[body, heading]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].title == "The \u03b1-Particle Experiment"
