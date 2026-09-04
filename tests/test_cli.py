from unittest.mock import ANY, patch

import pytest
from typer.testing import CliRunner

from bmrk.cli import _load_headings, _save_headings, app
from bmrk.detector import HeadingEntry, NoReadableTextError
from bmrk.layout import BlockLabel, DocumentBlock, DocumentLayout, RawLine


@pytest.fixture()
def runner():
    return CliRunner()


def _invoke(runner, args, headings=None, *, write_side_effect=None, detect_side_effect=None):
    """
    Invoke CLI inside an isolated filesystem with a dummy input.pdf.

    detect_headings returns *headings* (default: one heading) or raises
    *detect_side_effect* if provided.
    """
    if headings is None and detect_side_effect is None:
        headings = []

    with runner.isolated_filesystem():
        with open("input.pdf", "wb") as f:
            f.write(b"%PDF-1.4 stub")

        detect_kwargs = {}
        if detect_side_effect is not None:
            detect_kwargs["side_effect"] = detect_side_effect
        elif headings is not None:
            detect_kwargs["return_value"] = headings

        with (
            patch("bmrk.cli.detect_headings", **detect_kwargs) as mock_detect,
            patch("bmrk.cli.write_bookmarks") as mock_write,
        ):
            if write_side_effect:
                mock_write.side_effect = write_side_effect
            result = runner.invoke(app, args, catch_exceptions=False)

    return result, mock_detect, mock_write


def _make_layout() -> DocumentLayout:
    raw_line = RawLine(
        page=0,
        text="Generic Title",
        bbox=(48.0, 100.0, 180.0, 118.0),
        top=100.0,
        bottom=118.0,
        left=48.0,
        right=180.0,
        page_width=612.0,
        page_height=792.0,
        size=18.0,
        bold=True,
        italic=False,
        block_id=0,
        line_id=0,
        segment_texts=["Generic Title"],
    )
    block = DocumentBlock(
        page=0,
        bbox=raw_line.bbox,
        text="Generic Title",
        lines=[raw_line],
        dominant_size=18.0,
        bold=True,
        italic=False,
        centered=False,
        indent=48.0,
        label=BlockLabel.HEADING_CANDIDATE,
        confidence=0.96,
        features={"layout_boxclass": "title", "numeric_depth": None},
    )
    return DocumentLayout(
        lines=[raw_line],
        blocks=[block],
        body_cluster={"size": 12.0, "bold": False, "italic": False},
        toc_pages=set(),
    )


# ---------------------------------------------------------------------------
# Normal flow
# ---------------------------------------------------------------------------


class TestNormalFlow:
    def test_success_exit_code(self, runner):
        headings = [HeadingEntry(level=1, title="Introduction", page=0)]
        result, _, _ = _invoke(runner, ["input.pdf", "output.pdf"], headings)
        assert result.exit_code == 0

    def test_success_message_contains_output_path(self, runner):
        headings = [HeadingEntry(level=1, title="Introduction", page=0)]
        result, _, _ = _invoke(runner, ["input.pdf", "output.pdf"], headings)
        assert "output.pdf" in result.output

    def test_write_bookmarks_called_with_correct_args(self, runner):
        headings = [HeadingEntry(level=1, title="Introduction", page=0)]
        _, _, mock_write = _invoke(runner, ["input.pdf", "output.pdf"], headings)
        mock_write.assert_called_once_with(ANY, ANY, headings, on_step=ANY)

    def test_detected_heading_count_printed(self, runner):
        headings = [
            HeadingEntry(level=1, title="Intro", page=0),
            HeadingEntry(level=2, title="Background", page=1),
        ]
        result, _, _ = _invoke(runner, ["input.pdf", "output.pdf"], headings)
        assert "2" in result.output


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_write_output(self, runner):
        headings = [HeadingEntry(level=1, title="Introduction", page=0)]
        _, _, mock_write = _invoke(runner, ["input.pdf", "output.pdf", "--dry-run"], headings)
        mock_write.assert_not_called()

    def test_dry_run_prints_toc(self, runner):
        headings = [
            HeadingEntry(level=1, title="Introduction", page=0),
            HeadingEntry(level=2, title="Background", page=1),
        ]
        result, _, _ = _invoke(runner, ["input.pdf", "output.pdf", "--dry-run"], headings)
        assert result.exit_code == 0
        assert "Introduction" in result.output
        assert "Background" in result.output

    def test_dry_run_short_flag(self, runner):
        headings = [HeadingEntry(level=1, title="Introduction", page=0)]
        _, _, mock_write = _invoke(runner, ["input.pdf", "output.pdf", "-n"], headings)
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# No headings detected (has text, but no headings found)
# ---------------------------------------------------------------------------


