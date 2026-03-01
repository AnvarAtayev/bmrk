from unittest.mock import ANY, patch

import pytest
from typer.testing import CliRunner

from bmrk.cli import _load_headings, _save_headings, app
from bmrk.detector import HeadingEntry, NoReadableTextError


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
        mock_write.assert_called_once_with(ANY, ANY, headings)

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
