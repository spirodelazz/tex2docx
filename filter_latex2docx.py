#!/usr/bin/env python
"""Stage 2 filter: post-process the pandoc AST (JSON) via panflute.

    python filter_latex2docx.py AST_IN LABELS_JSON AST_OUT

Two passes over the document:

Pass 1 (read-only) collects float numbers: figures and tables are numbered
in document order and their labels registered, because LaTeX references
(\\ref, \\autoref) may point to floats that appear LATER in the source.

Pass 2 rewrites the AST:
* prefix figure/table captions with "Figure N: " / "Table N: "
* rasterize PDF images to PNG (PyMuPDF, cached) and drop the height
  attribute so Word can scale images proportionally
* replace raw \\ref{...} / \\autoref{...} / \\eqref{...} inlines with plain
  text ("3", "Figure 3", "(5)"), falling back to the label itself with a
  warning when unresolvable

Note: pandoc >= 3 wraps `table` floats in a Div whose identifier is the
table's \\label; the pf.Table sits inside that Div (identifier empty).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import panflute as pf

REF_RE = re.compile(r"\\(auto)?ref\s*\{([^}]+)\}")
EQREF_RE = re.compile(r"\\eqref\s*\{([^}]+)\}")


class Ctx:
    """Mutable filter state shared by both passes."""

    labels: dict = {}       # labels.json content
    figures: dict = {}      # label -> "Figure N"
    tables: dict = {}       # label -> "Table N"
    fig_n = 0
    tab_n = 0
    warned: set = set()


def _warn(msg: str) -> None:
    if msg not in Ctx.warned:
        Ctx.warned.add(msg)
        print(f"[filter] warning: {msg}", file=sys.stderr)


def init(labels_path: str | Path) -> None:
    p = Path(labels_path)
    if p.exists():
        Ctx.labels = json.loads(p.read_text(encoding="utf-8"))
    else:
        _warn(f"labels file not found: {p}")


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

def _figure_label(elem: pf.Figure) -> str | None:
    """pandoc puts the figure's \\label on the Figure element itself."""
    if elem.identifier:
        return elem.identifier
    order = Ctx.labels.get("fig", [])
    if Ctx.fig_n - 1 < len(order):
        return order[Ctx.fig_n - 1]
    return None


def _table_label(elem: pf.Table) -> str | None:
    """pandoc >= 3 wraps `table` floats in a Div carrying the \\label;
    the Table inside has an empty identifier."""
    if elem.identifier:
        return elem.identifier
    parent = getattr(elem, "parent", None)
    if isinstance(parent, pf.Div) and parent.identifier:
        return parent.identifier
    order = Ctx.labels.get("tab", [])
    if Ctx.tab_n - 1 < len(order):
        return order[Ctx.tab_n - 1]
    return None


# ---------------------------------------------------------------------------
# PDF rasterization
# ---------------------------------------------------------------------------

def _rasterize_pdf(elem: pf.Image) -> None:
    url = elem.url
    if not url.lower().endswith(".pdf"):
        return
    res_dir = Path(Ctx.labels.get("res_dir", "."))
    pdf_path = res_dir / url
    if not pdf_path.exists() and Path(url).exists():
        pdf_path = Path(url)
    if not pdf_path.exists():
        _warn(f"PDF image not found: {pdf_path}")
        return
    png_name = Path(url).with_suffix(".png").as_posix()
    png_path = res_dir / png_name
    if not png_path.exists():
        try:
            import pymupdf

            doc = pymupdf.open(pdf_path)
            page = doc[0]
            scale = 1024.0 / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
            png_path.parent.mkdir(parents=True, exist_ok=True)
            pix.save(png_path)
            doc.close()
        except Exception as exc:  # noqa: BLE001
            _warn(f"failed to rasterize {pdf_path}: {exc}")
            return
    elem.url = png_name


def _iter_images(elem) -> list:
    out = []
    for child in elem.content:
        if isinstance(child, pf.Image):
            out.append(child)
        elif hasattr(child, "content"):
            out.extend(_iter_images(child))
    return out


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------

def _prefix_caption(caption, num: str) -> None:
    if caption is None:
        return
    content = getattr(caption, "content", None)
    if not content:
        return
    first = content[0]
    if hasattr(first, "content"):
        first.content.insert(0, pf.Str(f"{num}: "))


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------