class TestNoHeadings:
    def test_warning_shown_when_no_headings(self, runner):
        result, _, _ = _invoke(runner, ["input.pdf", "output.pdf"], headings=[])
        assert "No headings detected" in result.output

    def test_output_still_written_when_no_headings(self, runner):
        _, _, mock_write = _invoke(runner, ["input.pdf", "output.pdf"], headings=[])
        mock_write.assert_called_once()

    def test_no_headings_dry_run_exits_cleanly(self, runner):
        result, _, mock_write = _invoke(
            runner, ["input.pdf", "output.pdf", "--dry-run"], headings=[]
        )
        assert result.exit_code == 0
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# No readable text (NoReadableTextError)
# ---------------------------------------------------------------------------


class TestNoReadableText:
    def test_no_readable_text_exits_nonzero(self, runner):
        result, _, _ = _invoke(
            runner,
            ["input.pdf", "output.pdf"],
            detect_side_effect=NoReadableTextError("no text"),
        )
        assert result.exit_code != 0

    def test_no_readable_text_does_not_write_output(self, runner):
        _, _, mock_write = _invoke(
            runner,
            ["input.pdf", "output.pdf"],
            detect_side_effect=NoReadableTextError("no text"),
        )
        mock_write.assert_not_called()

    def test_no_readable_text_shows_warning(self, runner):
        result, _, _ = _invoke(
            runner,
            ["input.pdf", "output.pdf"],
            detect_side_effect=NoReadableTextError("no text"),
        )
        assert "Warning" in result.output or "warning" in result.output.lower()


# ---------------------------------------------------------------------------
# --threshold
# ---------------------------------------------------------------------------


class TestThreshold:
    def test_default_threshold_is_1_05(self, runner):
        _, mock_detect, _ = _invoke(runner, ["input.pdf", "output.pdf"])
        mock_detect.assert_called_once_with(
            ANY,
            size_threshold_ratio=1.05,
            on_page=ANY,
            skip_pages=0,
            max_depth=3,
        )

    def test_custom_threshold_passed_to_detector(self, runner):
        _, mock_detect, _ = _invoke(runner, ["input.pdf", "output.pdf", "--threshold", "1.15"])
        mock_detect.assert_called_once_with(
            ANY,
            size_threshold_ratio=1.15,
            on_page=ANY,
            skip_pages=0,
            max_depth=3,
        )

    def test_threshold_short_flag(self, runner):
        _, mock_detect, _ = _invoke(runner, ["input.pdf", "output.pdf", "-t", "1.01"])
        mock_detect.assert_called_once_with(
            ANY,
            size_threshold_ratio=1.01,
            on_page=ANY,
            skip_pages=0,
            max_depth=3,
        )


# ---------------------------------------------------------------------------
# --verbose
# ---------------------------------------------------------------------------


