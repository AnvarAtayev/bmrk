import logging
from collections.abc import Callable

from pypdf import PdfReader, PdfWriter

from bmrk.detector import HeadingEntry

log = logging.getLogger("bmrk")


def write_bookmarks(
    input_path: str,
    output_path: str,
    headings: list[HeadingEntry],
    on_step: Callable[[str], None] | None = None,
) -> None:
    """
    Copy *input_path* to *output_path*, inserting bookmarks from *headings*.

    Parameters
    ----------
    input_path : str
        Source PDF (unmodified).
    output_path : str
        Destination PDF with bookmarks added.
    headings : list[HeadingEntry]
        Ordered list of headings as returned by ``detect_headings``.
    on_step : Callable[[str], None] | None
        Optional callback invoked with coarse-grained progress messages while
        the output PDF is being prepared and written.
    """
    def notify(message: str) -> None:
        log.debug(message)
        if on_step is not None:
            on_step(message)

    notify("Opening input PDF")
    reader = PdfReader(input_path)
    writer = PdfWriter()

    # Clone the entire document structure (pages, metadata, forms, etc.)
    # in one call.  Pre-existing outlines are intentionally NOT copied --
    # bmrk is authoritative for bookmarks.
    notify("Cloning PDF structure")
    writer.clone_reader_document_root(reader)

    # Build bookmark tree -------------------------------------------------------
    # parent_stack[i] stores the bookmark object for the most recently added
    # heading at level i.
    parent_stack: dict[int, object] = {}  # level → pypdf bookmark ref
    if headings:
        notify(f"Adding bookmarks (0/{len(headings)})")

    for index, entry in enumerate(headings, 1):
        # pypdf page indices are 0-based, same as our HeadingEntry.page
        page_idx = min(entry.page, len(reader.pages) - 1)

        # Determine parent
        parent = None
        for lvl in range(entry.level - 1, 0, -1):
            if lvl in parent_stack:
                parent = parent_stack[lvl]
                break

        log.debug(
            "%s[H%d] p%d: %s",
            "  " * (entry.level - 1),
            entry.level,
            page_idx + 1,
            entry.title[:60],
        )

        bm = writer.add_outline_item(
            title=entry.title,
            page_number=page_idx,
            parent=parent,
        )
        parent_stack[entry.level] = bm
        # Invalidate all deeper levels when we step back up
        for deeper in list(parent_stack.keys()):
            if deeper > entry.level:
                del parent_stack[deeper]

        if on_step is not None and (
            index == len(headings) or index == 1 or index % 100 == 0
        ):
            on_step(f"Adding bookmarks ({index}/{len(headings)})")

    # Write output
    notify("Writing output PDF")
    with open(output_path, "wb") as fh:
        writer.write(fh)

    notify(f"Written -> {output_path}")
