from unittest.mock import MagicMock, patch

from bmrk.layout import (
    BlockLabel,
    RawLine,
    analyze_layout,
    extract_raw_lines,
)

_FLAGS_REGULAR = 0
_FLAGS_BOLD_ITALIC = 18


def _make_span(
    text: str,
    size: float,
    flags: int = _FLAGS_REGULAR,
    top: float = 100.0,
    left: float = 0.0,
) -> dict:
    return {
        "text": text,
        "size": size,
        "flags": flags,
        "bbox": (left, top, left + len(text) * size * 0.6, top + size),
    }


def _make_line(*spans: dict, left: float | None = None, right: float | None = None) -> dict:
    x0 = left if left is not None else min(span["bbox"][0] for span in spans)
    y0 = min(span["bbox"][1] for span in spans)
    x1 = right if right is not None else max(span["bbox"][2] for span in spans)
    y1 = max(span["bbox"][3] for span in spans)
    return {"bbox": (x0, y0, x1, y1), "spans": list(spans)}


def _make_mock_doc(
    pages: list[list[dict]],
    *,
    page_width: float = 612.0,
    page_height: float = 792.0,
):
    mock_doc = MagicMock()
    mock_pages = []
    for items in pages:
        page = MagicMock()
        lines = []
        for item in items:
            if "spans" in item:
                lines.append({"bbox": item["bbox"], "spans": item["spans"]})
            else:
                lines.append(
                    {
                        "bbox": (
                            item["bbox"][0],
                            item["bbox"][1],
                            page_width,
                            item["bbox"][1] + item["size"],
                        ),
                        "spans": [item],
                    }
                )
        block = {"type": 0, "bbox": (0.0, 0.0, page_width, page_height), "lines": lines}
        page.get_text.return_value = {"blocks": [block] if lines else []}
        page.find_tables.return_value = MagicMock(tables=[])
        page.rect = MagicMock()
        page.rect.width = page_width
        page.rect.height = page_height
        mock_pages.append(page)

    mock_doc.__len__ = MagicMock(return_value=len(mock_pages))
    mock_doc.__iter__ = MagicMock(side_effect=lambda: iter(mock_pages))
    return mock_doc


def _make_raw_line(
    text: str,
    *,
    page: int = 0,
    top: float = 100.0,
    left: float = 48.0,
    size: float = 12.0,
    page_width: float = 612.0,
    page_height: float = 792.0,
) -> RawLine:
    return RawLine(
        page=page,
        text=text,
        bbox=(left, top, left + len(text) * size * 0.6, top + size),
        top=top,
        bottom=top + size,
        left=left,
        right=left + len(text) * size * 0.6,
        page_width=page_width,
        page_height=page_height,
        size=size,
        bold=False,
        italic=False,
        block_id=0,
        line_id=0,
        segment_texts=[text],
    )


