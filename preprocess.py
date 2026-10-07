#!/usr/bin/env python
"""Stage 1 of the LaTeX -> Word pipeline: source normalization.

Turns a LaTeX manuscript (IEEE style and similar) into a pandoc-friendly
LaTeX file and a label manifest consumed by stage 2's pandoc filter.

What it does
------------
* strips comments, the preamble, and float/spacing noise commands
* rewrites IEEE-specific constructs (title block, abstract, keywords,
  biographies, \\IEEEPARstart) into plain LaTeX pandoc understands
* downgrades algorithm/algorithmic environments to a bold caption line
  plus a verbatim pseudocode block (pandoc cannot parse algorithmicx)
* unwraps \\subfloat[]{...} so every graphic inside a figure survives
* numbers \\begin{equation} blocks as (1), (2), ... and records the
  label -> number mapping in labels.json for \\eqref resolution
* collects figure/table label order into labels.json for \\ref resolution

Usage
-----
    python preprocess.py INPUT.tex --build-dir build --resource-dir DIR

Outputs
-------
    build/clean.tex     normalized LaTeX fed to pandoc
    build/labels.json   {"res_dir":..., "fig":[...], "tab":[...], "eq":{...}}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

RE_COMMENT = re.compile(r"(?<!\\)%.*")

RE_TITLE = re.compile(r"\\title\{")
RE_AUTHOR = re.compile(r"\\author\{")

# A group that tolerates one level of nested braces: {arg} where arg may
# itself contain {..} but not deeper.
_ARG = r"(?:[^{}]|\{[^{}]*\})*"
_ARG2 = r"(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*"

RE_THANKS = re.compile(r"\\thanks\{" + _ARG + r"\}")
RE_IEEEPARSTART = re.compile(r"\\IEEEPARstart\{(.)\}\{(.?)\}")
RE_IEEEPUBID = re.compile(r"\\IEEEpubidadjcol|\\IEEEpeerreviewmaketitle")
RE_IEEEKEYWORDS = re.compile(
    r"\\begin\{IEEEkeywords\}(?P<body>.*?)\\end\{IEEEkeywords\}", re.DOTALL
)
RE_ABSTRACT = re.compile(
    r"\\begin\{abstract\}(?P<body>.*?)\\end\{abstract\}", re.DOTALL
)
RE_BIOGRAPHY = re.compile(
    r"\\begin\{IEEEbiography\}\s*\[(?P<img>\{(?:" + _ARG2 + r")*\})\]\s*\{(?P<name>"
    + _ARG2
    + r")\}(?P<body>.*?)\\end\{IEEEbiography\}",
    re.DOTALL,
)

RE_FIGURE_BLOCK = re.compile(
    r"\\begin\{figure\*?\}(?P<opts>\[[^\]]*\])?(?P<body>.*?)\\end\{figure\*?\}",
    re.DOTALL,
)
RE_TABLE_BLOCK = re.compile(
    r"\\begin\{table\*?\}(?P<opts>\[[^\]]*\])?(?P<body>.*?)\\end\{table\*?\}",
    re.DOTALL,
)
RE_ALGORITHM_BLOCK = re.compile(
    r"\\begin\{algorithm\}(?P<opts>\[[^\]]*\])?(?P<body>.*?)\\end\{algorithm\}",
    re.DOTALL,
)
RE_ALGORITHMIC = re.compile(
    r"\\begin\{algorithmic\}(?P<opts>\[\d+\])?(?P<code>.*?)\\end\{algorithmic\}",
    re.DOTALL,
)
RE_CAPTION = re.compile(r"\\caption\s*(?:\[[^\]]*\])?\s*\{(?P<text>" + _ARG2 + r")\}")
RE_LABEL = re.compile(r"\\label\{([^}]+)\}")
RE_SUBFLOAT = re.compile(
    r"\\subfloat\s*\*?\s*(?:\[[^\]]*\])?\s*\{(?P<body>" + _ARG2 + r")\}"
)

RE_EQUATION = re.compile(
    r"\\begin\{equation\}(?P<body>.*?)\\end\{equation\}", re.DOTALL
)

RE_NOISE_INLINE = re.compile(
    r"\\setlength\s*\{[^{}]*\}\s*\{[^{}]*\}"      # \setlength{..}{..}
    r"|\\(?:vspace|hspace)\s*\*?\s*\{[^{}]*\}"    # \vspace{..} / \hspace{..}
    r"|\\renewcommand\s*\{[^{}]*\}\s*\{[^{}]*\}"  # \renewcommand{..}{..}
    r"|\\markboth\s*\{[^{}]*\}\s*\{[^{}]*\}"
    r"|\\bibliographystyle\s*\{[^{}]*\}"
    r"|\\bibliography\s*\{[^{}]*\}"
    r"|\\thispagestyle\s*\{[^{}]*\}"
    r"|\\IEEEpubidadjcol"
    r"|\\centering"
    r"|\\hfill|\\hfil"
    r"|\\small|\\footnotesize|\\normalsize|\\large|\\Large|\\LARGE|huge|\\Huge"
    r"|\\maketitle"
)

# algorithmic -> pseudocode keyword mapping
ALGORITHMIC_KEYWORDS = [
    (re.compile(r"\\REQUIRE\b"), "Input:"),
    (re.compile(r"\\ENSURE\b"), "Output:"),
    (re.compile(r"\\STATE\b"), ""),
    (re.compile(r"\\IF\s*\{(?P<c>" + _ARG2 + r")\}"), r"if \g<c> then"),
    (re.compile(r"\\ELSIF\s*\{(?P<c>" + _ARG2 + r")\}"), r"else if \g<c> then"),
    (re.compile(r"\\ELSE\b"), "else"),
    (re.compile(r"\\ENDIF\b"), "end if"),
    (re.compile(r"\\WHILE\s*\{(?P<c>" + _ARG2 + r")\}"), r"while \g<c> do"),
    (re.compile(r"\\ENDWHILE\b"), "end while"),
    (re.compile(r"\\FOR\s*\{(?P<c>" + _ARG2 + r")\}"), r"for \g<c> do"),
    (re.compile(r"\\ENDFOR\b"), "end for"),
    (re.compile(r"\\RETURN\b"), "return"),
    (re.compile(r"\\COMMENT\s*\{(?P<c>" + _ARG2 + r")\}"), r"// \g<c>"),
]

# Optional macro expansion table (kept from the original vanvliet toolset;
# harmless for manuscripts that do not define these macros).
VANVLIET_MACROS = [
    (re.compile(r"\\tcov\{\\mat\{([^}]+)\}\}"), r"$\\mathbf{\\Sigma}_\\mathbf{\1}$"),
    (re.compile(r"\\tcov\{\\emat\{([^}]+)\}\}"), r"$\\mathbf{\\Sigma}_{\\widehat{\\mathbf{\1}}}$"),
    (re.compile(r"\\tcov\{\\text\{([^}]+)\}\}"), r"$\\mathbf{\\Sigma}_\\text{\1}$"),
    (re.compile(r"\\icov\{\\emat\{([^}]+)\}\}"), r"\\mathbf{\\Sigma}^{-1}_{\\widehat{\\mathbf{\1}}}"),
    (re.compile(r"\\ticov\{\\emat\{([^}]+)\}\}"), r"$\\mathbf{\\Sigma}^{-1}_{\\widehat{\\mathbf{\1}}}$"),
    (re.compile(r"\\mat\{([^}]+)\}"), r"\\mathbf{\1}"),
    (re.compile(r"\\vec\{([^}]+)\}"), r"\\mathbf{\1}"),
    (re.compile(r"\\tmat\{([^}]+)\}"), r"$\\mathbf{\1}$"),
    (re.compile(r"\\tvec\{([^}]+)\}"), r"$\\mathbf{\1}$"),
    (re.compile(r"\\emat\{([^}]+)\}"), r"\\widehat{\\mathbf{\1}}"),
    (re.compile(r"\\evec\{([^}]+)\}"), r"\\widehat{\\mathbf{\1}}"),
    (re.compile(r"\\temat\{([^}]+)\}"), r"$\\widehat{\\mathbf{\1}}$"),
    (re.compile(r"\\tevec\{([^}]+)\}"), r"$\\widehat{\\mathbf{\1}}$"),
    (re.compile(r"\\trans\b"), r"^\\mathsf{T}"),
    (re.compile(r"\\hermconj\b"), r"^\\mathsf{H}"),
    (re.compile(r"\\cov\{([^}]+)\}"), r"\\mathbf{\\Sigma}_\\mathbf{\1}"),
    (re.compile(r"\\icov\{([^}]+)\}"), r"\\mathbf{\\Sigma}^{-1}_\\mathbf{\1}"),
    (re.compile(r"\\tcov\{([^}]+)\}"), r"$\\mathbf{\\Sigma}_\\mathbf{\1}$"),
    (re.compile(r"\\ticov\{([^}]+)\}"), r"$\\mathbf{\\Sigma}^{-1}_\\mathbf{\1}$"),
    (re.compile(r"\\vspace\{2ex\}"), r""),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def strip_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = RE_COMMENT.sub("", line)
        lines.append(line.rstrip())
    return "\n".join(lines)


def extract_body(text: str) -> str:
    m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", text, re.DOTALL)
    return m.group(1) if m else text


def collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def balanced_group(text: str, start: int) -> tuple[str, int]:
    """Return the {...} group content starting at `start` (which must point
    at '{'), tolerating nested braces. Returns (content, index_after_close)."""
    assert text[start] == "{"
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
    raise ValueError("Unbalanced braces in LaTeX source")


# ---------------------------------------------------------------------------
# Title block
# ---------------------------------------------------------------------------

def build_title_block(text: str) -> str:
    """Replace \\title{...}, \\author{...} and \\maketitle with a plain
    centered title block. Works whether they live in preamble or body."""
    title = ""
    m = re.search(r"\\title\s*\{", text)
    if m:
        title, _ = balanced_group(text, m.end() - 1)
        text = text[: m.start()] + text[m.end() - 1 + 1 + len(title) + 1 :]

    author = ""
    m = re.search(r"\\author\s*\{", text)
    if m:
        author, end = balanced_group(text, m.end() - 1)
        text = text[: m.start()] + text[end:]

    author = RE_THANKS.sub("", author)
    author = re.sub(r"\s+", " ", author).strip()
    title = re.sub(r"\s+", " ", title).strip()

    block_parts = []
    if title:
        block_parts.append("\\begin{center}\n\\textbf{%s}\n\\end{center}" % title)
    if author:
        block_parts.append("\\begin{center}\n%s\n\\end{center}" % author)

    if block_parts:
        text = text.replace(r"\maketitle", "\n\n".join(block_parts) + "\n")
    else:
        text = text.replace(r"\maketitle", "")
    return text


# ---------------------------------------------------------------------------
# IEEE constructs
# ---------------------------------------------------------------------------

def rewrite_ieee_constructs(text: str) -> str:
    text = RE_IEEEPARSTART.sub(r"\1\2", text)

    def keywords_sub(m: re.Match) -> str:
        body = re.sub(r"\s+", " ", m.group("body")).strip()
        return "\\noindent\\textbf{Index Terms---}%s\n" % body

    text = RE_IEEEKEYWORDS.sub(keywords_sub, text)

    def abstract_sub(m: re.Match) -> str:
        body = m.group("body").strip()
        return "\n\\section*{Abstract}\n\n%s\n" % body

    text = RE_ABSTRACT.sub(abstract_sub, text)

    def biography_sub(m: re.Match) -> str:
        img = m.group("img").strip()
        if img.startswith("{") and img.endswith("}"):
            img = img[1:-1].strip()  # drop the optional-arg wrapper braces
        name = re.sub(r"\s+", " ", m.group("name")).strip()
        body = re.sub(r"\s+", " ", m.group("body")).strip()
        parts = ["\\begin{center}", img, "\\end{center}", ""]
        parts.append("\\noindent\\textbf{%s} %s\n" % (name, body))
        return "\n".join(parts)

    text = RE_BIOGRAPHY.sub(biography_sub, text)
    return text


# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------

def _convert_algorithmic_body(code: str) -> str:
    out_lines = []
    lineno = 0
    for raw in code.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("%"):
            continue
        for pattern, repl in ALGORITHMIC_KEYWORDS:
            line = pattern.sub(repl, line)
        # drop leftover lone braces used as grouping in algorithmic code
        line = re.sub(r"^\{\s*|\s*\}$", "", line)
        line = line.strip()
        if not line:
            continue
        lineno += 1
        out_lines.append("%d: %s" % (lineno, line))
    return "\n".join(out_lines)


def _render_algorithm(m: re.Match, counter: list[int]) -> str:
    counter[0] += 1
    n = counter[0]
    body = m.group("body")
    cap_m = RE_CAPTION.search(body)
    caption = re.sub(r"\s+", " ", cap_m.group("text")).strip() if cap_m else ""
    alg_m = RE_ALGORITHMIC.search(body)
    code = _convert_algorithmic_body(alg_m.group("code")) if alg_m else ""
    head = "\\noindent\\textbf{Algorithm %d: %s}\n" % (n, caption)
    block = ""
    if code:
        block = "\n\\begin{verbatim}\n%s\n\\end{verbatim}\n" % code
    return head + block


def rewrite_algorithms(text: str) -> str:
    """Handle algorithms both inside figure floats (the common IEEE layout)
    and standalone. Algorithm labels become plain text."""
    counter = [0]

    def figure_sub(m: re.Match) -> str:
        body = m.group("body")
        if "\\begin{algorithm}" not in body:
            return m.group(0)
        # strip float bookkeeping, keep only the rendered algorithm content
        body = RE_LABEL.sub("", body)
        body = RE_ALGORITHM_BLOCK.sub(lambda a: _render_algorithm(a, counter), body)
        return "\n" + body.strip() + "\n"

    text = RE_FIGURE_BLOCK.sub(figure_sub, text)
    # standalone algorithms (not wrapped in a figure)
    text = RE_ALGORITHM_BLOCK.sub(lambda a: _render_algorithm(a, counter), text)
    return text


# ---------------------------------------------------------------------------
# Subfloats, equations, labels
# ---------------------------------------------------------------------------

def unwrap_subfloats(text: str) -> str:
    # two passes: nested subfloats inside one figure body are independent
    prev = None
    while prev != text:
        prev = text
        text = RE_SUBFLOAT.sub(lambda m: m.group("body"), text)
    text = re.sub(r"^\s*\\\\\s*$", "", text, flags=re.MULTILINE)  # lone \\ lines
    # keep only the main (last) label inside each figure float: subfigure
    # labels would otherwise litter the float and confuse the filter
    out, last = [], 0
    for m in RE_FIGURE_BLOCK.finditer(text):
        out.append(text[last : m.start()])
        body = m.group("body")
        labs = RE_LABEL.findall(body)
        if len(labs) > 1:
            for lab in labs[:-1]:
                body = body.replace("\\label{%s}" % lab, "")
        prefix = m.group(0)[: m.start("body") - m.start(0)]
        suffix = m.group(0)[m.end("body") - m.start(0) :]
        out.append(prefix + body + suffix)
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def unstar_float_environments(text: str) -> str:
    """figure*/table* spans both columns in LaTeX; pandoc treats them the
    same as figure/table, so normalise before parsing."""
    text = text.replace(r"\begin{figure*}", r"\begin{figure}")
    text = text.replace(r"\end{figure*}", r"\end{figure}")
    text = text.replace(r"\begin{table*}", r"\begin{table}")
    text = text.replace(r"\end{table*}", r"\end{table}")
    return text


def number_equations(text: str, labels: dict) -> str:
    counter = [0]

    def eq_sub(m: re.Match) -> str:
        counter[0] += 1
        n = counter[0]
        body = m.group("body")
        lab_m = RE_LABEL.search(body)
        if lab_m:
            labels.setdefault("eq", {})[lab_m.group(1)] = n
            body = RE_LABEL.sub("", body)
        body = body.rstrip() + "\n\\qquad \\text{(%d)}\n" % n
        return "\\begin{equation}\n%s\\end{equation}" % body

    return RE_EQUATION.sub(eq_sub, text)


def collect_float_labels(text: str, labels: dict) -> None:
    fig_order, tab_order = [], []
    for m in RE_FIGURE_BLOCK.finditer(text):
        lab = RE_LABEL.search(m.group("body"))
        if lab:
            fig_order.append(lab.group(1))
    for m in RE_TABLE_BLOCK.finditer(text):
        lab = RE_LABEL.search(m.group("body"))
        if lab:
            tab_order.append(lab.group(1))
    labels["fig"] = fig_order
    labels["tab"] = tab_order


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def preprocess(
    input_tex: Path,
    build_dir: Path,
    resource_dir: Path,
    macros: bool = True,
) -> tuple[Path, Path]:
    text = read_source(input_tex)
    text = strip_comments(text)
    text = extract_body(text)
    text = build_title_block(text)
    text = rewrite_ieee_constructs(text)
    text = rewrite_algorithms(text)
    text = unwrap_subfloats(text)
    text = unstar_float_environments(text)
    if macros:
        for pattern, repl in VANVLIET_MACROS:
            text = pattern.sub(repl, text)

    labels: dict = {"res_dir": str(resource_dir.resolve()).replace("\\", "/")}
    text = number_equations(text, labels)
    collect_float_labels(text, labels)

    # final noise removal on remaining lines
    lines = [RE_NOISE_INLINE.sub("", ln) for ln in text.splitlines()]
    text = "\n".join(lines)
    text = collapse_blank_lines(text)

    build_dir.mkdir(parents=True, exist_ok=True)
    clean_tex = build_dir / "clean.tex"
    labels_path = build_dir / "labels.json"
    clean_tex.write_text(text, encoding="utf-8")
    labels_path.write_text(
        json.dumps(labels, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return clean_tex, labels_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="input .tex manuscript")
    p.add_argument("--build-dir", type=Path, default=Path("build"))
    p.add_argument("--resource-dir", type=Path, default=None,
                   help="directory containing figures/bib (default: input's dir)")
    p.add_argument("--no-macros", action="store_true",
                   help="skip the optional vanvliet macro expansion table")
    args = p.parse_args(argv)

    resource_dir = args.resource_dir or args.input.parent
    clean_tex, labels_path = preprocess(
        args.input, args.build_dir, resource_dir, macros=not args.no_macros
    )
    print(f"clean tex : {clean_tex}")
    print(f"labels    : {labels_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