class TestVerbose:
    def test_verbose_shows_toc_structure(self, runner):
        headings = [HeadingEntry(level=1, title="Intro", page=0)]
        result, _, _ = _invoke(runner, ["input.pdf", "output.pdf", "--verbose"], headings)
        assert "Intro" in result.output

    def test_verbose_short_flag_exits_ok(self, runner):
        headings = [HeadingEntry(level=1, title="Intro", page=0)]
        result, _, _ = _invoke(runner, ["input.pdf", "output.pdf", "-v"], headings)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# --cover-pages
# ---------------------------------------------------------------------------


class TestCoverPages:
    def test_cover_pages_passed_to_detector(self, runner):
        _, mock_detect, _ = _invoke(runner, ["input.pdf", "output.pdf", "--cover-pages", "2"])
        mock_detect.assert_called_once_with(
            ANY,
            size_threshold_ratio=1.05,
            on_page=ANY,
            skip_pages=2,
            max_depth=3,
        )


# ---------------------------------------------------------------------------
# --export-blocks / --blocks-only
# ---------------------------------------------------------------------------


class TestBlocks:
    def test_export_blocks_creates_jsonl_without_output(self, runner):
        layout = _make_layout()
        with runner.isolated_filesystem():
            with open("input.pdf", "wb") as f:
                f.write(b"%PDF-1.4 stub")
            with (
                patch("bmrk.cli.analyze_layout", return_value=layout) as mock_layout,
                patch("bmrk.cli.build_headings_from_layout") as mock_build,
                patch("bmrk.cli.detect_headings") as mock_detect,
                patch("bmrk.cli.write_bookmarks") as mock_write,
            ):
                result = runner.invoke(
                    app,
                    ["input.pdf", "--export-blocks", "blocks.jsonl", "--blocks-only"],
                    catch_exceptions=False,
                )

            with open("blocks.jsonl", encoding="utf-8") as fh:
                content = fh.read()

        assert result.exit_code == 0
        assert '"label": "heading_candidate"' in content
        assert '"layout_boxclass": "title"' in content
        mock_layout.assert_called_once()
        mock_build.assert_not_called()
        mock_detect.assert_not_called()
        mock_write.assert_not_called()

    def test_blocks_only_prints_block_structure(self, runner):
        layout = _make_layout()
        with runner.isolated_filesystem():
            with open("input.pdf", "wb") as f:
                f.write(b"%PDF-1.4 stub")
            with (
                patch("bmrk.cli.analyze_layout", return_value=layout),
                patch("bmrk.cli.build_headings_from_layout") as mock_build,
                patch("bmrk.cli.write_bookmarks") as mock_write,
            ):
                result = runner.invoke(
                    app,
                    ["input.pdf", "--blocks-only"],
                    catch_exceptions=False,
                )

        assert result.exit_code == 0
        assert "Block structure" in result.output
        assert "heading_candidate" in result.output
        assert "Generic Title" in result.output
        mock_build.assert_not_called()
        mock_write.assert_not_called()

    def test_export_blocks_reuses_layout_for_heading_build(self, runner):
        layout = _make_layout()
        headings = [HeadingEntry(level=1, title="Generic Title", page=0)]
        with runner.isolated_filesystem():
            with open("input.pdf", "wb") as f:
                f.write(b"%PDF-1.4 stub")
            with (
                patch("bmrk.cli.analyze_layout", return_value=layout) as mock_layout,
                patch("bmrk.cli.build_headings_from_layout", return_value=headings) as mock_build,
                patch("bmrk.cli.detect_headings") as mock_detect,
                patch("bmrk.cli.write_bookmarks") as mock_write,
            ):
                result = runner.invoke(
                    app,
                    ["input.pdf", "output.pdf", "--export-blocks", "blocks.jsonl"],
                    catch_exceptions=False,
                )

        assert result.exit_code == 0
        mock_layout.assert_called_once()
        mock_build.assert_called_once_with(
            layout,
            size_threshold_ratio=1.05,
            max_depth=3,
        )
        mock_detect.assert_not_called()
        mock_write.assert_called_once()


