import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

from bmrk.layout import BlockLabel, DocumentBlock, _merge_vertical_bands, analyze_layout

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
    left : float
        Horizontal start position of the span line on the page.
    right : float
        Horizontal end position of the span line on the page.
    page_height : float
        Height of the page in points.
    _segment_texts : list[str]
        Raw text fragments that formed this extracted line.
    """

    text: str
    size: float
    bold: bool
    italic: bool
    page: int
    top: float
    left: float
    right: float
    page_height: float
    _segment_texts: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches common numeric / alphanumeric section prefixes:
#   "1  Introduction"
#   "2.3  Methods"
#   "A.1  Appendix"
#   "2.1 Problem Simplification"
#
# Single-space separators are accepted syntactically and then filtered by
# _extract_numbered_depth to avoid common sentence-style false positives.
_NUMBERED_RE = re.compile(
    r"^(?P<prefix>"
    r"(?:[A-Z]\.?\d*|"  # Appendix-style: A, A.1
    r"\d+(?:\.\d+){0,3})"  # Numeric: 1, 1.2, 1.2.3, 1.2.3.4
    r"\.?)"
    r"(?P<sep>\s{1,6})"
    r"(?P<title>\S.*)"
)

# Strings that look like page numbers, running headers, figure captions etc.
_NOISE_RE = re.compile(
    r"^(?:\d+|page\s+\d+|figure\s+\d+|table\s+\d+|fig\.\s*\d+)$",
    re.IGNORECASE,
)

# TOC lines often end with "... 12" or "......12" or just "12" after dots/spaces.
_TOC_LINE_RE = re.compile(r".{5,}[\s.]{2,}\d{1,4}\s*$")

# OCR-tolerant TOC entry detector:
#   "Chapter 2 Brain Teasers .......... 3"
#   "2.1 Problem Simplification ...cccc... 3"
#   "4.5 Expected Value, Variance & Covariance 92"
_TOC_ENTRY_RE = re.compile(
    r"^(?:(?:chapter|part|appendix)\s+\S.*|"
    r"(?:[A-Z]\.?\d*|\d+(?:\.\d+){0,4})\.?\s+\S.*)"
    r"\s+\d{1,4}\s*$",
    re.IGNORECASE,
)

_TOC_HEADING_RE = re.compile(r"^(?:table\s+of\s+contents|contents)$", re.IGNORECASE)
_ROMAN_PAGE_RE = re.compile(r"^(?:[ivxlcdm]{1,10})$", re.IGNORECASE)

_MAX_HEADING_LEN = 200  # characters – very long lines are likely body text
_MIN_HEADING_LEN = 2

# Fraction of non-noise lines that must look like TOC entries for a page to be
# treated as a Table of Contents page.
_TOC_PAGE_THRESHOLD = 0.45

# OCR'd TOC pages often have broken leaders/page numbers and only a modest
# fraction of lines that still look like TOC entries. For pages adjacent to a
# confirmed TOC page, use a lower threshold when expanding contiguous TOC runs.
_TOC_NEIGHBOR_THRESHOLD = 0.18

# Structural label words that, when followed by a number or identifier, mark a
# chapter/part opener.  The regex matches the full heading text so that a bare
# "Introduction" (which is itself a complete heading) is NOT treated as a label,
# but "Chapter 1" or "Part IV" are.
_CHAPTER_LABEL_RE = re.compile(
    r"^(?:chapter|part|book|section|appendix|lecture|unit|module|episode)\s+\S+$",
    re.IGNORECASE,
)

# Broad chapter/part opener detector. Used as an anchor signal when inferring
# numeric heading depth mappings.
_CHAPTER_ANCHOR_RE = re.compile(
    r"^(?:chapter|part|book|appendix|lecture|unit|module|episode)\s+\S+",
    re.IGNORECASE,
)

# Unicode character class for math symbols and operators.  A span whose
# non-whitespace characters are predominantly drawn from these ranges is
# almost certainly a formula fragment, not a heading.
_MATH_CHAR_RE = re.compile(
    r"[Ͱ-Ͽ"  # Greek and Coptic
    r"℀-⅏"  # Letterlike Symbols
    r"←-⇿"  # Arrows
    r"∀-⋿"  # Mathematical Operators
    r"⌀-⏿"  # Miscellaneous Technical
    r"⟀-⟯"  # Miscellaneous Mathematical Symbols-A
    r"⦀-⧿"  # Miscellaneous Mathematical Symbols-B
    r"⨀-⫿"  # Supplemental Mathematical Operators
    r"\U0001D400-\U0001D7FF"  # Mathematical Alphanumeric Symbols
    r"=+\-*/^~<>|"  # Common ASCII math operators
    r"()\[\]{}]"  # Brackets and braces
)

_MATH_SPAN_MAX_LEN = 20  # math fragments extracted as spans are short

# Headings that mark the start of a bibliography / references section.
# Everything on the same page and later pages is excluded from Pass 3
# (styled-heading detection) to avoid picking up italic book titles.
_BIBLIOGRAPHY_RE = re.compile(
    r"^(?:bibliography|references|works cited|sources|further reading)$",
    re.IGNORECASE,
)
_TABLE_CAPTION_RE = re.compile(r"^table\s+\d+(?:\.\d+)?\b", re.IGNORECASE)

_NUMERIC_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")
_ALPHA_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_TOKEN_RE = re.compile(r"\S+")
_SYMBOL_RE = re.compile(r"[\\/|_=+*^~<>()[\]{}:-]")
_EQUATION_OP_RE = re.compile(r"[=+\-*/^<>≤≥≈≠⇒→←]")
_BODY_START_RE = re.compile(r"^(?:let|if|suppose|assume|again|then|so|for)\b", re.IGNORECASE)


def _normalize_spaced_caps(text: str) -> str:
    """Collapse OCR-spaced all-caps headings like ``C O N T E N T S``."""
    normalized = " ".join(text.strip().split())
    if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]", normalized):
        return normalized.replace(" ", "")
    return normalized


def _is_toc_heading_text(text: str) -> bool:
    """Return True when *text* is a TOC heading, tolerating spaced OCR caps."""
    return bool(_TOC_HEADING_RE.match(_normalize_spaced_caps(text)))


def _is_page_number_like_text(text: str) -> bool:
    """Return True for standalone TOC page numbers in Arabic or Roman numerals."""
    stripped = text.strip()
    return stripped.isdigit() or bool(_ROMAN_PAGE_RE.match(stripped))


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


def _extract_numbered_depth(text: str) -> int | None:
    """
    Return numeric heading depth inferred from *text*, or None.

    The parser supports both 2+ space separators and selected single-space
    formats. Single-space is accepted only for dotted numeric prefixes
    (e.g. ``"2.1 Title"``), which avoids most sentence-style false positives.
    """
    m = _NUMBERED_RE.match(text.strip())
    if not m:
        return None

    prefix = m.group("prefix")
    sep = m.group("sep")
    title = m.group("title")

    # Numbered headings should have a textual title, not a second numeric
    # expression or equation fragment.
    if not title or not title[0].isalpha():
        return None
    if _is_likely_equation_line(title):
        return None

    # Single-space separators are noisy for plain prefixes ("1 Intro", "A title").
    # Keep them only for dotted numeric forms ("2.1 Intro", "3.2.1 Details").
    if len(sep) == 1:
        core = prefix.rstrip(".")
        if "." not in core:
            return None
        if title and title[0].islower():
            return None

    return _numeric_depth(prefix)


def _infer_numeric_level_offset(depths: list[int], has_anchor: bool) -> int:
    """
    Infer a document-specific offset from numeric depth to heading level.

    When structural anchors (e.g. "Chapter 2 ...") are present, keep numeric
    depth absolute (offset 0). Without anchors, and when numbering starts
    deeper than 1, normalize so the minimum observed depth maps to level 1.
    """
    if not depths:
        return 0
    min_depth = min(depths)
    if has_anchor or min_depth <= 1:
        return 0
    return min_depth - 1


def _numeric_depth_to_level(depth: int, offset: int, max_levels: int) -> int:
    """Map numeric depth to heading level after applying inferred *offset*."""
    return max(1, min(max_levels, depth - offset))


def _is_numeric_table_row(text: str) -> bool:
    """
    Return True if *text* looks like a table row of numeric values.

    Such rows can mimic dotted numeric prefixes (e.g. ``1.623 5.018 ...``)
    and should not be treated as numbered headings.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if _ALPHA_RE.search(stripped):
        return False
    tokens = _NUMERIC_TOKEN_RE.findall(stripped)
    if len(tokens) < 3:
        return False
    leftovers = re.sub(r"[\d\s.,:%+\-()/]", "", stripped)
    return leftovers == ""