def _resolve_ref_text(label: str, autoref: bool) -> str | None:
    if label in Ctx.figures:
        full = Ctx.figures[label]
        return full if autoref else full.split()[-1]
    if label in Ctx.tables:
        full = Ctx.tables[label]
        return full if autoref else full.split()[-1]
    eqs = Ctx.labels.get("eq", {})
    if label in eqs:
        n = str(eqs[label])
        return f"Figure {n}" if autoref else f"({n})"
    _warn(f"unresolved reference: \\{'auto' if autoref else ''}ref{{{label}}}")
    return None


# ---------------------------------------------------------------------------
# Pass 1: collect float numbers (read-only walk)
# ---------------------------------------------------------------------------

def collect(elem, doc) -> None:
    if isinstance(elem, pf.Figure):
        Ctx.fig_n += 1
        label = _figure_label(elem)
        if label:
            Ctx.figures[label] = f"Figure {Ctx.fig_n}"
    elif isinstance(elem, pf.Table):
        Ctx.tab_n += 1
        label = _table_label(elem)
        if label:
            Ctx.tables[label] = f"Table {Ctx.tab_n}"


# ---------------------------------------------------------------------------
# Pass 2: rewrite the AST
# ---------------------------------------------------------------------------

def rewrite(elem, doc):
    if isinstance(elem, pf.Figure):
        num = Ctx.figures.get(elem.identifier)
        if num is None:
            order = Ctx.labels.get("fig", [])
            if elem.identifier in order:
                num = f"Figure {order.index(elem.identifier) + 1}"
        if num:
            _prefix_caption(elem.caption, num)
        for image in _iter_images(elem):
            _rasterize_pdf(image)
        return None

    if isinstance(elem, pf.Table):
        label = _table_label(elem)
        num = Ctx.tables.get(label) if label else None
        caption_para = None
        if num and elem.caption is not None:
            _prefix_caption(elem.caption, num)
            blocks = list(getattr(elem.caption, "content", []) or [])
            if blocks:
                inlines = []
                for blk in blocks:
                    if isinstance(blk, (pf.Plain, pf.Para)):
                        inlines.extend(blk.content)
                    else:
                        inlines.append(blk)
                if inlines:
                    caption_para = pf.Para(*inlines)
            # hand the caption to a plain paragraph; the table itself gets
            # an empty caption. This guarantees the caption renders exactly
            # once, regardless of how the docx writer treats Table captions.
            elem.caption = pf.Caption()
        if caption_para is not None:
            return [caption_para, elem]
        return None

    if isinstance(elem, pf.Image):
        _rasterize_pdf(elem)
        if "height" in elem.attributes:
            del elem.attributes["height"]
        return None

    if isinstance(elem, pf.RawInline) and elem.format == "latex":
        m = REF_RE.match(elem.text)
        if m:
            text = _resolve_ref_text(m.group(2), bool(m.group(1)))
            if text is not None:
                return pf.Str(text)
            return pf.Str(m.group(2))
        m = EQREF_RE.match(elem.text)
        if m:
            eqs = Ctx.labels.get("eq", {})
            if m.group(1) in eqs:
                return pf.Str("(%d)" % eqs[m.group(1)])
            _warn(f"unresolved \\eqref{{{m.group(1)}}}")
            return pf.Str(m.group(1))
    return None


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: filter_latex2docx.py AST_IN LABELS_JSON AST_OUT",
            file=sys.stderr,
        )
        return 2
    ast_in, labels_path, ast_out = (Path(a) for a in sys.argv[1:4])

    init(labels_path)

    with open(ast_in, encoding="utf-8") as f:
        doc = pf.load(f)

    # pass 1: collect float numbers (action returns None -> doc untouched)
    pf.run_filter(collect, doc=doc)
    # pass 2: rewrite captions, references and images
    pf.run_filter(rewrite, doc=doc)

    with open(ast_out, "w", encoding="utf-8") as f:
        pf.dump(doc, f)

    eqs = Ctx.labels.get("eq", {})
    print(
        f"[filter] figures={Ctx.fig_n} tables={Ctx.tab_n} "
        f"eq_labels={len(eqs)} warnings={len(Ctx.warned)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
