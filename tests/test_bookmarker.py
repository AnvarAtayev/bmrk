from unittest.mock import MagicMock, patch

import pytest

from bmrk.bookmarker import write_bookmarks
from bmrk.detector import HeadingEntry


def _make_reader(num_pages: int = 2, metadata: dict | None = None):
    """Return a mocked PdfReader with *num_pages* pages."""
    reader = MagicMock()
    reader.pages = [MagicMock() for _ in range(num_pages)]
    reader.metadata = metadata
    return reader


@pytest.fixture()
def mock_reader_writer(tmp_path):
    """
    Patch PdfReader and PdfWriter; yield (mock_reader, mock_writer, out_path).

    The caller can configure mock_reader.pages / metadata before use.
    """
    out_path = str(tmp_path / "out.pdf")
    with (
        patch("bmrk.bookmarker.PdfReader") as MockReader,
        patch("bmrk.bookmarker.PdfWriter") as MockWriter,
    ):
        reader = _make_reader()
        MockReader.return_value = reader

        writer = MagicMock()
        writer.add_outline_item.return_value = MagicMock()
        MockWriter.return_value = writer

        yield reader, writer, out_path


# ---------------------------------------------------------------------------
# Document cloning (clone_reader_document_root)
# ---------------------------------------------------------------------------


class TestDocumentCloning:
    def test_document_root_cloned(self, mock_reader_writer):
        reader, writer, out = mock_reader_writer

        write_bookmarks("in.pdf", out, [])

        writer.clone_reader_document_root.assert_called_once_with(reader)


# ---------------------------------------------------------------------------
# Bookmark ordering and parent-child hierarchy
# ---------------------------------------------------------------------------


class TestBookmarkTree:
    def test_no_headings_writes_no_bookmarks(self, mock_reader_writer):
        _, writer, out = mock_reader_writer
        write_bookmarks("in.pdf", out, [])
        writer.add_outline_item.assert_not_called()

    def test_single_heading_added(self, mock_reader_writer):
        reader, writer, out = mock_reader_writer
        headings = [HeadingEntry(level=1, title="Introduction", page=0)]

        write_bookmarks("in.pdf", out, headings)

        writer.add_outline_item.assert_called_once_with(
            title="Introduction", page_number=0, parent=None
        )

    def test_multiple_h1_headings_added_in_order(self, mock_reader_writer):
        reader, writer, out = mock_reader_writer
        reader.pages = [MagicMock(), MagicMock(), MagicMock()]
        headings = [
            HeadingEntry(level=1, title="Chapter 1", page=0),
            HeadingEntry(level=1, title="Chapter 2", page=1),
            HeadingEntry(level=1, title="Chapter 3", page=2),
        ]

        write_bookmarks("in.pdf", out, headings)

        calls = writer.add_outline_item.call_args_list
        assert len(calls) == 3
        assert calls[0].kwargs["title"] == "Chapter 1"
        assert calls[1].kwargs["title"] == "Chapter 2"
        assert calls[2].kwargs["title"] == "Chapter 3"

    def test_h2_nested_under_h1(self, mock_reader_writer):
        _, writer, out = mock_reader_writer
        h1_bookmark = MagicMock(name="h1_bm")
        writer.add_outline_item.side_effect = [h1_bookmark, MagicMock()]

        headings = [
            HeadingEntry(level=1, title="Chapter 1", page=0),
            HeadingEntry(level=2, title="Section 1.1", page=0),
        ]
        write_bookmarks("in.pdf", out, headings)

        calls = writer.add_outline_item.call_args_list
        assert calls[0].kwargs["parent"] is None  # H1 has no parent
        assert calls[1].kwargs["parent"] is h1_bookmark  # H2 is under H1

    def test_h3_nested_under_h2(self, mock_reader_writer):
        _, writer, out = mock_reader_writer
        h1_bm = MagicMock(name="h1_bm")
        h2_bm = MagicMock(name="h2_bm")
        writer.add_outline_item.side_effect = [h1_bm, h2_bm, MagicMock()]

        headings = [
            HeadingEntry(level=1, title="Chapter 1", page=0),
            HeadingEntry(level=2, title="Section 1.1", page=0),
            HeadingEntry(level=3, title="Subsection 1.1.1", page=0),
        ]
        write_bookmarks("in.pdf", out, headings)

        calls = writer.add_outline_item.call_args_list
        assert calls[2].kwargs["parent"] is h2_bm

    def test_returning_to_h1_clears_h2_parent(self, mock_reader_writer):
        """After a second H1, a following H2 must parent to the new H1, not the old one."""
        _, writer, out = mock_reader_writer
        h1a_bm = MagicMock(name="h1a_bm")
        h2_bm = MagicMock(name="h2_bm")
        h1b_bm = MagicMock(name="h1b_bm")
        h2b_bm = MagicMock(name="h2b_bm")
        writer.add_outline_item.side_effect = [h1a_bm, h2_bm, h1b_bm, h2b_bm]

        headings = [
            HeadingEntry(level=1, title="Ch 1", page=0),
            HeadingEntry(level=2, title="Sec 1.1", page=0),
            HeadingEntry(level=1, title="Ch 2", page=1),
            HeadingEntry(level=2, title="Sec 2.1", page=1),
        ]
        write_bookmarks("in.pdf", out, headings)

        calls = writer.add_outline_item.call_args_list
        assert calls[2].kwargs["parent"] is None  # second H1: no parent
        assert calls[3].kwargs["parent"] is h1b_bm  # Sec 2.1 under Ch 2


# ---------------------------------------------------------------------------
# Page index clamping
# ---------------------------------------------------------------------------


class TestPageIndexClamping:
    def test_page_beyond_last_is_clamped(self, mock_reader_writer):
        reader, writer, out = mock_reader_writer
        reader.pages = [MagicMock()]  # only page 0 exists

        headings = [HeadingEntry(level=1, title="Heading", page=99)]
        write_bookmarks("in.pdf", out, headings)

        call = writer.add_outline_item.call_args
        assert call.kwargs["page_number"] == 0  # clamped to max valid index

    def test_page_within_range_unchanged(self, mock_reader_writer):
        reader, writer, out = mock_reader_writer
        reader.pages = [MagicMock(), MagicMock(), MagicMock()]

        headings = [HeadingEntry(level=1, title="Heading", page=2)]
        write_bookmarks("in.pdf", out, headings)

        call = writer.add_outline_item.call_args
        assert call.kwargs["page_number"] == 2


# ---------------------------------------------------------------------------
# Output file
# ---------------------------------------------------------------------------


class TestOutputFile:
    def test_writer_is_written_to_output_path(self, mock_reader_writer, tmp_path):
        _, writer, out = mock_reader_writer
        write_bookmarks("in.pdf", out, [])
        writer.write.assert_called_once()
