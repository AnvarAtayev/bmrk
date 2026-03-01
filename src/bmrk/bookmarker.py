import logging

from pypdf import PdfReader, PdfWriter

from bmrk.detector import HeadingEntry

log = logging.getLogger("bmrk")


def write_bookmarks(
    input_path: str,
    output_path: str,
    headings: list[HeadingEntry],
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
    """
    reader = PdfReader(input_path)
    writer = PdfWriter()

    # Clone the entire document structure (pages, metadata, forms, etc.)
    # in one call.  Pre-existing outlines are intentionally NOT copied --
    # bmrk is authoritative for bookmarks.
    writer.clone_reader_document_root(reader)

    # Build bookmark tree -------------------------------------------------------
    # parent_stack[i] stores the bookmark object for the most recently added
    # heading at level i.
    parent_stack: dict[int, object] = {}  # level → pypdf bookmark ref

    for entry in headings:
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

    # Write output
    with open(output_path, "wb") as fh:
        writer.write(fh)

    log.debug("Written -> %s", output_path)
