from unittest.mock import MagicMock, patch

import pytest

from bmrk.detector import (
    NoReadableTextError,
    _assign_heading_levels,
    _estimate_body_size,
    _is_math_span,
    _is_noise,
    _is_toc_page,
    _merge_wrapped_headings,
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
        assert _is_math_span("\u222B") is True

    def test_product_symbol(self):
        assert _is_math_span("\u220F") is True

    def test_greek_letter(self):
        assert _is_math_span("\u03B1") is True

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
# _estimate_body_size
# ---------------------------------------------------------------------------


class TestEstimateBodySize:
    def test_empty_spans_returns_default(self):
        assert _estimate_body_size([]) == 11.0

    def test_single_span(self):
        spans = [{"text": "Hello world", "size": 12.0}]
        assert _estimate_body_size(spans) == 12.0

    def test_most_common_size_wins(self):
        spans = [
            {"text": "abc", "size": 12.0},
            {"text": "def", "size": 12.0},
            {"text": "ghi", "size": 12.0},
            {"text": "jkl", "size": 18.0},
        ]
        assert _estimate_body_size(spans) == 12.0

    def test_weighted_by_char_count(self):
        # One long span at 12pt beats many short spans at 10pt
        spans = [
            {"text": "x" * 100, "size": 12.0},
            {"text": "a", "size": 10.0},
            {"text": "b", "size": 10.0},
        ]
        assert _estimate_body_size(spans) == 12.0

    def test_rounds_to_half_point_buckets(self):
        # 12.3 and 12.4 both round to the 12.5 bucket
        spans = [
            {"text": "abc", "size": 12.3},
            {"text": "def", "size": 12.4},
        ]
        assert _estimate_body_size(spans) == 12.5


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
# _merge_wrapped_headings
# ---------------------------------------------------------------------------


class TestMergeWrappedHeadings:
    def _span(self, text: str, size: float, top: float, page: int = 0) -> dict:
        return {"text": text, "size": size, "top": top, "page": page}

    def test_empty_returns_empty(self):
        assert _merge_wrapped_headings([]) == []

    def test_single_span_unchanged(self):
        spans = [self._span("Introduction", 18.0, 100.0)]
        result = _merge_wrapped_headings(spans)
        assert len(result) == 1
        assert result[0]["text"] == "Introduction"

    def test_two_same_size_adjacent_lines_merged(self):
        spans = [
            self._span("COMPUTATIONAL", 24.0, 100.0),
            self._span("METHODS", 24.0, 130.0),  # gap = 30 < 24 * 1.8 = 43.2
        ]
        result = _merge_wrapped_headings(spans)
        assert len(result) == 1
        assert result[0]["text"] == "COMPUTATIONAL METHODS"

    def test_merged_span_stores_component_texts(self):
        spans = [
            self._span("COMPUTATIONAL", 24.0, 100.0),
            self._span("METHODS", 24.0, 130.0),
        ]
        result = _merge_wrapped_headings(spans)
        assert result[0]["_merged_texts"] == ["COMPUTATIONAL", "METHODS"]

    def test_different_sizes_not_merged(self):
        spans = [
            self._span("Chapter 1", 16.0, 100.0),
            self._span("Introduction", 24.0, 140.0),
        ]
        result = _merge_wrapped_headings(spans)
        assert len(result) == 2
        assert result[0]["text"] == "Chapter 1"
        assert result[1]["text"] == "Introduction"

    def test_large_gap_not_merged(self):
        # Gap of 200 >> 18 * 1.8 = 32.4 -- these are separate headings
        spans = [
            self._span("Introduction", 18.0, 100.0),
            self._span("Methods", 18.0, 300.0),
        ]
        result = _merge_wrapped_headings(spans)
        assert len(result) == 2

    def test_different_pages_not_merged(self):
        spans = [
            self._span("The End", 18.0, 100.0, page=0),
            self._span("Of Time", 18.0, 105.0, page=1),
        ]
        result = _merge_wrapped_headings(spans)
        assert len(result) == 2

    def test_sentence_ending_punctuation_stops_merge(self):
        # First line ends with "." — treat as separate heading, not a continuation
        spans = [
            self._span("Summary.", 18.0, 100.0),
            self._span("Overview", 18.0, 120.0),
        ]
        result = _merge_wrapped_headings(spans)
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
        "bbox": (0.0, top, len(text) * size * 0.6, top + size),
    }


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
        lines = [
            {
                "bbox": (0.0, s["bbox"][1], 500.0, s["bbox"][1] + s["size"]),
                "spans": [s],
            }
            for s in span_list
        ]
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
    def test_subsection_numeric_prefix_depth(self, mock_fitz):
        body = _make_span("body text here and more", 12.0, top=100)
        subsection = _make_span("2.3  Methods", 12.0, top=200)
        mock_doc = _make_mock_doc([[body, subsection]])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

        assert len(result) == 1
        assert result[0].level == 2  # dot-count = 1 -> depth 2

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

        result = detect_headings("dummy.pdf", size_threshold_ratio=1.05)

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
        assert result[0].title == (
            "Distributed Systems and Their Applications in Practice"
        )
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
