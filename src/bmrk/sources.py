import logging

import pymupdf

from bmrk.detector import HeadingEntry

log = logging.getLogger("bmrk")


def read_existing_outline(pdf_path: str) -> list[HeadingEntry]:
    """
    Return the PDF's own outline as heading entries, or an empty list.

    Many PDFs already carry a ``/Outlines`` tree written by whoever produced
    them.  When present it is authoritative and needs no heuristics, so it is
    preferred over font-size detection.

    Entries with a blank title or no page destination are skipped.  Heading
    levels are preserved as-is: an existing outline is already correct, and
    truncating it to ``max_depth`` would discard structure the document had.

    Parameters
    ----------
    pdf_path : str
        Path to the PDF to read.

    Returns
    -------
    list[HeadingEntry]
        Outline entries in document order, or ``[]`` when the PDF has no
        outline or none of its entries are usable.
    """
    try:
        with pymupdf.open(pdf_path) as doc:
            toc = doc.get_toc()
            page_count = len(doc)
    except (RuntimeError, ValueError) as exc:
        # Encrypted or malformed documents cannot report an outline.  Return
        # nothing so the caller falls back to font analysis.
        log.debug("Could not read an outline from %s: %s", pdf_path, exc)
        return []

    entries: list[HeadingEntry] = []
    for level, title, page in toc:
        title = title.strip()
        if not title or page < 1:
            continue
        entries.append(
            HeadingEntry(
                level=max(1, level),
                title=title,
                page=min(page - 1, page_count - 1),
            )
        )

    if entries:
        log.debug("Read %d entries from the existing PDF outline.", len(entries))
    return entries