def _is_sentence_like_text(text: str) -> bool:
    """
    Return True when *text* has sentence-like casing patterns.

    Long body lines typically contain many lowercase-starting words after
    the first token, unlike title-style headings.
    """
    words = _WORD_RE.findall(text)
    if len(words) < 8:
        return False
    lower_inside = sum(1 for w in words[1:] if w[0].islower())
    return lower_inside >= 3


def _is_likely_diagram_label(text: str, size: float, body_size: float) -> bool:
    r"""
    Return True when *text* looks like OCR noise from an in-page diagram.

    Diagram labels often become short, symbol-heavy, oversized OCR snippets
    (e.g. ``"6H )-O"``, ``"/ \\ IN"``) that are not usable heading text.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > 24:
        return False
    if size < body_size * 1.45:
        return False

    words = _WORD_RE.findall(stripped)
    alpha_count = sum(ch.isalpha() for ch in stripped)
    digit_count = sum(ch.isdigit() for ch in stripped)
    symbol_count = len(_SYMBOL_RE.findall(stripped))
    non_space_count = sum(1 for ch in stripped if not ch.isspace())

    if not words:
        return True

    has_long_word = any(len(w) >= 4 for w in words)
    if not has_long_word and (digit_count > 0 or symbol_count > 0):
        return True

    if non_space_count and (alpha_count / non_space_count) < 0.45:
        return True

    return False


def _is_likely_equation_line(text: str) -> bool:
    """
    Return True when *text* looks like an inline/display equation OCR line.

    Keeps mathematical symbols inside normal titles possible, but suppresses
    operator/digit-heavy equation strings from heading candidates.
    """
    stripped = text.strip()
    if not stripped:
        return False

    words = _WORD_RE.findall(stripped)
    max_word_len = max((len(w) for w in words), default=0)
    long_words = sum(1 for w in words if len(w) >= 5)
    short_words = sum(1 for w in words if len(w) <= 4)
    ops = len(_EQUATION_OP_RE.findall(stripped))
    digits = sum(ch.isdigit() for ch in stripped)
    letters = sum(ch.isalpha() for ch in stripped)
    non_space = sum(1 for ch in stripped if not ch.isspace())

    if stripped.endswith("=") and len(stripped) <= 60:
        return True
    if ("=>" in stripped or "->" in stripped or "⇒" in stripped) and ops >= 2:
        return True
    if ops >= 3 and (digits >= 2 or letters <= 6):
        return True
    if ops >= 1 and max_word_len <= 4 and short_words >= 3:
        return True
    if digits >= 1 and max_word_len <= 3 and len(words) <= 3:
        return True
    if ops >= 1 and short_words >= 3 and long_words == 0:
        return True
    if "=" in stripped and "?" in stripped:
        _, _, tail = stripped.rpartition("=")
        tail = tail.strip(" ?'\"’")
        tail_words = _WORD_RE.findall(tail)
        tail_ops = len(_EQUATION_OP_RE.findall(tail))
        tail_digits = sum(ch.isdigit() for ch in tail)
        tail_max_word_len = max((len(w) for w in tail_words), default=0)
        if (
            tail
            and tail_max_word_len <= 4
            and (tail_ops >= 1 or tail_digits >= 1 or len(tail_words) <= 2)
        ):
            return True
    if ":" in stripped:
        _, tail = stripped.split(":", 1)
        tail = tail.strip()
        tail_words = _WORD_RE.findall(tail)
        tail_ops = len(_EQUATION_OP_RE.findall(tail))
        tail_digits = sum(ch.isdigit() for ch in tail)
        tail_symbols = len(_SYMBOL_RE.findall(tail))
        tail_non_space = sum(1 for ch in tail if not ch.isspace())
        tail_max_word_len = max((len(w) for w in tail_words), default=0)
        if tail and ("[" in tail or "]" in tail or "(" in tail or ")" in tail):
            if tail_ops >= 1 or tail_digits >= 1 or tail_max_word_len <= 4:
                return True
        if (
            tail
            and tail_non_space
            and tail_ops >= 1
            and (
                tail_max_word_len <= 4
                or tail_digits >= 1
                or (tail_digits + tail_symbols) / tail_non_space >= 0.35
            )
        ):
            return True
    if ops >= 2 and non_space:
        math_density = (ops + digits) / non_space
        alpha_density = letters / non_space
        if math_density >= 0.35 and alpha_density <= 0.55:
            return True

    return False


def _is_likely_plain_heading_text(text: str) -> bool:
    """
    Return True when *text* has lexical structure expected of a heading.

    This is used for plain (non-bold/italic, non-numbered) candidates to
    suppress OCR fragments and equation-like lines.
    """
    stripped = text.strip()
    if not stripped:
        return False

    words = _WORD_RE.findall(stripped)
    if not words:
        return False

    tokens = _TOKEN_RE.findall(stripped)
    medium_words = sum(1 for w in words if len(w) >= 3)
    long_words = sum(1 for w in words if len(w) >= 5)
    digits = sum(ch.isdigit() for ch in stripped)
    ops = len(_EQUATION_OP_RE.findall(stripped))
    symbols = len(_SYMBOL_RE.findall(stripped))
    non_space = sum(1 for ch in stripped if not ch.isspace())

    # Equation-heavy prefixes like "(x+1)..." or "! <6<1 ..."
    if stripped[0] in "([{-!.,;:" and (digits > 0 or ops > 0):
        return False

    # Typical equation body line: operator + digits.
    if ops >= 1 and digits >= 1:
        return False

    # Factorial-heavy expressions.
    if re.search(r"\d\s*!", stripped):
        return False

    # Q/A markers ("A. ...", "B. ...") often denote inline questions, not
    # navigational headings, unless the line is clearly rich in normal words.
    if re.match(r"^[A-Z]\.\s+", stripped) and medium_words < 3:
        return False

    # Plain headings should have enough lexical content.
    if len(words) == 1:
        if len(words[0]) < 4:
            return False
    elif medium_words < 2:
        return False

    # Symbol/digit-dense snippets are usually OCR artifacts or equations.
    if non_space and ((digits + symbols) / non_space) > 0.35 and long_words < 2:
        return False

    # Token soup: many short noisy tokens with little lexical content.
    if len(tokens) >= 3:
        short_tokens = sum(1 for t in tokens if len(t) <= 3)
        noisy_tokens = sum(
            1
            for t in tokens
            if any(ch.isdigit() for ch in t) or any(not ch.isalnum() for ch in t)
        )
        if (
            short_tokens / len(tokens) >= 0.66
            and noisy_tokens / len(tokens) >= 0.5
            and long_words < 2
        ):
            return False

    return True


def _is_table_cell_token(text: str) -> bool:
    """Return True when *text* looks like a compact table cell value."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped in {"|", "?", "-", "—"}:
        return True
    if len(stripped) == 1:
        return True
    if any(ch.isdigit() for ch in stripped):
        return True
    punct = sum((not ch.isalnum()) and (not ch.isspace()) for ch in stripped)
    return punct >= 1 and len(stripped) <= 4


