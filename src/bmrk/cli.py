import logging
import tempfile
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from bmrk.bookmarker import write_bookmarks
from bmrk.detector import HeadingEntry, NoReadableTextError, detect_headings

console = Console(highlight=False)

app = typer.Typer()

# ---------------------------------------------------------------------------
# Heading file I/O helpers
# ---------------------------------------------------------------------------

_HEADINGS_HEADER = "# bmrk heading export\n# level\tpage\ttitle\n"


def _save_headings(headings: list[HeadingEntry], path: str) -> None:
    """
    Write *headings* to a tab-separated file at *path*.

    Format: ``level TAB page TAB title`` (page is 1-based for readability).
    Lines beginning with ``#`` are comments and are ignored on import.

    Parameters
    ----------
    headings : list[HeadingEntry]
        Headings to export.
    path : str
        Destination file path.
    """
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_HEADINGS_HEADER)
        for h in headings:
            fh.write(f"{h.level}\t{h.page + 1}\t{h.title}\n")


def _load_headings(path: str, max_depth: int = 3) -> list[HeadingEntry]:
    """
    Read headings from a tab-separated file previously written by ``_save_headings``.

    Parameters
    ----------
    path : str
        Path to the headings file.
    max_depth : int
        Maximum heading level; values outside ``[1, max_depth]`` are clamped.

    Returns
    -------
    list[HeadingEntry]
        Parsed heading entries ordered as they appear in the file.
    """
    entries: list[HeadingEntry] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t", maxsplit=2)
            if len(parts) != 3:
                typer.secho(
                    f"  Warning: skipping malformed line {lineno} in {path!r}: {line!r}",
                    fg=typer.colors.YELLOW,
                )
                continue
            level_s, page_s, title = parts
            try:
                level = int(level_s)
                page = int(page_s) - 1  # convert 1-based to 0-based
            except ValueError:
                typer.secho(
                    f"  Warning: skipping non-integer level/page on line {lineno}: {line!r}",
                    fg=typer.colors.YELLOW,
                )
                continue
            if level < 1 or level > max_depth:
                typer.secho(
                    f"  Warning: level {level} out of range [1,{max_depth}]"
                    f" on line {lineno}, clamping.",
                    fg=typer.colors.YELLOW,
                )
                level = max(1, min(max_depth, level))
            entries.append(HeadingEntry(level=level, title=title, page=max(page, 0)))
    return entries


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


