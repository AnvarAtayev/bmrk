import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

import fitz

log = logging.getLogger("bmrk")

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class NoReadableTextError(RuntimeError):
    """
    Raised when a PDF contains no extractable text.

    This typically means the PDF is a scanned image without a text layer.
    Re-run with ``--ocr`` or pre-process with ``ocrmypdf`` to add one.
    """


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class HeadingEntry:
    """
    A single detected heading with its level, text, and page location.

    Attributes
    ----------
    level : int
        Heading depth (1-based): 1 = top-level chapter, 2 = section, etc.
    title : str
        Heading text as extracted from the PDF.
    page : int
        0-based page index where the heading appears.
    """

    level: int
    title: str
    page: int


class Span(TypedDict, total=False):
    """
    A single text span extracted from a PDF page.

    Attributes
    ----------
    text : str
        The text content of the span.
    size : float
        Font size in points.
    bold : bool
        Whether the majority of characters are bold.
    italic : bool
        Whether the majority of characters are italic.
    page : int
        0-based page index.
    top : float
        Vertical position of the span on the page (points from top).
    page_height : float
        Height of the page in points.
    _merged_texts : list[str]
        Original per-line texts when multiple lines are merged into one span.
    """

    text: str
    size: float
    bold: bool
    italic: bool
    page: int
    top: float
    page_height: float
    _merged_texts: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches common numeric / alphanumeric section prefixes:
#   "1  Introduction"
#   "2.3  Methods"
#   "A.1  Appendix"
#   "III.  Results"   (Roman numerals – optional support)
# The separator requires at LEAST two whitespace characters.  A single space
# is intentionally excluded because it matches too many normal sentences that
# start with a capital letter or a small number (e.g. "A good introduction",
# "I believe ...", "10 years later", "1917 saw ...").  Real numbered headings
# use two or more spaces, a tab, or a tab-space to visually separate the
# prefix from the title.
_NUMBERED_RE = re.compile(
    r"^(?P<prefix>"
    r"(?:[A-Z]\.?\d*|"  # Appendix-style: A, A.1
    r"\d+(?:\.\d+){0,3})"  # Numeric: 1, 1.2, 1.2.3, 1.2.3.4
    r"\.?)"
    r"\s{2,6}"  # separator: at least 2 spaces (single-space excluded to avoid false positives)
    r"(?P<title>\S.*)"
)

# Strings that look like page numbers, running headers, figure captions etc.
_NOISE_RE = re.compile(
    r"^(?:\d+|page\s+\d+|figure\s+\d+|table\s+\d+|fig\.\s*\d+)$",
    re.IGNORECASE,
)

# TOC lines often end with "... 12" or "......12" or just "12" after dots/spaces.
_TOC_LINE_RE = re.compile(r".{5,}[\s.]{2,}\d{1,4}\s*$")

_MAX_HEADING_LEN = 200  # characters – very long lines are likely body text
_MIN_HEADING_LEN = 2

# Fraction of non-noise lines that must look like TOC entries for a page to be
# treated as a Table of Contents page.
_TOC_PAGE_THRESHOLD = 0.45

# Fraction of page height that defines the header/footer margin zone.
# Spans whose top position falls below this fraction of the page height are
# treated as page headers; those above (1 - fraction) are treated as footers.
# Both are excluded from heading detection to suppress running headers.
_HEADER_MARGIN_RATIO = 0.08

# A heading title that appears on this many or more distinct pages is almost
# certainly a running header rather than a real section heading.  All
# occurrences after the first (the actual chapter start) are discarded.
_RUNNING_HEADER_MIN_PAGES = 3

# Structural label words that, when followed by a number or identifier, mark a
# chapter/part opener.  The regex matches the full heading text so that a bare
# "Introduction" (which is itself a complete heading) is NOT treated as a label,
# but "Chapter 1" or "Part IV" are.
_CHAPTER_LABEL_RE = re.compile(
    r"^(?:chapter|part|book|section|appendix|lecture|unit|module|episode)\s+\S+$",
    re.IGNORECASE,
)

# Maximum character length for italic/bold same-size heading candidates (Pass 3).
# Long lines are almost always body text, not headings.
_MAX_STYLED_HEADING_LEN = 120