def _is_sparse_table_row(span: Span, body_size: float) -> bool:
    """
    Return True when *span* looks like a full OCR table row.

    Table rows often contain a short label plus compact cell values spread
    across a wide line. That yields unusually low text density compared with
    normal prose lines.
    """
    if span.get("bold") or span.get("italic"):
        return False

    text = span.get("text", "").strip()
    if not text or len(text) > 24:
        return False

    tokens = [t for t in span.get("_segment_texts", []) if t.strip()]
    if len(tokens) < 4:
        return False

    width = span.get("right", 0) - span.get("left", 0)
    compact_len = len(text.replace(" ", ""))
    if width <= 0 or compact_len <= 0:
        return False

    density = width / compact_len
    cellish = sum(_is_table_cell_token(t) for t in tokens)
    return density >= body_size * 2.4 and cellish >= 3


def _looks_like_table_label_fragment(text: str) -> bool:
    """Return True when *text* looks like a short row label inside a table."""
    stripped = text.strip().strip("|").strip()
    if not stripped:
        return False
    if " " not in stripped:
        return False
    alpha = sum(ch.isalpha() for ch in stripped)
    return alpha >= 4 and len(stripped) <= 18


def _infer_table_bands(spans: list[Span], body_size: float) -> dict[int, list[tuple[float, float]]]:
    """
    Infer vertical table regions from OCR text structure.

    Two signals are used:
    - sparse full-row lines with compact cell values spread across the width
    - tight vertical clusters of short spans aligned into multiple columns

    This is designed for OCR-heavy books where vector table detection is not
    available or reliable.
    """
    by_page: dict[int, list[Span]] = {}
    for span in spans:
        by_page.setdefault(span["page"], []).append(span)

    x_bin = max(body_size * 4.0, 36.0)
    window_height = body_size * 4.5
    result: dict[int, list[tuple[float, float]]] = {}

    for page, page_spans in by_page.items():
        page_spans = sorted(page_spans, key=lambda s: s.get("top", 0))
        bands: list[tuple[float, float]] = []

        for span in page_spans:
            if _is_sparse_table_row(span, body_size):
                top = span.get("top", 0) - body_size * 0.5
                bottom = span.get("top", 0) + span.get("size", 0) + body_size
                bands.append((top, bottom))

        for idx, span in enumerate(page_spans):
            cluster: list[Span] = []
            start_top = span.get("top", 0)
            for other in page_spans[idx:]:
                if other.get("top", 0) - start_top > window_height:
                    break
                text = other.get("text", "").strip()
                if not text or len(text) > 16 or len(text.split()) > 4:
                    continue
                cluster.append(other)

            if len(cluster) < 4:
                continue

            x_bins = {round(other.get("left", 0) / x_bin) for other in cluster}
            if len(x_bins) < 3:
                continue
            if not any(
                _looks_like_table_label_fragment(other.get("text", ""))
                for other in cluster
            ):
                continue

            top = min(other.get("top", 0) for other in cluster) - body_size * 0.5
            bottom = (
                max(other.get("top", 0) + other.get("size", 0) for other in cluster)
                + body_size * 0.8
            )
            bands.append((top, bottom))

        merged = _merge_vertical_bands(bands, gap=body_size * 0.8)

        for caption in page_spans:
            if not _TABLE_CAPTION_RE.match(caption.get("text", "").strip()):
                continue

            band_map = {page: merged}
            block: list[Span] = []
            prev_top = caption.get("top", 0)
            started = False

            for span in reversed(page_spans):
                if span.get("top", 0) >= caption.get("top", 0):
                    continue
                if prev_top - span.get("top", 0) > body_size * 7.5:
                    if started:
                        break
                    continue

                if _in_table_band(span, band_map) or _is_caption_adjacent_table_line(
                    span,
                    body_size,
                ):
                    block.append(span)
                    started = True
                    prev_top = span.get("top", 0)
                    continue

                if started:
                    break

            if block:
                top = min(span.get("top", 0) for span in block) - body_size * 0.5
                bottom = (
                    max(span.get("top", 0) + span.get("size", 0) for span in block)
                    + body_size * 0.8
                )
                bands.append((top, bottom))

        merged = _merge_vertical_bands(bands, gap=body_size * 0.8)
        if merged:
            result[page] = merged

    return result


