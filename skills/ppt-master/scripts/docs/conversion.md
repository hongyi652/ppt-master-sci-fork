# Conversion Tools

> Architecture rationale (why MinerU is the only PDF path, why pandoc remains a fallback for some document formats, and why curl_cffi is used for TLS impersonation): see [docs/technical-design.md "Source Content Conversion"](../../../../docs/technical-design.md#source-content-conversion).

Source conversion tools are low-level building blocks behind `project_manager.py import-sources`. In the normal PPT workflow, create the project first and import sources so every derived Markdown file, `_files/` directory, zip, and report stays inside the project tree instead of beside the original source document.

## PDF parsing via `source_to_md/mineru_to_md.py`

PPT Master now uses MinerU as the only supported PDF parser. The older `source_to_md/pdf_to_md.py` command remains only as a compatibility shim and exits with an error telling you to use MinerU.

MinerU normalizes its result zip into the project convention used throughout the repo: `<output>.md` plus a sibling `<output>_files/` directory. Extracted images are referenced from Markdown and `image_manifest.json` is written for project import.

Preferred workflow:
- `python3 scripts/project_manager.py import-sources <project_path> <file.pdf>`
- Standalone `convert_pdf.py` / `mineru_to_md.py` is only for exceptional repair flows, and must use `-o <project_path>/sources/<name>.md`

```bash
python3 scripts/source_to_md/mineru_to_md.py paper.pdf -o projects/demo/sources/paper.md
python3 scripts/source_to_md/mineru_to_md.py paper.pdf -o projects/demo/sources/paper.md --is-ocr
python3 scripts/source_to_md/mineru_to_md.py mineru_result.zip --from-zip -o projects/demo/sources/paper.md
python3 scripts/project_manager.py import-sources projects/demo paper.pdf --move
```

Configuration:

```bash
MINERU_API_TOKEN=<token>
# Optional:
MINERU_API_BASE_URL=https://mineru.net/api/v4
```

## `source_to_md/doc_to_md.py`

Hybrid converter: pure-Python for the common formats, pandoc fallback for the rest.

Native path (no external binary required):
- `.docx` — via `mammoth`
- `.html` / `.htm` — via `markdownify` + `beautifulsoup4`
- `.epub` — via `ebooklib` + `markdownify`
- `.ipynb` — via `nbconvert`

Pandoc fallback (only if you need these):
- `.doc`, `.odt`, `.rtf`, `.tex`/`.latex`, `.rst`, `.org`, `.typ`

```bash
python3 scripts/source_to_md/doc_to_md.py lecture.docx
python3 scripts/source_to_md/doc_to_md.py lecture.docx -o output.md
python3 scripts/source_to_md/doc_to_md.py notes.epub
python3 scripts/source_to_md/doc_to_md.py paper.tex -o paper.md  # uses pandoc
```

Dependencies:

```bash
# Native path — always required
pip install mammoth markdownify ebooklib nbconvert beautifulsoup4

# Fallback path — only for .doc/.odt/.rtf/.tex/.rst/.org/.typ
# macOS:   brew install pandoc
# Ubuntu:  sudo apt install pandoc
# Windows: https://pandoc.org/installing.html
```

When you run a low-level converter manually, always pass `-o` inside the target project's `sources/` directory. The normalized result is `<output>.md` plus a sibling `<output>_files/` directory containing extracted images with relative references.

## `source_to_md/excel_to_md.py`

Excel workbook converter for presentation source intake.

Supported formats:
- `.xlsx`
- `.xlsm`

Unsupported by default:
- `.xls` — resave as `.xlsx` first

```bash
python3 scripts/source_to_md/excel_to_md.py report.xlsx
python3 scripts/source_to_md/excel_to_md.py report.xlsx -o output.md
python3 scripts/source_to_md/excel_to_md.py report.xlsm --max-rows 200 --max-cols 40
```

Behavior:
- preserves workbook and sheet structure in Markdown
- exports visible sheets only
- trims empty outer rows and columns
- propagates merged-cell labels for readable Markdown tables
- exports formula cells as cached values; it does not recalculate formulas

Dependency:

```bash
pip install openpyxl
```

CSV/TSV files are already plain-text table sources and do not require this converter.

## `source_to_md/ppt_to_md.py`

Structured PowerPoint-to-Markdown converter for Open XML slide decks.

Supported formats include:
- `.pptx`, `.pptm`
- `.ppsx`, `.ppsm`
- `.potx`, `.potm`

```bash
python3 scripts/source_to_md/ppt_to_md.py sales_deck.pptx
python3 scripts/source_to_md/ppt_to_md.py sales_deck.pptx -o output.md
python3 scripts/source_to_md/ppt_to_md.py ./decks
python3 scripts/source_to_md/ppt_to_md.py ./decks -o ./markdown
python3 scripts/source_to_md/ppt_to_md.py template.ppsx -o notes/template.md
```

Behavior:
- extracts slide text in reading order
- converts PowerPoint tables to Markdown tables
- exports embedded pictures to a sibling `_files/` directory
- appends speaker notes when present

Dependency:

```bash
pip install python-pptx
```

Legacy `.ppt` is not parsed directly. Resave it as `.pptx` or export it to PDF first.

## `source_to_md/web_to_md.py`

Convert web pages to Markdown and download images locally.

```bash
python3 scripts/source_to_md/web_to_md.py https://example.com/article
python3 scripts/source_to_md/web_to_md.py https://url1.com https://url2.com
python3 scripts/source_to_md/web_to_md.py -f urls.txt
python3 scripts/source_to_md/web_to_md.py https://example.com -o output.md
```

When `curl_cffi` is installed (included in `requirements.txt`), this script
automatically impersonates a modern Chrome TLS fingerprint, which lets it
fetch WeChat Official Accounts (`mp.weixin.qq.com`) and other sites that
block Python's default TLS fingerprint. No extra flags needed. If
`curl_cffi` is not available, it falls back to plain `requests`.


## `rotate_images.py`

Fix image EXIF orientation in downloaded or imported assets.

```bash
python3 scripts/rotate_images.py auto projects/xxx_files
python3 scripts/rotate_images.py gen projects/xxx_files
python3 scripts/rotate_images.py fix fixes.json
```

Use this when extracted photos appear sideways after conversion or import.


## `extract_formulas.py`

Extract LaTeX formulas from MinerU-generated (or any LaTeX-containing) Markdown
files and produce a `formula_manifest.json` for AI review.

```bash
python3 scripts/extract_formulas.py paper.md
python3 scripts/extract_formulas.py projects/demo/sources/paper.md
python3 scripts/extract_formulas.py paper.md -o projects/demo/images/formula_manifest.json
python3 scripts/extract_formulas.py paper.md --display-only --min-length 8
```

Behavior:
- scans for `$...$` (inline), `$$...$$` (display), and `\begin{equation}` etc. environments
- deduplicates identical formulas
- filters trivial single-variable or numeric expressions
- outputs `formula_manifest.json` with each formula's LaTeX code, type, and surrounding context
- all entries default to `"render": false` — the Strategist (AI) reviews and sets `"render": true` for formulas worth including in the presentation

No external dependencies (standard library only).


## `latex_to_svg.py`

Convert LaTeX formula strings to standalone SVG files using the local
TeX distribution and `dvisvgm`.

```bash
# Single formula
python3 scripts/latex_to_svg.py "E=mc^2" -o formula.svg
python3 scripts/latex_to_svg.py "\frac{a}{b}" --inline -o inline.svg

# Manifest batch mode (in-pipeline)
python3 scripts/latex_to_svg.py --manifest projects/demo/images/formula_manifest.json
python3 scripts/latex_to_svg.py --manifest projects/demo/images/formula_manifest.json --force
```

Behavior:
- generates a minimal `.tex` file using the `standalone` document class
- compiles with `latex` (or `xelatex`/`pdflatex` if `latex` is unavailable)
- converts DVI/PDF to SVG via `dvisvgm --no-fonts --exact-bbox`
- embeds non-visual `<title>` / `<desc>` metadata plus `data-formula-*` root
  attributes so each SVG remains traceable to its LaTeX source
- in manifest mode, processes all entries with `"render": true`, writes SVGs to the
  manifest directory as `formula_*.svg`, and updates each entry's status and dimensions

Dependency:

```bash
# MiKTeX (Windows) or TeX Live (Linux/macOS)
# Both provide latex, pdflatex, xelatex, and dvisvgm
```

Pipeline integration:
1. Step 1 (Source Processing): run `extract_formulas.py` after MinerU conversion
2. Step 4 (Strategist): AI reviews `formula_manifest.json`, sets `"render": true`
3. Step 5 (Image Acquisition): run `latex_to_svg.py --manifest` to generate SVGs,
   then `stabilize_image_assets.py` to refresh `notes/formula_asset_table.md`
4. Step 6 (Executor): read `notes/formula_asset_table.md`, reference `SVG href`,
   add `data-formula-id`, and respect short-formula scale guidance