@app.command()
def main(
    input_pdf: str = typer.Argument(metavar="INPUT", help="Source PDF file."),
    output_pdf: str | None = typer.Argument(
        None,
        metavar="OUTPUT",
        help="Destination PDF file. Optional when using --dry-run or --export-headings.",
    ),
    threshold: float = typer.Option(
        1.05,
        "--threshold",
        "-t",
        metavar="RATIO",
        show_default=True,
        help=(
            "Font-size ratio above which a text span is considered a heading. "
            "E.g. 1.05 means 5% larger than body text. "
            "Raise to 1.15 for noisy PDFs; lower to 1.01 to catch bold same-size headers."
        ),
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print detected headings and progress information."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Detect and print headings only; do not write an output file.",
    ),
    ocr: bool = typer.Option(
        False,
        "--ocr",
        help=(
            "Run OCR on INPUT before detecting headings (useful for scanned PDFs). "
            "Requires ocrmypdf: pip install bmrk[ocr]."
        ),
    ),
    export_headings: str | None = typer.Option(
        None,
        "--export-headings",
        metavar="FILE",
        help=(
            "After detection, write the heading structure to FILE as a tab-separated "
            "text file (level, 1-based page, title).  The file can be edited and fed "
            "back in with --import-headings."
        ),
    ),
    import_headings: str | None = typer.Option(
        None,
        "--import-headings",
        metavar="FILE",
        help=(
            "Read the heading structure from FILE instead of running detection.  "
            "The file must be in the tab-separated format produced by --export-headings."
        ),
    ),
    cover_pages: int = typer.Option(
        0,
        "--cover-pages",
        metavar="N",
        show_default=True,
        help="Skip the first N pages of INPUT when detecting headings (e.g. cover page).",
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        "-d",
        metavar="N",
        show_default=True,
        min=1,
        help=(
            "Maximum heading depth to include in the bookmarks. "
            "1 = top-level chapters only, 2 = chapters + sections, "
            "3 = chapters + sections + subsections, etc."
        ),
    ),
) -> None:
    """
    Add navigable bookmarks to a PDF based on its heading structure.

    Analyses INPUT for headings using font-size heuristics and numeric
    section prefixes, then writes a bookmarked copy to OUTPUT.

    Examples
    --------
    bmrk paper.pdf paper_bookmarked.pdf
    """
    # Suppress noisy pypdf warnings ("Ignoring wrong pointing object ...")
    # that pollute the terminal during PDF reading/writing.  In verbose mode
    # we keep them visible for debugging.
    if not verbose:
        logging.getLogger("pypdf").setLevel(logging.ERROR)

    # When no OUTPUT is given, treat it as export-only / dry-run.
    write_output = output_pdf is not None
    if not write_output and not dry_run and export_headings is None:
        typer.secho(
            "Error: OUTPUT is required unless --dry-run or --export-headings is given.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if not Path(input_pdf).exists():
        typer.secho(f"Error: input file not found: {input_pdf}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if import_headings is not None and export_headings is not None:
        typer.secho(
            "  Warning: --import-headings and --export-headings used together; "
            "the imported headings will simply be re-exported.",
            fg=typer.colors.YELLOW,
        )

    effective_input = input_pdf
    tmp_ocr: str | None = None
    status = console.status(f"[bold]bmrk:[/bold] Analysing {input_pdf}")
    status.start()

    try:
        # ------------------------------------------------------------------
        # Optional OCR pre-processing
        # ------------------------------------------------------------------
        if ocr:
            try:
                import ocrmypdf  # lazy import -- optional dependency
            except ImportError:
                status.stop()
                typer.secho(
                    "  ocrmypdf is not installed. Install it with: pip install bmrk[ocr]",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=1)

            status.update("[bold]bmrk:[/bold] Running OCR ...")
            tmp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp_ocr = tmp_file.name
            tmp_file.close()
            try:
                ocrmypdf.ocr(input_pdf, tmp_ocr, progress_bar=False)
            except Exception as exc:
                Path(tmp_ocr).unlink(missing_ok=True)
                status.stop()
                typer.secho(f"  OCR failed: {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=1)
            effective_input = tmp_ocr

        # ------------------------------------------------------------------
        # Heading detection (or import from file)
        # ------------------------------------------------------------------
        if import_headings is not None:
            status.update(f"[bold]bmrk:[/bold] Loading headings from {import_headings}")
            try:
                headings = _load_headings(import_headings, max_depth=max_depth)
            except OSError as exc:
                status.stop()
                typer.secho(f"  Cannot read headings file: {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=1)
        else:
            status.stop()
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold]bmrk:[/bold] Detecting headings"),
                BarColumn(),
                MofNCompleteColumn(),
                transient=True,
                console=console,
            ) as progress:
                task = progress.add_task("Detecting headings", total=None)

                def on_page(current: int, total: int) -> None:
                    progress.update(task, completed=current + 1, total=total)

                try:
                    headings = detect_headings(
                        effective_input,
                        size_threshold_ratio=threshold,
                        on_page=on_page,
                        skip_pages=cover_pages,
                        max_depth=max_depth,
                    )
                except NoReadableTextError as exc:
                    typer.secho(f"  Warning: {exc}", fg=typer.colors.YELLOW)
                    typer.secho(
                        "  No output file written.",
                        fg=typer.colors.YELLOW,
                    )
                    raise typer.Exit(code=1)
            status.start()

        # ------------------------------------------------------------------
        # Export headings to file if requested
        # ------------------------------------------------------------------
        if export_headings is not None:
            try:
                _save_headings(headings, export_headings)
            except OSError as exc:
                status.stop()
                typer.secho(
                    f"  Warning: could not write headings file: {exc}", fg=typer.colors.YELLOW
                )
                status.start()

        if not headings:
            status.stop()
            typer.secho(
                "  No headings detected. "
                "Try lowering --threshold (e.g. --threshold 1.01) or use --ocr if "
                "the PDF is a scanned image.",
                fg=typer.colors.YELLOW,
            )
            if not write_output or dry_run:
                raise typer.Exit()
            # Still write the output (a clean copy) so the command is idempotent
            write_bookmarks(effective_input, output_pdf, headings)
            raise typer.Exit()

        if dry_run or verbose or not write_output:
            status.stop()
            console.print(f"  Detected [bold]{len(headings)}[/bold] heading(s).")
            console.print()
            console.print("  Detected TOC structure:")
            for h in headings:
                indent = "    " + "  " * (h.level - 1)
                console.print(f"{indent}[H{h.level}] p{h.page + 1:>4}  {h.title[:80]}")
            console.print()

        if dry_run or not write_output:
            raise typer.Exit()

        # ------------------------------------------------------------------
        # Write bookmarked PDF
        # ------------------------------------------------------------------
        status.update(f"[bold]bmrk:[/bold] Writing bookmarked PDF ({len(headings)} headings)")
        write_bookmarks(effective_input, output_pdf, headings)
        status.stop()
        console.print(
            f"[bold green]bmrk:[/bold green] {output_pdf} [dim]({len(headings)} headings)[/dim]"
        )
    finally:
        status.stop()
        if tmp_ocr is not None:
            Path(tmp_ocr).unlink(missing_ok=True)


if __name__ == "__main__":
    app()