def _in_table_band(span: Span, table_bands: dict[int, list[tuple[float, float]]]) -> bool:
    """Return True when *span* lies inside an inferred table band."""
    for top, bottom in table_bands.get(span.get("page", -1), []):
        if top <= span.get("top", 0) <= bottom:
            return True
    return False


def _is_caption_adjacent_table_line(span: Span, body_size: float) -> bool:
    """
    Return True when *span* looks like table content near a table caption.

    This is looser than `_is_sparse_table_row`: it is only used when expanding
    an already-detected table area up toward its header rows.
    """
    text = span.get("text", "").strip()
    if not text or _TABLE_CAPTION_RE.match(text):
        return False
    if text.endswith("."):
        return False
    if _extract_numbered_depth(text) is not None or _CHAPTER_ANCHOR_RE.match(text):
        return False

    width = span.get("right", 0) - span.get("left", 0)
    compact_len = len(text.replace(" ", ""))
    if width <= 0 or compact_len <= 0:
        return False

    density = width / compact_len
    tokens = [t for t in span.get("_segment_texts", []) if t.strip()]
    cellish = sum(_is_table_cell_token(t) for t in tokens)
    has_table_symbol = any(ch in text for ch in "()[]/=|+-")
    words = _WORD_RE.findall(text)
    medium_words = sum(1 for w in words if len(w) >= 3)
    if len(text) <= 24 and not has_table_symbol and medium_words >= 2:
        return False

    if _is_sparse_table_row(span, body_size):
        return True
    if len(text) <= 18 and density >= body_size * 2.0:
        return True
    if len(text) <= 18 and any(ch in text for ch in "()[]/") and density >= body_size * 1.3:
        return True
    if has_table_symbol and len(tokens) >= 5 and density >= body_size * 0.8:
        return True
    if len(text) <= 60 and cellish >= 2 and density >= body_size * 1.0:
        return True
    if (
        len(text) <= 60
        and density >= body_size * 1.2
        and any(
            any(ch.isdigit() for ch in token) or any(ch in token for ch in "=<>")
            for token in tokens
        )
    ):
        return True
    if len(text) <= 60 and _is_likely_equation_line(text) and density >= body_size * 0.9:
        return True
    if len(text) <= 12 and density >= body_size * 1.5:
        return True
    if len(text) <= 12 and not _is_likely_plain_heading_text(text):
        return True
    return False