# Unicode character class for math symbols and operators.  A span whose
# non-whitespace characters are predominantly drawn from these ranges is
# almost certainly a formula fragment, not a heading.
_MATH_CHAR_RE = re.compile(
    r"[\u0370-\u03FF"       # Greek and Coptic
    r"\u2100-\u214F"        # Letterlike Symbols
    r"\u2190-\u21FF"        # Arrows
    r"\u2200-\u22FF"        # Mathematical Operators
    r"\u2300-\u23FF"        # Miscellaneous Technical
    r"\u27C0-\u27EF"        # Miscellaneous Mathematical Symbols-A
    r"\u2980-\u29FF"        # Miscellaneous Mathematical Symbols-B
    r"\u2A00-\u2AFF"        # Supplemental Mathematical Operators
    r"\U0001D400-\U0001D7FF"  # Mathematical Alphanumeric Symbols
    r"=+\-*/^~<>|"          # Common ASCII math operators
    r"()\[\]{}]"            # Brackets and braces
)

_MATH_SPAN_MAX_LEN = 20  # math fragments extracted as spans are short

# Headings that mark the start of a bibliography / references section.
# Everything on the same page and later pages is excluded from Pass 3
# (styled-heading detection) to avoid picking up italic book titles.
_BIBLIOGRAPHY_RE = re.compile(
    r"^(?:bibliography|references|works cited|sources|further reading)$",
    re.IGNORECASE,
)


def _is_noise(text: str) -> bool:
    text = text.strip()
    if not text or len(text) < _MIN_HEADING_LEN or len(text) > _MAX_HEADING_LEN:
        return True
    return bool(_NOISE_RE.match(text))


