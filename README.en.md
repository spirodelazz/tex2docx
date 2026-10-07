# tex2docx

[简体中文](README.md) | English

One-command conversion of a LaTeX manuscript (IEEE style and similar) to a
Word (.docx) document, keeping tables, equations, figures, algorithms and
citations intact.

## About

Turning a LaTeX manuscript into the Word copy that many journals require
means re-typesetting every equation, re-inserting every figure and
re-numbering every float by hand. This project wraps the whole job into a
**single command**: pandoc does the heavy lifting of LaTeX -> Word, and a
panflute-based AST filter adds what pandoc cannot do on its own — figure and
table numbering with cross-reference resolution, PDF figure rasterization,
and the IEEE-specific structures (title block, abstract, keywords, author
biographies).

Built out of a real submission need and validated end-to-end against a
complete IEEE manuscript (24 numbered equations, 19 figures, 7 tables,
5 algorithms, 3 author biographies) with zero warnings.

## Requirements

- [pandoc](https://pandoc.org/installing.html) 3.0+ (on `PATH`, or point the
  `PANDOC` environment variable at the executable; the AST filter relies on
  the pandoc 3 document AST)
- Python 3.10+ with two packages:

  ```bash
  pip install panflute pymupdf
  ```

## Usage

```bash
python convert.py path/to/manuscript.tex -o output.docx
```

`convert.py` runs a four-stage pipeline:

| Stage | Tool | What it does |
|-------|------|--------------|
| 1 | `preprocess.py` | normalizes the LaTeX source into `build/clean.tex` and records figure/table/equation labels into `build/labels.json` |
| 2 | pandoc | LaTeX -> JSON AST |
| 3 | `filter_latex2docx.py` | numbers figures/tables, prefixes captions ("Figure 1: ..."), resolves `\ref`/`\autoref`/`\eqref`, rasterizes PDF figures to PNG (PyMuPDF, cached) |
| 4 | pandoc | JSON -> .docx with `--citeproc` (bundled `ieee.csl`), bibliography auto-detected next to the input, styles from `template.docx` |

Options: `--bibliography`, `--csl`, `--template`, `--resource-dir`,
`--build-dir`, `--no-macros`. The `bash.sh` / `bash.bat` wrappers forward all
arguments to `convert.py`.

## What is supported

- tables (with captions and numbering)
- display equations (numbered `(1)`, `(2)`, ...; `\eqref` resolves to `(N)`)
- figures (PDF figures are rasterized to PNG at 1024 px; multi-figure floats
  and `figure*` spans are kept)
- `algorithm`/`algorithmic` pseudocode (downgraded to an "Algorithm N"
  heading plus a verbatim block)
- IEEE constructs: title block, abstract, `\IEEEPARstart`, keywords,
  `IEEEbiography` (photo + bolded name paragraph)
- citations via `--citeproc` with IEEE numbering `[1]`, bibliography at the
  end of the document

## Project layout

```
convert.py               one-command pipeline entry point
preprocess.py            stage 1: LaTeX normalization + label manifest
filter_latex2docx.py     stage 3: pandoc AST post-processing (panflute)
template.docx            Word style template (reference-doc)
ieee.csl                 IEEE citation style for citeproc
manuscript/              local test manuscripts (git-ignored, not distributed)
bash.sh / bash.bat       thin wrappers around convert.py
```

Intermediate files live in `build/` (git-ignored): `clean.tex`,
`labels.json`, `ast.json`, `filtered.json`.

## Options reference

| Option | Default | Description |
|--------|---------|-------------|
| `-o, --output` | alongside the input tex | output .docx path |
| `--bibliography` | auto-detect (`<input>.bib` or `ref.bib` next to input) | bib file handed to citeproc |
| `--csl` | bundled `ieee.csl` | citation style |
| `--template` | bundled `template.docx` | Word style reference-doc; replace with your own for custom styles |
| `--resource-dir` | input's directory | where figures/bib live |
| `--build-dir` | `build/` | scratch directory for intermediate files |
| `--no-macros` | off | skip the built-in vanvliet macro expansion table (`\mat`, `\tcov`, ...) |

## Troubleshooting

- **`pandoc not found`** — install pandoc or set the `PANDOC` environment
  variable to the full path of the executable:
  `PANDOC=/path/to/pandoc python convert.py ...`
- **`ModuleNotFoundError: panflute` / `pymupdf`** — run the pipeline with the
  interpreter that has the packages installed, or `pip install panflute pymupdf`.
- **`unresolved reference: \ref{...}`** — the label has no matching figure,
  table or equation; check the label spelling in the manuscript. The output
  keeps the label text where the number would be.
- **`PDF image not found`** — figures must live in `--resource-dir` (by
  default the input tex's directory), matching the paths in `\includegraphics`.
- **Citations show as raw keys** — no `.bib` was found; pass
  `--bibliography path/to/refs.bib`.

## Known limitations

- `\thanks` footnotes (funding/affiliations) are dropped from the title block;
  author affiliations survive inside `IEEEbiography` entries only.
- Algorithm pseudocode is plain verbatim text: math inside it stays as LaTeX
  source and line indentation is not preserved.
- The first page layout (two-column IEEE title area) is not reproduced; the
  title block is a centered bold paragraph.
- Page/column breaks and float placement (`[!htb]`) are Word-flowed, not
  reproduced literally.