class TestLayoutAnalysis:
    @patch("bmrk.detector.fitz")
    def test_extract_raw_lines_preserves_geometry_and_style(self, mock_fitz):
        line = _make_line(
            _make_span("Example Heading", 18.0, flags=_FLAGS_BOLD_ITALIC, top=120.0, left=36.0),
            left=36.0,
            right=260.0,
        )
        mock_doc = _make_mock_doc([[line]], page_width=600.0)
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        lines = extract_raw_lines("dummy.pdf")

        assert len(lines) == 1
        raw = lines[0]
        assert raw.text == "Example Heading"
        assert raw.size == 18.0
        assert raw.bold is True
        assert raw.italic is True
        assert raw.left == 36.0
        assert raw.right == 260.0
        assert raw.page_width == 600.0
        assert raw.block_id == 0
        assert raw.line_id == 0

    @patch("bmrk.detector.fitz")
    def test_multiline_prose_becomes_body_paragraph(self, mock_fitz):
        page = [
            _make_span(
                "This sample paragraph continues across multiple visual lines without ending",
                12.0,
                top=160.0,
                left=48.0,
            ),
            _make_span(
                "the thought until the next line completes the paragraph naturally",
                12.0,
                top=176.0,
                left=48.0,
            ),
            _make_span("Example Section", 18.0, top=260.0, left=48.0),
        ]
        mock_doc = _make_mock_doc([page])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        layout = analyze_layout("dummy.pdf")

        body_blocks = [block for block in layout.blocks if block.label == BlockLabel.BODY_PARAGRAPH]
        assert len(body_blocks) == 1
        assert "multiple visual lines" in body_blocks[0].text
        assert body_blocks[0].features["line_count"] == 2

    @patch("bmrk.detector.fitz")
    def test_wrapped_heading_becomes_heading_candidate(self, mock_fitz):
        page = [
            _make_span("SAMPLE", 24.0, top=100.0, left=80.0),
            _make_span("TOPIC", 24.0, top=132.0, left=80.0),
            _make_span("x" * 200, 12.0, top=240.0, left=48.0),
        ]
        mock_doc = _make_mock_doc([page])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        layout = analyze_layout("dummy.pdf")

        headings = [block for block in layout.blocks if block.label == BlockLabel.HEADING_CANDIDATE]
        assert len(headings) == 1
        assert headings[0].text == "SAMPLE TOPIC"

    @patch("bmrk.detector.fitz")
    def test_centered_formula_becomes_display_math(self, mock_fitz):
        formula = _make_line(
            _make_span("f(x) = x^2 + y^2", 14.0, top=220.0, left=220.0),
            left=210.0,
            right=390.0,
        )
        page = [
            _make_span("Example Section", 18.0, top=100.0, left=48.0),
            formula,
            _make_span("x" * 200, 12.0, top=320.0, left=48.0),
        ]
        mock_doc = _make_mock_doc([page])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        layout = analyze_layout("dummy.pdf")

        formula_block = next(block for block in layout.blocks if "f(x) =" in block.text)
        assert formula_block.label == BlockLabel.DISPLAY_MATH

    @patch("bmrk.detector.fitz")
    def test_table_rows_become_table_region(self, mock_fitz):
        table_header = _make_line(
            _make_span("Name ", 14.0, top=200.0, left=40.0),
            _make_span("1 ", 14.0, top=200.0, left=180.0),
            _make_span("2 ", 14.0, top=200.0, left=240.0),
            _make_span("3", 14.0, top=200.0, left=300.0),
        )
        table_row = _make_line(
            _make_span("Group ", 14.0, top=228.0, left=40.0),
            _make_span("4 ", 14.0, top=228.0, left=180.0),
            _make_span("5 ", 14.0, top=228.0, left=240.0),
            _make_span("6", 14.0, top=228.0, left=300.0),
        )
        page = [
            _make_span("Example Section", 18.0, top=100.0, left=48.0),
            _make_span("x" * 200, 12.0, top=150.0, left=48.0),
            table_header,
            table_row,
            _make_span("Another Section", 18.0, top=340.0, left=48.0),
        ]
        mock_doc = _make_mock_doc([page])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        layout = analyze_layout("dummy.pdf")

        table_blocks = [block for block in layout.blocks if block.label == BlockLabel.TABLE_REGION]
        assert table_blocks
        assert any("Name 1 2 3" in block.text for block in table_blocks)

    @patch("bmrk.detector.fitz")
    def test_table_caption_becomes_caption(self, mock_fitz):
        page = [
            _make_span("Table 2.1 Example values", 10.0, top=180.0, left=48.0),
            _make_span("Example Section", 18.0, top=260.0, left=48.0),
            _make_span("x" * 200, 12.0, top=320.0, left=48.0),
        ]
        mock_doc = _make_mock_doc([page])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        layout = analyze_layout("dummy.pdf")

        caption = next(block for block in layout.blocks if block.text.startswith("Table 2.1"))
        assert caption.label == BlockLabel.CAPTION

    @patch("bmrk.detector.fitz")
    def test_prompt_followed_by_solution_becomes_problem_prompt(self, mock_fitz):
        page = [
            _make_span("A. What is the sample output?", 12.0, top=180.0, left=48.0),
            _make_span(
                "Solution: Start from the baseline and simplify.",
                12.0,
                top=212.0,
                left=48.0,
            ),
            _make_span("x" * 200, 12.0, top=260.0, left=48.0),
        ]
        mock_doc = _make_mock_doc([page])
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        layout = analyze_layout("dummy.pdf")

        prompt = next(block for block in layout.blocks if block.text.startswith("A."))
        assert prompt.label == BlockLabel.PROBLEM_PROMPT

    @patch("bmrk.detector.fitz")
    def test_repeated_top_text_becomes_running_header_footer(self, mock_fitz):
        pages = []
        for page_number in range(3):
            pages.append(
                [
                    _make_span("Sample Running Header", 12.0, top=20.0, left=120.0),
                    _make_span(f"Example Section {page_number + 1}", 18.0, top=180.0, left=48.0),
                    _make_span("x" * 200, 12.0, top=260.0, left=48.0),
                ]
            )
        mock_doc = _make_mock_doc(pages)
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        layout = analyze_layout("dummy.pdf")

        header_blocks = [
            block for block in layout.blocks if block.text == "Sample Running Header"
        ]
        assert len(header_blocks) == 3
        assert all(block.label == BlockLabel.RUNNING_HEADER_FOOTER for block in header_blocks)

    @patch("bmrk.detector.fitz")
    def test_toc_page_becomes_toc_entry(self, mock_fitz):
        pages = [
            [
                _make_span("Table of Contents", 18.0, top=100.0, left=48.0),
                _make_span("1. Example Section .......... 3", 12.0, top=150.0, left=48.0),
                _make_span("2. Another Section ......... 7", 12.0, top=175.0, left=48.0),
                _make_span("3. Final Section ........... 9", 12.0, top=200.0, left=48.0),
            ],
            [
                _make_span("Example Section", 18.0, top=120.0, left=48.0),
                _make_span("x" * 200, 12.0, top=220.0, left=48.0),
            ],
        ]
        mock_doc = _make_mock_doc(pages)
        mock_fitz.open.return_value.__enter__.return_value = mock_doc

        layout = analyze_layout("dummy.pdf")

        assert layout.toc_pages == {0}
        toc_blocks = [block for block in layout.blocks if block.page == 0]
        assert toc_blocks
        assert all(block.label == BlockLabel.TOC_ENTRY for block in toc_blocks)