def _is_plain_neighbor_line(cur: Span, other: Span | None) -> bool:
    """
    Return True when *other* looks like an adjacent plain body-text line.

    This is used to suppress styled (italic/bold) body fragments that occur
    mid-paragraph and would otherwise look like headings in Pass 3.
    """
    if other is None:
        return False
    if other.get("page") != cur.get("page"):
        return False
    if abs(other.get("size", 0) - cur.get("size", 0)) > 0.5:
        return False
    if other.get("bold") or other.get("italic"):
        return False
    gap = abs(other.get("top", 0) - cur.get("top", 0))
    return gap <= cur.get("size", 0) * 1.9


def _is_short_math_fragment(text: str) -> bool:
    """
    Return True when *text* looks like a short inline math fragment.

    This is used to bridge body sentences that are split by OCR around a
    displayed symbol or tiny matrix/vector snippet.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if _is_math_span(stripped) or _is_likely_equation_line(stripped):
        return True
    words = _WORD_RE.findall(stripped)
    return len(stripped) <= 4 or (len(words) <= 1 and len(stripped) <= 8)


def _is_body_starter_with_continuation(cur: Span, nxt: Span | None, nxt2: Span | None) -> bool:
    """
    Return True when *cur* is a body sentence start, not a heading.

    Handles mathematical prose like ``"Let A be ..."`` where OCR splits a
    sentence across a short formula fragment and a lowercase continuation line.
    """
    text = cur.get("text", "").strip()
    if not _BODY_START_RE.match(text):
        return False

    if nxt is not None and nxt.get("page") == cur.get("page"):
        gap1 = nxt.get("top", 0) - cur.get("top", 0)
        if 0 < gap1 <= cur.get("size", 0) * 2.0:
            nxt_text = nxt.get("text", "")
            if nxt_text and nxt_text[0].islower():
                return True

    if (
        nxt is None
        or nxt2 is None
        or nxt.get("page") != cur.get("page")
        or nxt2.get("page") != cur.get("page")
    ):
        return False

    gap1 = nxt.get("top", 0) - cur.get("top", 0)
    gap2 = nxt2.get("top", 0) - nxt.get("top", 0)
    nxt2_text = nxt2.get("text", "")
    if not nxt2_text or not nxt2_text[0].islower():
        return False
    if not (0 < gap1 <= cur.get("size", 0) * 1.0 and 0 < gap2 <= cur.get("size", 0) * 1.4):
        return False
    return _is_short_math_fragment(nxt.get("text", ""))


def _is_numeric_continuation_line(cur: Span, prev: Span | None) -> bool:
    """
    Return True when *cur* is a wrapped body-text tail, not a heading.

    This catches lines like ``"50 baskets?"`` that continue a question from
    the line above rather than introducing a new section.
    """
    text = cur.get("text", "").strip()
    if not text or not text[0].isdigit():
        return False
    if cur.get("bold") or cur.get("italic"):
        return False
    if prev is None or prev.get("page") != cur.get("page"):
        return False
    if prev.get("bold") or prev.get("italic"):
        return False

    prev_text = prev.get("text", "").rstrip()
    if not prev_text or prev_text[-1] in ".:;!?":
        return False
    if len(prev_text) < 25:
        return False
    if cur.get("size", 0) > prev.get("size", 0) * 1.2:
        return False

    gap = cur.get("top", 0) - prev.get("top", 0)
    return 0 < gap <= cur.get("size", 0) * 1.6


def _is_colon_math_introducer(cur: Span, nxt: Span | None, nxt2: Span | None) -> bool:
    """
    Return True when *cur* is a body label introducing displayed math.

    Example:
        ``"Example rule:"`` followed by one or two short equation fragments.
    """
    text = cur.get("text", "").strip()
    if not text.endswith(":"):
        return False
    if cur.get("bold") or cur.get("italic"):
        return False

    nearby = [s for s in (nxt, nxt2) if s is not None and s.get("page") == cur.get("page")]
    if not nearby:
        return False

    math_like = 0
    for other in nearby:
        gap = other.get("top", 0) - cur.get("top", 0)
        if not (0 < gap <= cur.get("size", 0) * 4.5):
            continue
        other_text = other.get("text", "").strip()
        if not other_text:
            continue
        if (
            _is_short_math_fragment(other_text)
            or _is_likely_equation_line(other_text)
            or (len(other_text) <= 12 and not _is_likely_plain_heading_text(other_text))
        ):
            math_like += 1

    return math_like >= 1


def _is_likely_paragraph_lead(cur: Span, nxt: Span | None, body_size: float) -> bool:
    """
    Return True when *cur* looks like the first visual line of a paragraph.

    OCR noise sometimes bumps the font size of a body-text lead line just
    above the heading threshold. This catches that pattern:
    - plain (not bold/italic)
    - only slightly above body size
    - long line followed by a lowercase continuation line at similar size
    """
    if nxt is None or nxt.get("page") != cur.get("page"):
        return False
    if cur.get("bold") or cur.get("italic"):
        return False
    if cur.get("size", 0) > body_size * 1.15:
        return False
    text = cur.get("text", "")
    if len(text) < 45:
        return False
    nxt_text = nxt.get("text", "")
    if not nxt_text or not nxt_text[0].islower():
        return False
    if abs(nxt.get("size", 0) - cur.get("size", 0)) > 0.7:
        return False
    gap = nxt.get("top", 0) - cur.get("top", 0)
    return 0 < gap <= cur.get("size", 0) * 1.9


def _in_margin(span: Span, ratio: float) -> bool:
    """
    Return True if *span* lies within the page header or footer margin zone.

    Parameters
    ----------
    span : Span
        Span as returned by the layout extractor.
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
        Spans from a single page, as returned by the layout extractor.

    Returns
    -------
    bool
        True if this page appears to be a Table of Contents.
    """
    non_noise = [s["text"] for s in page_spans if not _is_noise(s["text"])]

    # Explicit TOC heading is a strong signal, especially for OCR'd books
    # where leader dots/page numbers are corrupted.
    if any(_is_toc_heading_text(t) for t in non_noise):
        return True

    if len(non_noise) < 3:
        return False
    toc_count = sum(1 for t in non_noise if _TOC_LINE_RE.match(t) or _TOC_ENTRY_RE.match(t))
    return (toc_count / len(non_noise)) >= _TOC_PAGE_THRESHOLD


def _toc_likeness(page_spans: list[Span]) -> float:
    """
    Return a soft TOC-likeness score in [0, 1] for a page.

    Score is the fraction of non-empty lines matching either strict or
    OCR-tolerant TOC entry patterns.
    """
    lines = [s["text"].strip() for s in page_spans if s["text"].strip()]
    if not lines:
        return 0.0
    toc_count = sum(1 for t in lines if _TOC_LINE_RE.match(t) or _TOC_ENTRY_RE.match(t))
    return toc_count / len(lines)


def _is_toc_continuation_page(page_spans: list[Span]) -> bool:
    """Return True for TOC continuation pages with stacked title/page-number lines."""
    lines = [s["text"].strip() for s in page_spans if s["text"].strip()]
    if len(lines) < 8:
        return False

    page_number_lines = sum(1 for text in lines if _is_page_number_like_text(text))
    title_like_lines = sum(
        1
        for text in lines
        if not _is_page_number_like_text(text)
        and not _is_noise(text)
        and not _is_sentence_like_text(text)
        and len(text) <= 90
    )
    structural_lines = sum(
        1
        for text in lines
        if _CHAPTER_ANCHOR_RE.match(text) or _extract_numbered_depth(text) is not None
    )

    return page_number_lines >= 4 and title_like_lines >= 6 and (
        structural_lines >= 2 or page_number_lines >= 8
    )


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------


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
    # Normalize to 0.5pt buckets (same as the layout body-size estimate) so
    # that the keys produced here match the rounded lookup in detect_headings.
    normalized = [round(sz * 2) / 2 for sz in heading_sizes]
    unique = sorted(set(normalized), reverse=True)
    mapping = {}
    for i, sz in enumerate(unique[:max_levels]):
        mapping[sz] = i + 1
    for sz in unique[max_levels:]:
        mapping[sz] = max_levels
    return mapping


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _extract_heading_blocks(layout) -> list[DocumentBlock]:
    """Return heading-candidate blocks in document order."""
    return [
        block
        for block in sorted(layout.blocks, key=lambda item: (item.page, item.bbox[1], item.bbox[0]))
        if block.label == BlockLabel.HEADING_CANDIDATE
    ]


def _build_heading_entries(
    heading_blocks: list[DocumentBlock],
    *,
    body_size: float,
    size_threshold_ratio: float,
    merge_chapter_labels: bool,
    max_depth: int,
) -> list[HeadingEntry]:
    """Convert heading-candidate blocks into final heading entries."""
    threshold = body_size * size_threshold_ratio
    max_levels = max(max_depth, 3)
    numbered_depths = [
        block.features["numeric_depth"]
        for block in heading_blocks
        if block.features.get("numeric_depth") is not None
    ]
    has_structural_anchor = any(block.features.get("chapter_anchor") for block in heading_blocks)
    numeric_level_offset = _infer_numeric_level_offset(
        numbered_depths, has_anchor=has_structural_anchor
    )

    size_candidates = [block for block in heading_blocks if block.dominant_size > threshold]
    size_map = _assign_heading_levels(
        [block.dominant_size for block in size_candidates], max_levels=max_levels
    )

    bib_start_page: int | None = None
    for block in heading_blocks:
        if _BIBLIOGRAPHY_RE.match(block.text.strip()):
            bib_start_page = block.page
            break

    raw_entries: list[HeadingEntry] = []
    for block in heading_blocks:
        text = block.text
        numeric_depth = block.features.get("numeric_depth")
        is_anchor_heading = bool(block.features.get("chapter_anchor"))

        # Suppress bibliography-page styled items that are not structurally deeper.
        if (
            bib_start_page is not None
            and block.page >= bib_start_page
            and block.dominant_size <= threshold
            and (block.bold or block.italic)
            and numeric_depth is None
            and not is_anchor_heading
            and not _BIBLIOGRAPHY_RE.match(text.strip())
        ):
            continue

        if block.dominant_size > threshold:
            size_level = size_map.get(round(block.dominant_size * 2) / 2, 1)
        elif block.bold or block.italic:
            size_level = 1 if is_anchor_heading else 3
        else:
            size_level = 1

        if is_anchor_heading:
            level = 1
        elif numeric_depth is not None:
            numeric_level = _numeric_depth_to_level(
                numeric_depth, numeric_level_offset, max_levels=max_levels
            )
            if block.dominant_size > threshold and not (block.bold or block.italic):
                level = numeric_level if has_structural_anchor else max(size_level, numeric_level)
            elif block.dominant_size > threshold:
                level = numeric_level if has_structural_anchor else max(size_level, numeric_level)
            else:
                level = numeric_level
        else:
            level = size_level

        raw_entries.append(HeadingEntry(level=level, title=text, page=block.page))

    entries: list[HeadingEntry] = []
    prev_title: str | None = None
    for entry in raw_entries:
        if entry.title == prev_title:
            continue
        entries.append(entry)
        prev_title = entry.title

    if merge_chapter_labels:
        merged: list[HeadingEntry] = []
        i = 0
        while i < len(entries):
            entry = entries[i]
            nxt = entries[i + 1] if i + 1 < len(entries) else None
            if (
                nxt is not None
                and nxt.page == entry.page
                and _CHAPTER_ANCHOR_RE.match(entry.title)
                and _extract_numbered_depth(nxt.title) is None
                and not _CHAPTER_LABEL_RE.match(nxt.title)
                and nxt.level <= entry.level + 1
            ):
                merged.append(
                    HeadingEntry(
                        level=min(entry.level, nxt.level),
                        title=f"{entry.title} {nxt.title}",
                        page=entry.page,
                    )
                )
                i += 2
                continue
            merged.append(entry)
            i += 1
        entries = merged

    return [entry for entry in entries if entry.level <= max_depth]


def build_headings_from_layout(
    layout,
    *,
    size_threshold_ratio: float = 1.05,
    merge_chapter_labels: bool = True,
    max_depth: int = 3,
) -> list[HeadingEntry]:
    """Convert a precomputed layout into final heading entries."""
    if not layout.lines:
        raise NoReadableTextError(
            "No selectable text found in this PDF. "
            "It may be a scanned image. Re-run with --ocr to add a text layer "
            "automatically, or pre-process with: ocrmypdf input.pdf input_ocr.pdf"
        )

    body_size = float(layout.body_cluster.get("size", 11.0))
    threshold = body_size * size_threshold_ratio
    heading_blocks = _extract_heading_blocks(layout)

    log.debug(
        "Body font size estimated at %.1fpt (heading threshold: >%.1fpt)",
        body_size,
        threshold,
    )
    log.debug(
        "Detected %d heading-candidate block(s) after layout labeling.",
        len(heading_blocks),
    )

    entries = _build_heading_entries(
        heading_blocks,
        body_size=body_size,
        size_threshold_ratio=size_threshold_ratio,
        merge_chapter_labels=merge_chapter_labels,
        max_depth=max_depth,
    )

    log.debug("Detected %d heading(s).", len(entries))
    return entries


def detect_headings(
    pdf_path: str,
    size_threshold_ratio: float = 1.05,
    on_page: Callable[[int, int], None] | None = None,
    skip_pages: int = 0,
    skip_toc: bool = True,
    header_margin: float = 0.0,
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
        Optional exclusion margin (fraction of page height) for spans at the
        very top/bottom of the page.  Default is 0 (no exclusion).  Running
        headers are primarily suppressed by repetition-based detection, so
        this is only an extra safety valve for noisy PDFs.
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
    layout = analyze_layout(
        pdf_path,
        size_threshold_ratio=size_threshold_ratio,
        on_page=on_page,
        skip_pages=skip_pages,
        skip_toc=skip_toc,
        header_margin=header_margin,
    )
    return build_headings_from_layout(
        layout,
        size_threshold_ratio=size_threshold_ratio,
        merge_chapter_labels=merge_chapter_labels,
        max_depth=max_depth,
    )