def _is_math_span(text: str) -> bool:
    """
    Return True if *text* appears to be a math symbol or formula fragment.

    A span is classified as math when it is short (at most
    ``_MATH_SPAN_MAX_LEN`` non-whitespace characters) and at least half of
    those characters belong to well-known mathematical Unicode ranges or
    common ASCII operator characters.

    Parameters
    ----------
    text : str
        Span text to check.

    Returns
    -------
    bool
        True when the span is predominantly math symbols.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > _MATH_SPAN_MAX_LEN:
        return False
    math_count = len(_MATH_CHAR_RE.findall(stripped))
    return math_count / len(stripped) >= 0.5


def _numeric_depth(prefix: str) -> int:
    """
    Return the nesting depth implied by a numeric prefix like '2.3.1'.

    Parameters
    ----------
    prefix : str
        A numeric or alphanumeric section prefix (e.g. ``'2.3.1'``).

    Returns
    -------
    int
        Number of dot-separated parts, indicating nesting depth.
    """
    parts = prefix.rstrip(".").split(".")
    return len(parts)


def _in_margin(span: Span, ratio: float) -> bool:
    """
    Return True if *span* lies within the page header or footer margin zone.

    Parameters
    ----------
    span : Span
        Span as returned by ``_extract_spans``.
    ratio : float
        Fraction of the page height that defines the margin zone.  A span
        with ``top < page_height * ratio`` (header) or
        ``top > page_height * (1 - ratio)`` (footer) is considered in-margin.

    Returns
    -------
    bool
        True when the span is in the header or footer margin.
    """
    if ratio <= 0:
        return False
    page_h = span.get("page_height", 0)
    if page_h <= 0:
        return False
    frac = span.get("top", 0) / page_h
    return frac < ratio or frac > (1.0 - ratio)


def _is_toc_page(page_spans: list[Span]) -> bool:
    """
    Return True if *page_spans* look like a Table of Contents page.

    A page is treated as a TOC when at least ``_TOC_PAGE_THRESHOLD`` of its
    non-noise lines end with a pattern like ``"Section title ..... 12"``.

    Parameters
    ----------
    page_spans : list[Span]
        Spans from a single page as returned by ``_extract_spans``.

    Returns
    -------
    bool
        True if this page appears to be a Table of Contents.
    """
    non_noise = [s["text"] for s in page_spans if not _is_noise(s["text"])]
    if len(non_noise) < 3:
        return False
    toc_count = sum(1 for t in non_noise if _TOC_LINE_RE.match(t))
    return (toc_count / len(non_noise)) >= _TOC_PAGE_THRESHOLD


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------


def _extract_spans(
    pdf_path: str,
    on_page: Callable[[int, int], None] | None = None,
) -> list[Span]:
    """
    Extract text spans from a PDF file using PyMuPDF's native span hierarchy.

    Each text line in the PDF is emitted as one entry.  All PyMuPDF spans
    within a line are concatenated; font size is the median across characters
    in the line; bold and italic are derived from whether a majority of
    characters carry the respective PyMuPDF font flag (bold = bit 4,
    italic = bit 1).

    Parameters
    ----------
    pdf_path : str
        Path to the PDF file to extract spans from.
    on_page : callable or None
        Optional callback invoked as ``on_page(current_index, total_pages)``
        after each page is processed.  Useful for progress reporting.

    Returns
    -------
    list[Span]
        List of Span dicts.
    """
    spans: list[Span] = []
    with fitz.open(pdf_path) as doc:
        total = len(doc)
        for page_idx, page in enumerate(doc):
            if on_page is not None:
                on_page(page_idx, total)

            page_h = page.rect.height
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

            for block in blocks:
                if block.get("type") != 0:  # skip image blocks
                    continue
                for line in block.get("lines", []):
                    raw_spans = line.get("spans", [])

                    # Find the dominant font size in this line so we can
                    # skip superscript/subscript spans (footnote refs, etc.).
                    max_size = max(
                        (sp.get("size", 0) for sp in raw_spans if sp.get("text")),
                        default=0,
                    )
                    sup_threshold = max_size * 0.75

                    text_parts: list[str] = []
                    all_sizes: list[float] = []
                    bold_chars = 0
                    italic_chars = 0
                    total_chars = 0

                    for span in raw_spans:
                        t = span.get("text", "")
                        if not t:
                            continue
                        # Skip superscripts / subscripts
                        if span.get("size", 0) < sup_threshold:
                            continue
                        text_parts.append(t)
                        n = len(t)
                        flags = span.get("flags", 0)
                        all_sizes.extend([span.get("size", 0)] * n)
                        if flags & 16:  # bold
                            bold_chars += n
                        if flags & 2:  # italic
                            italic_chars += n
                        total_chars += n

                    text = "".join(text_parts).strip()
                    if not text or not all_sizes:
                        continue

                    all_sizes.sort()
                    median_size = all_sizes[len(all_sizes) // 2]

                    spans.append(
                        {
                            "text": text,
                            "size": median_size,
                            "bold": bold_chars > total_chars / 2,
                            "italic": italic_chars > total_chars / 2,
                            "page": page_idx,
                            "top": line["bbox"][1],
                            "page_height": page_h,
                        }
                    )
    return spans


def _estimate_body_size(spans: list[Span]) -> float:
    """
    Estimate body-text font size as the weighted mode of all span sizes.

    Weight is the number of characters in the span, so long paragraphs
    dominate the estimate.

    Parameters
    ----------
    spans : list[Span]
        Spans as returned by ``_extract_spans``.

    Returns
    -------
    float
        Estimated body font size in points.
    """
    counter: Counter = Counter()
    for s in spans:
        # Round to 0.5pt buckets to avoid float fragmentation
        bucket = round(s["size"] * 2) / 2
        counter[bucket] += len(s["text"])

    if not counter:
        return 11.0  # sensible default

    return counter.most_common(1)[0][0]


def _assign_heading_levels(
    heading_sizes: list[float],
    max_levels: int = 3,
) -> dict[float, int]:
    """
    Assign heading levels to a list of font sizes.

    Maps distinct font sizes to levels 1 .. *max_levels*, largest first.

    Parameters
    ----------
    heading_sizes : list[float]
        Font sizes of candidate heading spans (may contain duplicates).
    max_levels : int
        Maximum number of distinct heading levels (default 3).

    Returns
    -------
    dict[float, int]
        Mapping from font size to heading level (1-based).
    """
    # Normalize to 0.5pt buckets (same as _estimate_body_size) so that the
    # keys produced here match the rounded lookup in detect_headings.
    normalized = [round(sz * 2) / 2 for sz in heading_sizes]
    unique = sorted(set(normalized), reverse=True)
    mapping = {}
    for i, sz in enumerate(unique[:max_levels]):
        mapping[sz] = i + 1
    for sz in unique[max_levels:]:
        mapping[sz] = max_levels
    return mapping


def _merge_wrapped_headings(candidates: list[Span]) -> list[Span]:
    """
    Merge consecutive heading candidates that form a single wrapped heading.

    A heading that is too wide for the column wraps to a second (or third)
    line in the PDF, producing two separate spans with the same font size.
    This function joins them back into one entry.

    Two consecutive candidates are merged when all of the following hold:

    - Same page.
    - Font sizes differ by at most 0.5 pt.
    - The vertical gap between the two lines is at most 1.8x the font size
      (normal tight leading is ~1.2x; anything larger signals separate items).
    - The preceding line does not end with sentence-ending punctuation
      (``.:;,?!``), which would indicate distinct logical items.

    Parameters
    ----------
    candidates : list[Span]
        Heading candidate spans sorted by (page, top), as produced by Pass 1.

    Returns
    -------
    list[Span]
        Candidates with wrapped lines merged.  Each merged span gains a
        ``_merged_texts`` key listing the original per-line texts so that
        ``detect_headings`` can add them all to ``seen_keys``.
    """
    if not candidates:
        return candidates

    merged: list[Span] = []
    i = 0
    while i < len(candidates):
        span: Span = dict(candidates[i])  # type: ignore[assignment]
        components = [candidates[i]["text"]]
        last_top = candidates[i]["top"]

        while i + 1 < len(candidates):
            nxt = candidates[i + 1]
            gap = nxt["top"] - last_top
            prev_text = components[-1]
            if (
                nxt["page"] == span["page"]
                and abs(nxt["size"] - span["size"]) <= 0.5
                and gap <= span["size"] * 1.8
                and prev_text[-1] not in ".:;,?!"
            ):
                components.append(nxt["text"])
                last_top = nxt["top"]
                i += 1
            else:
                break

        span["text"] = " ".join(t.strip() for t in components)
        span["_merged_texts"] = components
        merged.append(span)
        i += 1

    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_headings(
    pdf_path: str,
    size_threshold_ratio: float = 1.05,
    on_page: Callable[[int, int], None] | None = None,
    skip_pages: int = 0,
    skip_toc: bool = True,
    header_margin: float = _HEADER_MARGIN_RATIO,
    merge_chapter_labels: bool = True,
    max_depth: int = 3,
) -> list[HeadingEntry]:
    """
    Detect headings in *pdf_path* and return them as ``HeadingEntry`` objects.

    Parameters
    ----------
    pdf_path : str
        Path to the input PDF.
    size_threshold_ratio : float
        A span is a heading candidate if its font size is at least
        ``body_size * size_threshold_ratio``. Lower values catch more headings
        (e.g. bold same-size section titles); higher values are more strict.
    on_page : callable or None
        Optional progress callback invoked as ``on_page(current, total)``
        for each page processed during text extraction.
    skip_pages : int
        Number of leading pages to exclude from heading detection (e.g. cover
        pages).  Default is 0 (process all pages).
    skip_toc : bool
        When True (default), pages that appear to be a Table of Contents are
        automatically excluded from heading detection.
    header_margin : float
        Fraction of the page height reserved for page headers (top) and
        footers (bottom).  Spans in this margin zone are excluded from heading
        detection to suppress running page headers.  Default is
        ``_HEADER_MARGIN_RATIO`` (0.08).  Pass 0 to disable.
    merge_chapter_labels : bool
        When True (default), a structural label such as ``"Chapter 1"`` or
        ``"Part IV"`` that is immediately followed by a title on the same page
        is merged into a single bookmark entry, e.g. ``"Chapter 1 Introduction"``.
        Disable with ``False`` to keep them as separate entries.
    max_depth : int
        Maximum heading depth to detect (1 = only top-level chapters,
        2 = chapters + sections, 3 = chapters + sections + subsections, etc.).
        Default is 3.

    Returns
    -------
    list[HeadingEntry]
        Heading entries ordered by page and position within the document.

    Raises
    ------
    NoReadableTextError
        If the PDF contains no extractable text (e.g. a scanned image PDF).
    """
    spans = _extract_spans(pdf_path, on_page=on_page)
    if not spans:
        raise NoReadableTextError(
            "No selectable text found in this PDF. "
            "It may be a scanned image. Re-run with --ocr to add a text layer "
            "automatically, or pre-process with: ocrmypdf input.pdf input_ocr.pdf"
        )

    # Skip leading pages (e.g. cover page)
    if skip_pages > 0:
        spans = [s for s in spans if s["page"] >= skip_pages]

    # Detect and skip TOC pages
    if skip_toc:
        pages_with_spans: dict[int, list[Span]] = {}
        for s in spans:
            pages_with_spans.setdefault(s["page"], []).append(s)
        toc_pages = {pg for pg, pg_spans in pages_with_spans.items() if _is_toc_page(pg_spans)}
        if toc_pages:
            log.debug("TOC pages detected and skipped: %s", sorted(toc_pages))
            spans = [s for s in spans if s["page"] not in toc_pages]

    body_size = _estimate_body_size(spans)
    threshold = body_size * size_threshold_ratio

    log.debug(
        "Body font size estimated at %.1fpt (heading threshold: >%.1fpt)",
        body_size,
        threshold,
    )

    # -----------------------------------------------------------------------
    # Pass 1+2 -- font-size and numeric-prefix candidates (single pass)
    # -----------------------------------------------------------------------
    # Font-size candidates are collected first; numeric-prefix candidates are
    # tracked separately.  When a span matches both, the font-size level wins.
    size_candidates: list[Span] = []
    numbered_candidates: dict[tuple[int, str], int] = {}  # (page, text) -> level
    for s in spans:
        if _in_margin(s, header_margin):
            continue
        text = s["text"]
        is_noise = _is_noise(text)

        # Font-size candidate
        if not is_noise and s["size"] > threshold:
            if not text[0].islower() and text[-1] != "." and not _is_math_span(text):
                size_candidates.append(s)

        # Numeric-prefix candidate
        m = _NUMBERED_RE.match(text.strip())
        if m:
            depth = _numeric_depth(m.group("prefix"))
            numbered_candidates[(s["page"], text.strip())] = min(depth, max(max_depth, 3))

    # Merge wrapped headings (same-size spans on the same page within ~1.8x line height)
    size_candidates = _merge_wrapped_headings(size_candidates)

    # Build level mapping from the sizes we actually found
    size_map = _assign_heading_levels(
        [s["size"] for s in size_candidates], max_levels=max(max_depth, 3)
    )

    log.debug("Heading size -> level map: %s", size_map)

    # -----------------------------------------------------------------------
    # Merge: font-size headings take priority; fill gaps with numbered ones
    # -----------------------------------------------------------------------
    seen_keys: set[tuple[int, str]] = set()
    raw_entries: list[HeadingEntry] = []

    for s in size_candidates:
        key = (s["page"], s["text"])
        seen_keys.add(key)
        # Mark individual component texts as seen so later passes don't re-add them
        for comp in s.get("_merged_texts", []):
            seen_keys.add((s["page"], comp))
        level = size_map.get(round(s["size"] * 2) / 2, 1)
        raw_entries.append(HeadingEntry(level=level, title=s["text"], page=s["page"]))

    for s in spans:
        key = (s["page"], s["text"])
        if key in seen_keys:
            continue
        if key in numbered_candidates:
            level = numbered_candidates[key]
            raw_entries.append(HeadingEntry(level=level, title=s["text"], page=s["page"]))
            seen_keys.add(key)

    # -----------------------------------------------------------------------
    # Pass 3 -- italic / bold headings at body size
    # -----------------------------------------------------------------------
    # Typeset documents often render section headings in italic or bold at the
    # same point size as body text.  We capture these as level-3 headings.

    # Exclude bibliography pages from styled-heading detection.
    bib_start_page: int | None = None
    for e in raw_entries:
        if _BIBLIOGRAPHY_RE.match(e.title.strip()):
            bib_start_page = e.page
            break

    styled_threshold = body_size * 0.99
    styled_candidates: list[Span] = []
    for s in spans:
        if bib_start_page is not None and s["page"] >= bib_start_page:
            continue
        if _in_margin(s, header_margin):
            continue
        key = (s["page"], s["text"])
        if key in seen_keys:
            continue
        if _is_noise(s["text"]):
            continue
        if s["size"] >= styled_threshold and (s.get("italic") or s.get("bold")):
            text = s["text"]
            # Reject lines that start with a lowercase letter (continuation
            # of a non-heading paragraph) or end with a period/digit.
            if text[0].islower():
                continue
            if text[-1] in ".0123456789":
                continue
            styled_candidates.append(s)

    # Merge wrapped styled headings before applying the length filter so that
    # a long italic title split across multiple PDF lines is joined first.
    styled_candidates = _merge_wrapped_headings(styled_candidates)

    for s in styled_candidates:
        text = s["text"]
        if len(text) > _MAX_STYLED_HEADING_LEN:
            continue
        key = (s["page"], text)
        if key in seen_keys:
            continue
        raw_entries.append(HeadingEntry(level=3, title=text, page=s["page"]))
        seen_keys.add(key)
        for comp in s.get("_merged_texts", []):
            seen_keys.add((s["page"], comp))

    # Sort by page, then by original document order (approximated by span order).
    # Use setdefault so the first occurrence wins when the same text appears
    # multiple times on the same page.
    span_order: dict[tuple[int, str], int] = {}
    for i, s in enumerate(spans):
        span_order.setdefault((s["page"], s["text"]), i)

    # Register merged (wrapped) heading texts at the position of their first
    # component so they sort correctly relative to other headings on the page.
    for s in size_candidates + styled_candidates:
        merged_texts = s.get("_merged_texts", [])
        if len(merged_texts) > 1:
            first_key = (s["page"], merged_texts[0])
            if first_key in span_order:
                span_order.setdefault((s["page"], s["text"]), span_order[first_key])

    raw_entries.sort(key=lambda e: (e.page, span_order.get((e.page, e.title), 0)))

    # -----------------------------------------------------------------------
    # Suppress running page headers (frequency-based)
    # -----------------------------------------------------------------------
    # A title on 3+ distinct pages is likely a running header; keep only the
    # first occurrence.
    title_page_set: dict[str, set[int]] = {}
    for e in raw_entries:
        title_page_set.setdefault(e.title, set()).add(e.page)
    running_headers = {
        t for t, pages in title_page_set.items() if len(pages) >= _RUNNING_HEADER_MIN_PAGES
    }
    if running_headers:
        log.debug("Running page headers suppressed: %s", sorted(running_headers))
        seen_running: set[str] = set()
        deduped: list[HeadingEntry] = []
        for e in raw_entries:
            if e.title in running_headers:
                if e.title in seen_running:
                    continue
                seen_running.add(e.title)
            deduped.append(e)
        raw_entries = deduped

    # -----------------------------------------------------------------------
    # De-duplicate adjacent identical titles (e.g. same heading repeated)
    # -----------------------------------------------------------------------
    entries: list[HeadingEntry] = []
    prev_title: str | None = None
    for e in raw_entries:
        if e.title == prev_title:
            continue
        entries.append(e)
        prev_title = e.title

    # -----------------------------------------------------------------------
    # Merge chapter labels with their following title (same page)
    # -----------------------------------------------------------------------
    # Books and structured documents typically have a chapter-opener page
    # where a label ("Chapter 1", "Part IV") and a title ("Introduction")
    # are separate typographic elements.  When merge_chapter_labels is
    # enabled, such a pair is collapsed into one bookmark entry
    # ("Chapter 1 Introduction") so the outline is clean.
    if merge_chapter_labels:
        merged: list[HeadingEntry] = []
        i = 0
        while i < len(entries):
            e = entries[i]
            nxt = entries[i + 1] if i + 1 < len(entries) else None
            if (
                nxt is not None
                and nxt.page == e.page
                and _CHAPTER_LABEL_RE.match(e.title)
                and nxt.level <= e.level  # title is at least as prominent as the label
            ):
                merged.append(
                    HeadingEntry(
                        level=min(e.level, nxt.level),
                        title=f"{e.title} {nxt.title}",
                        page=e.page,
                    )
                )
                i += 2
            else:
                merged.append(e)
                i += 1
        entries = merged

    # -----------------------------------------------------------------------
    # Filter by max_depth
    # -----------------------------------------------------------------------
    entries = [e for e in entries if e.level <= max_depth]

    log.debug("Detected %d heading(s).", len(entries))

    return entries
