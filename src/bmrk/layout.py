from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("bmrk")


class BlockLabel(str, Enum):
    """Structural labels assigned to document blocks."""

    HEADING_CANDIDATE = "heading_candidate"
    BODY_PARAGRAPH = "body_paragraph"
    DISPLAY_MATH = "display_math"
    TABLE_REGION = "table_region"
    CAPTION = "caption"
    PROBLEM_PROMPT = "problem_prompt"
    RUNNING_HEADER_FOOTER = "running_header_footer"
    TOC_ENTRY = "toc_entry"
    NOISE = "noise"


@dataclass
class RawLine:
    """A normalized text line extracted from a PDF page."""

    page: int
    text: str
    bbox: tuple[float, float, float, float]
    top: float
    bottom: float
    left: float
    right: float
    page_width: float
    page_height: float
    size: float
    bold: bool
    italic: bool
    block_id: int
    line_id: int
    segment_texts: list[str]


@dataclass
class DocumentBlock:
    """A grouped document block with structural metadata."""

    page: int
    bbox: tuple[float, float, float, float]
    text: str
    lines: list[RawLine]
    dominant_size: float
    bold: bool
    italic: bool
    centered: bool
    indent: float
    label: BlockLabel = BlockLabel.NOISE
    confidence: float = 0.0
    features: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentLayout:
    """Full layout analysis for a PDF."""

    lines: list[RawLine]
    blocks: list[DocumentBlock]
    body_cluster: dict[str, Any]
    toc_pages: set[int]


