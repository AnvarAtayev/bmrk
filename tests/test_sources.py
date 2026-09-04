"""End-to-end tests for heading sources, using real generated PDFs."""

import fitz
import pytest
from typer.testing import CliRunner

from bmrk.cli import app
from bmrk.sources import read_existing_outline

runner = CliRunner()

# A six-level outline, deeper than the default --max-depth of 3, including one
# blank title of the kind found in real documents.
_OUTLINE = [
    [1, "Front Matter", 1],
    [1, "Chapter One", 2],
    [2, "Section 1.1", 3],
    [3, "Subsection 1.1.1", 4],
    [4, "Deeper 1.1.1.1", 5],
    [5, "Deeper Still", 6],
    [6, "Deepest", 7],
    [1, "   ", 8],
    [1, "Chapter Two", 9],
]


def _write_pages(count, heading_pages=(), heading_top=140):
    """Build a *count*-page document, optionally with large headings on some pages."""
    doc = fitz.open()
    for index in range(count):
        page = doc.new_page()
        y = heading_top
        page.insert_text((72, 40), f"Running Header  {index + 1}", fontsize=9)
        if index in heading_pages:
            page.insert_text((72, y), f"{index + 1}  Chapter {index + 1} Title", fontsize=18)
            y += 40
        for line in range(25):
            page.insert_text(
                (72, y), f"Body line {line} of page {index}, ordinary prose here.", fontsize=11
            )
            y += 16
    return doc


@pytest.fixture
def pdf_with_outline(tmp_path):
    """Build a PDF carrying its own six-level bookmark outline."""
    path = tmp_path / "with_outline.pdf"
    doc = _write_pages(10)
    doc.set_toc(_OUTLINE)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def pdf_without_outline(tmp_path):
    """Build a PDF with no outline but font-detectable headings."""
    path = tmp_path / "no_outline.pdf"
    doc = _write_pages(10, heading_pages={0, 5})
    doc.save(str(path))
    doc.close()
    return str(path)


def _outline_of(path):
    doc = fitz.open(path)
    toc = [(level, title, page) for level, title, page in doc.get_toc()]
    doc.close()
    return toc


# ---------------------------------------------------------------------------
# read_existing_outline
# ---------------------------------------------------------------------------


class TestReadExistingOutline:
    def test_reads_outline(self, pdf_with_outline):
        entries = read_existing_outline(pdf_with_outline)
        assert [(e.level, e.title, e.page) for e in entries[:3]] == [
            (1, "Front Matter", 0),
            (1, "Chapter One", 1),
            (2, "Section 1.1", 2),
        ]

    def test_skips_empty_titles(self, pdf_with_outline):
        titles = [e.title for e in read_existing_outline(pdf_with_outline)]
        assert "   " not in titles
        assert all(t.strip() for t in titles)
        assert len(titles) == len(_OUTLINE) - 1

    def test_preserves_depth_beyond_max_depth(self, pdf_with_outline):
        levels = {e.level for e in read_existing_outline(pdf_with_outline)}
        assert max(levels) == 6, "an existing outline must not be truncated to --max-depth"

    def test_no_outline_returns_empty(self, pdf_without_outline):
        assert read_existing_outline(pdf_without_outline) == []


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestSourceSelection:
    def test_outline_not_duplicated(self, pdf_with_outline, tmp_path):
        """Regression: cloning used to copy the outline and then append to it."""
        out = tmp_path / "out.pdf"
        result = runner.invoke(app, [pdf_with_outline, str(out)])
        assert result.exit_code == 0
        assert len(_outline_of(str(out))) == len(_OUTLINE) - 1

    def test_roundtrip_exact(self, pdf_with_outline, tmp_path):
        out = tmp_path / "out.pdf"
        runner.invoke(app, [pdf_with_outline, str(out)])
        expected = [(lvl, title, page) for lvl, title, page in _OUTLINE if title.strip()]
        assert _outline_of(str(out)) == expected

    def test_reports_outline_source(self, pdf_with_outline):
        result = runner.invoke(app, [pdf_with_outline, "--dry-run"])
        assert result.exit_code == 0
        assert "existing outline" in result.output

    def test_falls_back_to_detection(self, pdf_without_outline):
        result = runner.invoke(app, [pdf_without_outline, "--dry-run"])
        assert result.exit_code == 0
        assert "font analysis" in result.output
        assert "[H1]" in result.output

    def test_source_font_ignores_outline(self, pdf_with_outline):
        result = runner.invoke(app, [pdf_with_outline, "--dry-run", "--source", "font"])
        assert "Chapter One" not in result.output

    def test_source_outline_errors_without_one(self, pdf_without_outline):
        result = runner.invoke(app, [pdf_without_outline, "--dry-run", "--source", "outline"])
        assert result.exit_code == 1
        assert "no bookmark outline" in result.output

    def test_rejects_unknown_source(self, pdf_with_outline):
        result = runner.invoke(app, [pdf_with_outline, "--dry-run", "--source", "nope"])
        assert result.exit_code == 1
        assert "unknown --source" in result.output

    def test_hints_at_the_override_when_using_the_outline(self, pdf_with_outline):
        result = runner.invoke(app, [pdf_with_outline, "--dry-run"])
        assert "--source font" in result.output

    def test_no_hint_when_headings_came_from_fonts(self, pdf_without_outline):
        result = runner.invoke(app, [pdf_without_outline, "--dry-run"])
        assert "--source font" not in result.output

    def test_hint_shown_when_writing_output(self, pdf_with_outline, tmp_path):
        result = runner.invoke(app, [pdf_with_outline, str(tmp_path / "out.pdf")])
        assert "--source font" in result.output