# ---------------------------------------------------------------------------
# --export-headings / --import-headings
# ---------------------------------------------------------------------------


class TestExportImportHeadings:
    def test_export_headings_creates_file(self, runner):
        headings = [HeadingEntry(level=1, title="Introduction", page=0)]
        with runner.isolated_filesystem():
            with open("input.pdf", "wb") as f:
                f.write(b"%PDF-1.4 stub")
            with (
                patch("bmrk.cli.detect_headings", return_value=headings),
                patch("bmrk.cli.write_bookmarks"),
            ):
                runner.invoke(
                    app,
                    ["input.pdf", "output.pdf", "--export-headings", "out.tsv"],
                    catch_exceptions=False,
                )
            with open("out.tsv", encoding="utf-8") as fh:
                content = fh.read()
        assert "Introduction" in content
        assert "1\t1\t" in content  # level=1, page=1 (1-based)

    def test_import_headings_skips_detection(self, runner):
        with runner.isolated_filesystem():
            with open("input.pdf", "wb") as f:
                f.write(b"%PDF-1.4 stub")
            with open("headings.tsv", "w") as fh:
                fh.write("# comment\n")
                fh.write("1\t3\tImported\n")
            with (
                patch("bmrk.cli.detect_headings") as mock_detect,
                patch("bmrk.cli.write_bookmarks"),
            ):
                result = runner.invoke(
                    app,
                    ["input.pdf", "output.pdf", "--import-headings", "headings.tsv"],
                    catch_exceptions=False,
                )
        mock_detect.assert_not_called()
        assert result.exit_code == 0

    def test_import_headings_uses_loaded_entries(self, runner):
        with runner.isolated_filesystem():
            with open("input.pdf", "wb") as f:
                f.write(b"%PDF-1.4 stub")
            with open("headings.tsv", "w") as fh:
                fh.write("1\t3\tImported Heading\n")
            with patch("bmrk.cli.detect_headings"), patch("bmrk.cli.write_bookmarks") as mock_write:
                runner.invoke(
                    app,
                    ["input.pdf", "output.pdf", "--import-headings", "headings.tsv"],
                    catch_exceptions=False,
                )
            written_headings = mock_write.call_args[0][2]
        assert len(written_headings) == 1
        assert written_headings[0].title == "Imported Heading"
        assert written_headings[0].page == 2  # 1-based 3 -> 0-based 2


# ---------------------------------------------------------------------------
# _save_headings / _load_headings round-trip
# ---------------------------------------------------------------------------


class TestHeadingsRoundTrip:
    def test_roundtrip(self, tmp_path):
        original = [
            HeadingEntry(level=1, title="Introduction", page=0),
            HeadingEntry(level=2, title="1.1 Background", page=1),
            HeadingEntry(level=3, title="Details", page=3),
        ]
        path = str(tmp_path / "headings.tsv")
        _save_headings(original, path)
        loaded = _load_headings(path)

        assert len(loaded) == len(original)
        for orig, load in zip(original, loaded):
            assert load.level == orig.level
            assert load.title == orig.title
            assert load.page == orig.page

    def test_comments_ignored_on_load(self, tmp_path):
        path = str(tmp_path / "headings.tsv")
        with open(path, "w") as fh:
            fh.write("# this is a comment\n")
            fh.write("1\t1\tIntroduction\n")
        loaded = _load_headings(path)
        assert len(loaded) == 1
        assert loaded[0].title == "Introduction"


# ---------------------------------------------------------------------------
# Bad inputs
# ---------------------------------------------------------------------------


class TestBadInputs:
    def test_missing_input_file_errors(self, runner):
        with runner.isolated_filesystem():
            result = runner.invoke(app, ["nonexistent.pdf", "output.pdf"])
        assert result.exit_code != 0

    def test_missing_arguments_errors(self, runner):
        with runner.isolated_filesystem():
            result = runner.invoke(app, [])
        assert result.exit_code != 0