_CAPTION_RE = re.compile(r"^(?:table|figure|fig\.)\s+\d+(?:\.\d+)?\b", re.IGNORECASE)
_PROMPT_RE = re.compile(r"^(?:[A-Z]\.|Q\.|\d+\.)\s+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_PAGE_NUMBER_RE = re.compile(r"\b\d{1,4}\b")


def _fitz_module():
    from bmrk import detector as detector_module

    return detector_module.fitz


def _detector_helpers():
    from bmrk import detector as detector_module

    return detector_module


def _rounded_size(size: float) -> float:
    return round(size * 2) / 2


def _normalize_running_text(text: str) -> str:
    text = _PAGE_NUMBER_RE.sub(" ", text.lower())
    return " ".join(text.split())


def _line_to_span(line: RawLine) -> dict[str, Any]:
    return {
        "text": line.text,
        "size": line.size,
        "bold": line.bold,
        "italic": line.italic,
        "page": line.page,
        "top": line.top,
        "left": line.left,
        "right": line.right,
        "page_height": line.page_height,
        "_segment_texts": list(line.segment_texts),
    }


def _block_to_span(block: DocumentBlock) -> dict[str, Any]:
    left, top, right, _ = block.bbox
    page_height = (
        block.lines[0].page_height
        if block.lines
        else float(block.features.get("page_height") or 0.0)
    )
    return {
        "text": block.text,
        "size": block.dominant_size,
        "bold": block.bold,
        "italic": block.italic,
        "page": block.page,
        "top": top,
        "left": left,
        "right": right,
        "page_height": page_height,
        "_segment_texts": list(block.features.get("segment_texts", [])),
    }


def _merge_vertical_bands(
    bands: list[tuple[float, float]], *, gap: float
) -> list[tuple[float, float]]:
    if not bands:
        return []

    merged: list[tuple[float, float]] = []
    for start, end in sorted(bands):
        if not merged or start > merged[-1][1] + gap:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        merged[-1] = (prev_start, max(prev_end, end))
    return merged


def _read_pdf_artifacts(
    pdf_path: str,
    on_page=None,
) -> tuple[list[RawLine], dict[int, list[tuple[float, float, float, float]]]]:
    fitz = _fitz_module()
    lines: list[RawLine] = []
    table_boxes: dict[int, list[tuple[float, float, float, float]]] = {}

    with fitz.open(pdf_path) as doc:
        total = len(doc)
        for page_idx, page in enumerate(doc):
            if on_page is not None:
                on_page(page_idx, total)

            page_h = page.rect.height if isinstance(page.rect.height, (int, float)) else 0.0
            raw_width = getattr(page.rect, "width", 0.0)
            page_w = raw_width if isinstance(raw_width, (int, float)) else 0.0
            try:
                tables = page.find_tables()
                table_items = getattr(tables, "tables", None)
                if isinstance(table_items, (list, tuple)):
                    page_table_boxes = [
                        tuple(getattr(table, "bbox"))
                        for table in table_items
                        if getattr(table, "bbox", None) is not None
                    ]
                else:
                    page_table_boxes = []
            except Exception:
                page_table_boxes = []
            if page_table_boxes:
                table_boxes[page_idx] = page_table_boxes

            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
            for block_id, block in enumerate(blocks):
                if block.get("type") != 0:
                    continue
                for line_id, line in enumerate(block.get("lines", [])):
                    raw_spans = line.get("spans", [])
                    max_size = max(
                        (sp.get("size", 0) for sp in raw_spans if sp.get("text")),
                        default=0,
                    )
                    sup_threshold = max_size * 0.75

                    text_parts: list[str] = []
                    segment_texts: list[str] = []
                    all_sizes: list[float] = []
                    bold_chars = 0
                    italic_chars = 0
                    total_chars = 0

                    for span in raw_spans:
                        text = span.get("text", "")
                        if not text:
                            continue
                        if span.get("size", 0) < sup_threshold:
                            continue
                        text_parts.append(text)
                        if text.strip():
                            segment_texts.append(text.strip())
                        n = len(text)
                        flags = span.get("flags", 0)
                        all_sizes.extend([span.get("size", 0)] * n)
                        if flags & 16:
                            bold_chars += n
                        if flags & 2:
                            italic_chars += n
                        total_chars += n

                    text = "".join(text_parts).strip()
                    if not text or not all_sizes:
                        continue

                    all_sizes.sort()
                    median_size = all_sizes[len(all_sizes) // 2]
                    x0, y0, x1, y1 = line["bbox"]
                    lines.append(
                        RawLine(
                            page=page_idx,
                            text=text,
                            bbox=(x0, y0, x1, y1),
                            top=y0,
                            bottom=y1,
                            left=x0,
                            right=x1,
                            page_width=page_w or block.get("bbox", (0.0, 0.0, 0.0, 0.0))[2] or x1,
                            page_height=page_h,
                            size=median_size,
                            bold=bold_chars > total_chars / 2,
                            italic=italic_chars > total_chars / 2,
                            block_id=block_id,
                            line_id=line_id,
                            segment_texts=segment_texts,
                        )
                    )

    lines.sort(key=lambda line: (line.page, line.top, line.left))
    return lines, table_boxes


def extract_raw_lines(pdf_path: str, on_page=None) -> list[RawLine]:
    """Extract normalized raw lines from *pdf_path*."""
    return _read_pdf_artifacts(pdf_path, on_page=on_page)[0]


def _detect_toc_pages(lines: list[RawLine]) -> set[int]:
    helpers = _detector_helpers()
    pages: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        pages.setdefault(line.page, []).append(_line_to_span(line))

    toc_pages = {page for page, page_lines in pages.items() if helpers._is_toc_page(page_lines)}
    if not toc_pages:
        return set()

    changed = True
    while changed:
        changed = False
        for page, page_lines in pages.items():
            if page in toc_pages:
                continue
            if (page - 1) not in toc_pages and (page + 1) not in toc_pages:
                continue
            if helpers._toc_likeness(
                page_lines
            ) >= helpers._TOC_NEIGHBOR_THRESHOLD or helpers._is_toc_continuation_page(page_lines):
                toc_pages.add(page)
                changed = True
    return toc_pages


def _detect_running_texts(lines: list[RawLine]) -> set[str]:
    pages_by_text: dict[str, set[int]] = {}
    for line in lines:
        if line.page_height <= 0:
            continue
        top_frac = line.top / line.page_height
        bottom_frac = line.bottom / line.page_height
        if top_frac > 0.08 and bottom_frac < 0.92:
            continue
        normalized = _normalize_running_text(line.text)
        if not normalized:
            continue
        pages_by_text.setdefault(normalized, set()).add(line.page)

    return {text for text, pages in pages_by_text.items() if len(pages) >= 3}


def _estimate_body_cluster(
    lines: list[RawLine], toc_pages: set[int], running_texts: set[str]
) -> dict[str, Any]:
    counts: Counter[tuple[float, bool, bool]] = Counter()
    for line in lines:
        if line.page in toc_pages:
            continue
        normalized = _normalize_running_text(line.text)
        if normalized in running_texts:
            continue
        width = line.right - line.left
        if line.page_width > 0 and width < line.page_width * 0.12:
            continue
        if _is_centered(line.left, line.right, line.page_width, line.size):
            continue
        counts[(_rounded_size(line.size), line.bold, line.italic)] += len(line.text)

    if not counts:
        return {"size": 11.0, "bold": False, "italic": False}

    size, bold, italic = counts.most_common(1)[0][0]
    return {"size": size, "bold": bold, "italic": italic}


def _column_band(left: float, body_size: float) -> int:
    width = max(body_size * 4.0, 36.0)
    return round(left / width)


def _is_centered(left: float, right: float, page_width: float, size: float) -> bool:
    width = right - left
    if page_width <= 0 or width <= 0:
        return False
    line_center = (left + right) / 2
    page_center = page_width / 2
    return width < page_width * 0.85 and abs(line_center - page_center) <= max(
        page_width * 0.08, size * 4.0
    )


def _line_kind(line: RawLine, body_size: float) -> str:
    helpers = _detector_helpers()
    text = line.text.strip()
    if not text:
        return "empty"
    if _CAPTION_RE.match(text):
        return "caption"
    if helpers._is_likely_equation_line(text) or helpers._is_math_span(text):
        return "math"
    if _PROMPT_RE.match(text):
        return "prompt"
    if helpers._is_numeric_table_row(text):
        return "table"
    if helpers._is_sparse_table_row(_line_to_span(line), body_size):
        return "table"
    return "normal"


def _ends_heading_like_unit(text: str, helpers) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[-1] in ".:;!?":
        return True
    if helpers._extract_numbered_depth(stripped) is not None:
        return True
    return bool(helpers._CHAPTER_ANCHOR_RE.match(stripped))


def _should_merge_line(
    block_lines: list[RawLine],
    next_line: RawLine,
    body_size: float,
    current_column: int,
    next_column: int,
) -> bool:
    helpers = _detector_helpers()
    prev = block_lines[-1]
    dominant_size = _rounded_size(sum(line.size for line in block_lines) / len(block_lines))
    if prev.page != next_line.page:
        return False
    if prev.block_id != next_line.block_id:
        return False
    if current_column != next_column:
        return False
    if abs(prev.left - next_line.left) > 12.0:
        return False
    if abs(next_line.size - dominant_size) > 0.5:
        return False
    if prev.bold != next_line.bold or prev.italic != next_line.italic:
        return False
    if _is_centered(prev.left, prev.right, prev.page_width, prev.size) != _is_centered(
        next_line.left, next_line.right, next_line.page_width, next_line.size
    ):
        return False
    gap = next_line.top - prev.top
    if gap <= 0 or gap > max(dominant_size, body_size) * 1.8:
        return False
    if _ends_heading_like_unit(prev.text, helpers):
        return False
    if _line_kind(prev, body_size) != _line_kind(next_line, body_size):
        return False
    return True


def _make_block(lines: list[RawLine], body_size: float) -> DocumentBlock:
    text = " ".join(line.text.strip() for line in lines if line.text.strip())
    left = min(line.left for line in lines)
    top = min(line.top for line in lines)
    right = max(line.right for line in lines)
    bottom = max(line.bottom for line in lines)
    size_counter: Counter[float] = Counter()
    bold_chars = 0
    italic_chars = 0
    total_chars = 0
    segment_texts: list[str] = []
    for line in lines:
        size_counter[_rounded_size(line.size)] += len(line.text)
        segment_texts.extend(line.segment_texts)
        total_chars += len(line.text)
        if line.bold:
            bold_chars += len(line.text)
        if line.italic:
            italic_chars += len(line.text)
    dominant_size = size_counter.most_common(1)[0][0]
    bold = bold_chars > total_chars / 2 if total_chars else False
    italic = italic_chars > total_chars / 2 if total_chars else False
    page_width = lines[0].page_width
    centered = _is_centered(left, right, page_width, dominant_size)
    features = {
        "line_count": len(lines),
        "word_count": len(_WORD_RE.findall(text)),
        "text_length": len(text),
        "segment_texts": segment_texts,
        "width_ratio": (right - left) / page_width if page_width else 1.0,
        "column_band": _column_band(left, body_size),
    }
    return DocumentBlock(
        page=lines[0].page,
        bbox=(left, top, right, bottom),
        text=text,
        lines=list(lines),
        dominant_size=dominant_size,
        bold=bold,
        italic=italic,
        centered=centered,
        indent=left,
        label=BlockLabel.NOISE,
        confidence=0.0,
        features=features,
    )


def _merge_wrapped_heading_blocks(
    blocks: list[DocumentBlock], body_size: float
) -> list[DocumentBlock]:
    helpers = _detector_helpers()
    if not blocks:
        return blocks

    merged: list[DocumentBlock] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        parts = [block]
        while i + 1 < len(blocks):
            nxt = blocks[i + 1]
            prev = parts[-1]
            prev_text = prev.text.strip()
            gap = nxt.bbox[1] - prev.bbox[1]
            if (
                prev.page == nxt.page
                and prev.features.get("column_band") == nxt.features.get("column_band")
                and abs(prev.dominant_size - nxt.dominant_size) <= 0.5
                and gap <= max(prev.dominant_size, body_size) * 1.8
                and abs(prev.indent - nxt.indent) <= 12.0
                and prev_text
                and prev_text[-1] not in ".:;,?!"
                and (
                    prev.bold
                    or prev.italic
                    or prev.dominant_size > body_size * 1.05
                    or helpers._extract_numbered_depth(prev_text) is not None
                    or helpers._CHAPTER_ANCHOR_RE.match(prev_text)
                )
            ):
                # keep heading-like wraps together but avoid merging obvious prose
                if helpers._is_sentence_like_text(prev_text) or helpers._is_sentence_like_text(
                    nxt.text
                ):
                    break
                parts.append(nxt)
                i += 1
                continue
            break

        if len(parts) == 1:
            merged.append(block)
            i += 1
            continue

        lines: list[RawLine] = []
        for part in parts:
            lines.extend(part.lines)
        merged.append(_make_block(lines, body_size))
        i += 1

    return merged


def _build_blocks(lines: list[RawLine], body_size: float) -> list[DocumentBlock]:
    if not lines:
        return []

    blocks: list[DocumentBlock] = []
    current: list[RawLine] = [lines[0]]
    current_column = _column_band(lines[0].left, body_size)

    for line in lines[1:]:
        next_column = _column_band(line.left, body_size)
        if _should_merge_line(current, line, body_size, current_column, next_column):
            current.append(line)
            continue
        blocks.append(_make_block(current, body_size))
        current = [line]
        current_column = next_column

    blocks.append(_make_block(current, body_size))
    blocks = _merge_wrapped_heading_blocks(blocks, body_size)
    blocks.sort(key=lambda block: (block.page, block.bbox[1], block.bbox[0]))
    return blocks


def _bands_from_table_boxes(
    table_boxes: dict[int, list[tuple[float, float, float, float]]],
) -> dict[int, list[tuple[float, float]]]:
    bands: dict[int, list[tuple[float, float]]] = {}
    for page, boxes in table_boxes.items():
        page_bands = [(y0 - 8.0, y1 + 8.0) for _, y0, _, y1 in boxes]
        bands[page] = _merge_vertical_bands(page_bands, gap=8.0)
    return bands


def _combine_table_bands(
    lines: list[RawLine],
    table_boxes: dict[int, list[tuple[float, float, float, float]]],
    body_size: float,
) -> dict[int, list[tuple[float, float]]]:
    helpers = _detector_helpers()
    span_bands = helpers._infer_table_bands([_line_to_span(line) for line in lines], body_size)
    box_bands = _bands_from_table_boxes(table_boxes)
    combined: dict[int, list[tuple[float, float]]] = {}
    all_pages = set(span_bands) | set(box_bands)
    for page in all_pages:
        bands = list(span_bands.get(page, [])) + list(box_bands.get(page, []))
        combined[page] = _merge_vertical_bands(bands, gap=body_size * 0.8)
    return combined


def _in_band(block: DocumentBlock, bands: dict[int, list[tuple[float, float]]]) -> bool:
    top = block.bbox[1]
    bottom = block.bbox[3]
    for band_top, band_bottom in bands.get(block.page, []):
        overlap = min(bottom, band_bottom) - max(top, band_top)
        if overlap > 0:
            return True
    return False


def _display_math_score(
    block: DocumentBlock,
    prev_block: DocumentBlock | None,
    next_block: DocumentBlock | None,
) -> int:
    helpers = _detector_helpers()
    text = block.text.strip()
    if not text:
        return 0

    words = _WORD_RE.findall(text)
    alpha_chars = sum(ch.isalpha() for ch in text)
    non_space = sum(1 for ch in text if not ch.isspace()) or 1
    op_chars = len(helpers._EQUATION_OP_RE.findall(text))
    symbol_chars = sum(ch in "()[]{}|/\\_^=+-*<>" for ch in text)
    lexical_ratio = len("".join(words)) / non_space if non_space else 0.0
    score = 0

    if helpers._is_likely_equation_line(text) or helpers._is_math_span(text):
        score += 1
    if (op_chars + symbol_chars) / non_space >= 0.18 or lexical_ratio <= 0.45:
        score += 1
    width_ratio = block.features.get("width_ratio", 1.0)
    if block.centered or width_ratio <= 0.55:
        score += 1
    if (
        prev_block is not None
        and prev_block.page == block.page
        and helpers._is_likely_equation_line(prev_block.text)
    ):
        score += 1
    if (
        next_block is not None
        and next_block.page == block.page
        and helpers._is_likely_equation_line(next_block.text)
    ):
        score += 1
    if re.search(r"\b(?:lim|sum|prod|int|d/dx|dx|dy|lambda|beta|sigma|matrix)\b", text, re.I):
        score += 1
    if alpha_chars <= 6 and op_chars >= 1:
        score += 1
    return score


def _is_problem_prompt(
    block: DocumentBlock, next_blocks: list[DocumentBlock], body_size: float
) -> bool:
    text = block.text.strip()
    if not _PROMPT_RE.match(text):
        return False
    question_like = "?" in text or len(text.split()) >= 6
    near_solution = any(
        nxt.page == block.page and nxt.text.strip().lower().startswith("solution:")
        for nxt in next_blocks[:3]
    )
    body_sized = abs(block.dominant_size - body_size) <= 1.0
    return body_sized and (question_like or near_solution)


def _is_body_paragraph(
    block: DocumentBlock,
    prev_block: DocumentBlock | None,
    next_block: DocumentBlock | None,
    body_cluster: dict[str, Any],
) -> bool:
    helpers = _detector_helpers()
    text = block.text.strip()
    if not text:
        return False
    body_key = (_rounded_size(block.dominant_size), block.bold, block.italic)
    if body_key != (body_cluster["size"], body_cluster["bold"], body_cluster["italic"]):
        return False
    if block.centered:
        return False
    if len(text) >= 60 or helpers._is_sentence_like_text(text):
        return True
    if text[0].islower() or text.endswith((".", ",", ";", ":")):
        return True
    for neighbor in (prev_block, next_block):
        if neighbor is None or neighbor.page != block.page:
            continue
        if abs(neighbor.dominant_size - block.dominant_size) > 0.5:
            continue
        if abs(neighbor.indent - block.indent) > 12.0:
            continue
        if helpers._is_sentence_like_text(neighbor.text) or len(neighbor.text) >= 60:
            return True
    return False


def _is_heading_candidate(
    block: DocumentBlock,
    prev_block: DocumentBlock | None,
    next_block: DocumentBlock | None,
    next_blocks: list[DocumentBlock],
    body_size: float,
    size_threshold_ratio: float,
) -> bool:
    helpers = _detector_helpers()
    text = block.text.strip()
    if not text or helpers._is_noise(text):
        return False

    threshold = body_size * size_threshold_ratio
    numeric_depth = (
        None if helpers._is_numeric_table_row(text) else helpers._extract_numbered_depth(text)
    )
    chapter_anchor = bool(helpers._CHAPTER_ANCHOR_RE.match(text))
    strong_signal = bool(
        chapter_anchor
        or numeric_depth is not None
        or block.dominant_size > threshold
        or ((block.bold or block.italic) and block.dominant_size >= body_size * 0.99)
    )
    if not strong_signal:
        return False

    cur_span = _block_to_span(block)
    prev_span = _block_to_span(prev_block) if prev_block is not None else None
    next_span = _block_to_span(next_block) if next_block is not None else None
    next2_span = _block_to_span(next_blocks[1]) if len(next_blocks) > 1 else None

    if numeric_depth is None and helpers._is_likely_diagram_label(
        text, block.dominant_size, body_size
    ):
        return False
    if numeric_depth is None and helpers._is_likely_equation_line(text):
        return False
    if helpers._is_body_starter_with_continuation(cur_span, next_span, next2_span):
        return False
    if helpers._is_numeric_continuation_line(cur_span, prev_span):
        return False
    if helpers._is_colon_math_introducer(cur_span, next_span, next2_span):
        return False
    if (
        not block.bold
        and not block.italic
        and helpers._is_likely_paragraph_lead(cur_span, next_span, body_size)
    ):
        return False
    if (
        (block.bold or block.italic)
        and prev_span is not None
        and next_span is not None
        and helpers._is_plain_neighbor_line(cur_span, prev_span)
        and helpers._is_plain_neighbor_line(cur_span, next_span)
        and next_block is not None
        and next_block.page == block.page
        and next_block.text
        and next_block.text[0].islower()
    ):
        return False
    if text[0].islower():
        return False
    if text.endswith("."):
        return False
    if not block.bold and not block.italic and numeric_depth is None and not chapter_anchor:
        if not helpers._is_likely_plain_heading_text(text):
            return False
        if helpers._is_sentence_like_text(text) and block.dominant_size <= body_size * 1.35:
            return False
        if next_block is not None and next_block.page == block.page:
            gap_ok = 0 < (next_block.bbox[1] - block.bbox[1]) <= block.dominant_size * 2.8
            if gap_ok:
                next_text = next_block.text
                if text.endswith(","):
                    return False
                if (
                    helpers._is_sentence_like_text(text)
                    and next_text
                    and (next_text[0].islower() or len(next_text) <= 24)
                ):
                    return False

    if next_block is not None and next_block.page == block.page:
        gap = next_block.bbox[1] - block.bbox[1]
        if gap <= 0:
            return False
        if (
            gap <= max(block.dominant_size, body_size) * 1.8
            and next_block.label == BlockLabel.HEADING_CANDIDATE
        ):
            return False

    return True


def _label_blocks(
    blocks: list[DocumentBlock],
    *,
    body_cluster: dict[str, Any],
    toc_pages: set[int],
    running_texts: set[str],
    table_bands: dict[int, list[tuple[float, float]]],
    body_size: float,
    size_threshold_ratio: float,
    header_margin: float,
) -> None:
    helpers = _detector_helpers()

    for idx, block in enumerate(blocks):
        prev_block = blocks[idx - 1] if idx > 0 else None
        next_blocks = [other for other in blocks[idx + 1 :] if other.page == block.page][:3]
        next_block = next_blocks[0] if next_blocks else None
        layout_boxclass = str(block.features.get("layout_boxclass") or "").lower()

        block.features.update(
            {
                "numeric_depth": None
                if helpers._is_numeric_table_row(block.text)
                else helpers._extract_numbered_depth(block.text),
                "chapter_anchor": bool(helpers._CHAPTER_ANCHOR_RE.match(block.text.strip())),
                "math_score": _display_math_score(block, prev_block, next_block),
            }
        )

        page_height = (
            block.lines[0].page_height
            if block.lines
            else float(block.features.get("page_height") or 0.0)
        )
        top_frac = block.bbox[1] / page_height if page_height else 0.0
        bottom_frac = block.bbox[3] / page_height if page_height else 0.0
        normalized_text = _normalize_running_text(block.text)

        if block.page in toc_pages:
            block.label = BlockLabel.TOC_ENTRY
            block.confidence = 0.95
            continue
        if layout_boxclass in {"page-header", "page-footer"}:
            block.label = BlockLabel.RUNNING_HEADER_FOOTER
            block.confidence = 0.98
            continue
        if (
            normalized_text in running_texts and (top_frac <= 0.08 or bottom_frac >= 0.92)
        ) or helpers._in_margin(_block_to_span(block), header_margin):
            block.label = BlockLabel.RUNNING_HEADER_FOOTER
            block.confidence = 0.95
            continue
        if (
            layout_boxclass == "table"
            or block.features.get("layout_table")
            or _in_band(block, table_bands)
        ):
            block.label = BlockLabel.TABLE_REGION
            block.confidence = 0.95
            continue
        if layout_boxclass == "caption" or _CAPTION_RE.match(block.text.strip()):
            block.label = BlockLabel.CAPTION
            block.confidence = 0.9
            continue
        if layout_boxclass == "picture":
            if block.features["math_score"] >= 1:
                block.label = BlockLabel.DISPLAY_MATH
                block.confidence = 0.9
            else:
                block.label = BlockLabel.NOISE
                block.confidence = 0.75
            continue
        if block.features["math_score"] >= 2:
            block.label = BlockLabel.DISPLAY_MATH
            block.confidence = 0.8
            continue
        if _is_problem_prompt(block, next_blocks, body_size):
            block.label = BlockLabel.PROBLEM_PROMPT
            block.confidence = 0.85
            continue
        if layout_boxclass in {"title", "section-header"} and not helpers._is_noise(block.text):
            block.label = BlockLabel.HEADING_CANDIDATE
            block.confidence = 0.96
            continue
        if _is_body_paragraph(block, prev_block, next_block, body_cluster):
            block.label = BlockLabel.BODY_PARAGRAPH
            block.confidence = 0.75
            continue
        if _is_heading_candidate(
            block, prev_block, next_block, next_blocks, body_size, size_threshold_ratio
        ):
            block.label = BlockLabel.HEADING_CANDIDATE
            block.confidence = 0.7
            continue
        block.label = BlockLabel.NOISE
        block.confidence = 0.5


def analyze_layout(
    pdf_path: str,
    *,
    size_threshold_ratio: float = 1.05,
    on_page=None,
    skip_pages: int = 0,
    skip_toc: bool = True,
    header_margin: float = 0.0,
) -> DocumentLayout:
    """Analyze document layout and label structural blocks."""
    lines, table_boxes = _read_pdf_artifacts(pdf_path, on_page=on_page)
    if skip_pages > 0:
        lines = [line for line in lines if line.page >= skip_pages]
        table_boxes = {page: boxes for page, boxes in table_boxes.items() if page >= skip_pages}
    if not lines:
        return DocumentLayout(
            lines=[],
            blocks=[],
            body_cluster={"size": 11.0, "bold": False, "italic": False},
            toc_pages=set(),
        )

    toc_pages = _detect_toc_pages(lines) if skip_toc else set()
    running_texts = _detect_running_texts(lines)
    body_cluster = _estimate_body_cluster(lines, toc_pages, running_texts)
    body_size = body_cluster["size"]
    blocks = _build_blocks(lines, body_size)
    table_bands = _combine_table_bands(lines, table_boxes, body_size)
    _label_blocks(
        blocks,
        body_cluster=body_cluster,
        toc_pages=toc_pages,
        running_texts=running_texts,
        table_bands=table_bands,
        body_size=body_size,
        size_threshold_ratio=size_threshold_ratio,
        header_margin=header_margin,
    )
    return DocumentLayout(
        lines=lines,
        blocks=blocks,
        body_cluster=body_cluster,
        toc_pages=toc_pages,
    )
