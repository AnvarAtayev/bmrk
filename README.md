<p align="center">
  <img src="https://raw.githubusercontent.com/AnvarAtayev/bmrk/main/assets/bmrk-logo.png" width="180" />
</p>

<h1 align="center">bmrk</h1>

<p align="center">
  A simple CLI tool for adding structured bookmarks to PDFs.
</p>

<p align="center">
  <a href="https://github.com/AnvarAtayev/bmrk/actions/workflows/ci.yml">
    <img src="https://github.com/AnvarAtayev/bmrk/actions/workflows/ci.yml/badge.svg" />
  </a>
  <a href="https://pypi.org/project/bmrk/">
    <img src="https://img.shields.io/pypi/v/bmrk?v=1" />
  </a>
  <a href="https://pypi.org/project/bmrk/">
    <img src="https://img.shields.io/pypi/pyversions/bmrk?v=1" />
  </a>
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" />
  </a>
</p>

`bmrk` analyses a PDF's text and font metadata to detect its heading structure, then writes a bookmarked copy for easier navigation in any PDF viewer.

---

### Table of Contents
- [Installation](#installation)
  - [From source](#from-source)
  - [With OCR support](#with-ocr-support)
    - [OCR in a dev environment](#ocr-in-a-dev-environment)
- [Usage](#usage)
  - [Basic](#basic)
  - [Options](#options)
  - [Inspect before writing](#inspect-before-writing)
  - [Inspect block inference](#inspect-block-inference)
  - [Manual heading adjustments](#manual-heading-adjustments)
  - [Tune for a noisy PDF](#tune-for-a-noisy-pdf)
  - [Handle a cover page](#handle-a-cover-page)
- [How it works](#how-it-works)
- [Code structure](#code-structure)
- [Limitations](#limitations)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---
## Installation

```bash
pip install bmrk
```

For an isolated install that keeps `bmrk` available globally without polluting your Python environment:

```bash
# pipx
pipx install bmrk

# uv
uv tool install bmrk
```

To run `bmrk` once without installing it:

```bash
# pipx
pipx run bmrk paper.pdf paper_bookmarked.pdf

# uvx (uv's ephemeral tool runner)
uvx bmrk paper.pdf paper_bookmarked.pdf
```

### From source

```bash
pip install git+https://github.com/AnvarAtayev/bmrk.git
```

### With OCR support

For scanned PDFs that lack a text layer, install the optional OCR extra:

```bash
pip install "bmrk[ocr]"
# or
pipx install "bmrk[ocr]"
# or
uv tool install "bmrk[ocr]"
```

This pulls in [ocrmypdf](https://ocrmypdf.readthedocs.io/), which itself requires **Tesseract** and **Ghostscript** to be installed on your system:

```bash
# macOS
brew install tesseract ghostscript

# Debian/Ubuntu
sudo apt install tesseract-ocr ghostscript

# Windows -- download installers from:
#   https://github.com/UB-Mannheim/tesseract/wiki
#   https://www.ghostscript.com/releases/gsdnld.html
```

Then pass `--ocr` to `bmrk`:

```bash
bmrk scanned.pdf scanned_bookmarked.pdf --ocr
```

#### OCR in a dev environment

```bash
# 1. Clone the repo and sync all extras
git clone https://github.com/AnvarAtayev/bmrk.git
cd bmrk
uv sync --extra dev --extra ocr

# 2. Install system deps (macOS example)
brew install tesseract ghostscript

# 3. Run
uv run bmrk scanned.pdf out.pdf --ocr
```

## Usage

```
bmrk [OPTIONS] <INPUT>.pdf [<OUTPUT>.pdf]
```

### Basic

```bash
bmrk paper.pdf paper_bookmarked.pdf
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--source SOURCE` | `auto` | Where headings come from: `auto` uses the PDF's own bookmark outline when it has one and falls back to font analysis; `outline` requires an existing outline; `font` ignores it and always analyses fonts. |
| `--threshold RATIO` / `-t` | `1.05` | Font-size ratio above which text is treated as a heading. Raise to `1.15` for noisy PDFs; lower to `1.01` to catch bold same-size section titles. |
| `--verbose` / `-v` | off | Print detected headings and progress info. |
| `--dry-run` / `-n` | off | Detect and print headings only; do not write an output file. Useful for tuning `--threshold`. |
| `--ocr` | off | Run OCR before detection. Requires `bmrk[ocr]`. |
| `--export-headings FILE` | -- | Write detected heading structure to FILE (TSV). Edit and feed back in with `--import-headings`. |
| `--import-headings FILE` | -- | Use headings from FILE instead of running detection. Enables manual adjustments. |
| `--export-blocks FILE` | -- | Write the intermediate block-labeling stage to FILE as JSON Lines. |
| `--blocks-only` | off | Stop after layout/block analysis. Do not detect headings or write a PDF. |
| `--cover-pages N` | `0` | Skip the first N pages when detecting headings (e.g. cover page). |
| `--max-depth N` / `-d` | `3` | Maximum heading depth to include (1 = chapters only, 2 = + sections, 3 = + subsections). |

### Redo bookmarks a PDF already has

If a PDF ships with an outline, `bmrk` keeps it and tells you so:

```bash
bmrk book.pdf out.pdf
# bmrk: out.pdf (27 headings from existing outline)
#       Pass --source font to ignore it and rebuild the headings.
```

When those bookmarks are wrong, rebuild them from the page layout instead:

```bash
bmrk book.pdf out.pdf --source font
```

`--threshold`, `--max-depth` and `--cover-pages` only affect font analysis, so
they do nothing until you pass `--source font`.

### Inspect before writing

```bash
bmrk paper.pdf --dry-run --verbose
```

### Inspect block inference

To inspect the middle stage directly:

```bash
bmrk paper.pdf --blocks-only
```

To export every inferred block as JSON Lines:

```bash
bmrk paper.pdf --export-blocks blocks.jsonl --blocks-only
```

Each JSON line includes:

- page index and page number
- bounding box
- final `label`
- confidence
- dominant size and style flags
- the extracted block text
- block features used for later heading inference

This is the best way to debug why a line was treated as a heading, body paragraph, caption, table region, math block, or prompt.

### Manual heading adjustments

If the auto-detected bookmarks are not quite right, you can export the heading structure, edit it by hand, and import the corrected version back in.

**Step 1 -- Export the detected headings**

```bash
bmrk paper.pdf --export-headings headings.tsv
```

When OUTPUT is omitted, `bmrk` runs detection and exports the heading list without writing a PDF.

**Step 2 -- Edit the TSV file**

Open `headings.tsv` in any text editor or spreadsheet app. The format is tab-separated with three columns:

```
# bmrk heading export
# level	page	title
1	1	Introduction
2	3	Background
2	7	Methods
1	12	Results
3	14	Statistical Analysis
```

- **level** -- heading depth (1 = top-level chapter, 2 = section, 3 = subsection, ...).
- **page** -- 1-based page number where the heading appears.
- **title** -- the bookmark text shown in the PDF viewer.
- Lines starting with `#` are comments and are ignored on import.

Common edits:

- **Remove a heading** -- delete the line entirely.
- **Add a missing heading** -- insert a new line with the correct level, page, and title.
- **Fix a title** -- change the text in the third column.
- **Change nesting** -- adjust the level number (e.g. change `2` to `1` to promote a section to a chapter).
- **Reorder headings** -- rearrange lines; bookmarks are inserted in the order they appear in the file.

**Step 3 -- Import and produce the bookmarked PDF**

```bash
bmrk paper.pdf paper_bookmarked.pdf --import-headings headings.tsv
```

This skips detection entirely and uses your edited headings to write the bookmarked PDF.

### Tune for a noisy PDF

```bash
# More conservative -- only large headings
bmrk paper.pdf out.pdf --threshold 1.15

# More aggressive -- catches bold same-size section titles
bmrk paper.pdf out.pdf --threshold 1.01
```

### Handle a cover page

```bash
# Skip page 1 (the cover) when detecting headings
bmrk report.pdf report_bookmarked.pdf --cover-pages 1
```

## How it works

`bmrk` does not detect headings from isolated lines anymore. It first builds a page layout, labels document blocks, and only then infers bookmark entries from blocks that survive the structural filters.

### Detection pipeline

1. **Extract document structure**
   - raw text lines, font sizes, positions, style flags, and detected table geometry

2. **Build and label blocks**
   - merge related lines into blocks
   - label blocks as body paragraphs, table regions, display math, captions, problem prompts, TOC entries, running headers/footers, or heading candidates

3. **Infer heading levels**
   - numbered prefixes such as `2.1`
   - font-size/style ranking
   - chapter/part anchors
   - local context

5. **Post-process headings**
   - remove duplicates
   - merge labels like `Chapter 1` + `Title`
   - enforce `--max-depth`

6. **Write bookmarks**
   - the final `HeadingEntry` list is written into the PDF outline

The new middle stage is visible directly through `--blocks-only` and `--export-blocks`. That lets you inspect what `bmrk` thinks each page region is before heading-level inference happens.

### Main functions

The key functions are:

- `src/bmrk/cli.py`
  - `main(...)`: parses CLI options, runs detection/import, and writes bookmarks
- `src/bmrk/layout.py`
  - `analyze_layout(...)`: line extraction, block construction, and labeling
  - `_read_pdf_artifacts(...)`: native line and table extraction
  - `_label_blocks(...)`: assigns structural labels to blocks
- `src/bmrk/detector.py`
  - `detect_headings(...)`: converts labeled blocks into final `HeadingEntry` values
  - `_build_heading_entries(...)`: assigns heading levels and merges chapter labels
- `src/bmrk/bookmarker.py`
  - `write_bookmarks(...)`: writes the final outline into the output PDF

```mermaid
flowchart LR
    A[PDF] --> C[Extract text lines]
    C --> D[Build document blocks]
    D --> E[Label blocks]
    E --> F[Infer heading levels]
    F --> G[Post-process headings]
    G --> H[Write bookmarks]

    E -.- E1["table / math / caption / prompt
    body / TOC / running header
    heading candidate"]
    F -.- F1["numbering depth
    font-size/style rank
    chapter anchors"]
    G -.- G1["deduplicate
    merge Chapter + Title
    apply max depth"]
```

## Code structure

```
src/bmrk/
├── cli.py        # Typer CLI entry point
├── layout.py     # Line extraction, block construction, and block labeling
├── detector.py   # Heading inference and HeadingEntry dataclass
├── bookmarker.py # PDF bookmark writing
```

## Limitations

- **Scanned/image PDFs** -- `bmrk` cannot detect headings in PDFs without selectable text. Run OCR first with `bmrk --ocr` (requires `bmrk[ocr]`).
- **Existing bookmarks** -- `bmrk` replaces any existing outline; it does not merge with pre-existing bookmarks.

## Development

```bash
uv sync --extra dev

# Lint
uv run ruff check src/

# Test
uv run pytest
```

## Contributing

Contributions are welcome. Bug reports, feature requests, and pull requests can all be submitted via [GitHub Issues](https://github.com/AnvarAtayev/bmrk/issues) or as a pull request against `main`.

Before opening a pull request, run the lint and test suite to confirm nothing is broken:

```bash
uv sync --extra dev
uv run ruff check src/
uv run pytest
```

## License

MIT
