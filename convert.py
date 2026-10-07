#!/usr/bin/env python
"""One-command LaTeX -> Word (.docx) conversion.

Pipeline
--------
1. preprocess.py          normalize the LaTeX source + emit build/labels.json
2. pandoc                 latex -> JSON AST
3. filter_latex2docx.py   number floats, resolve \\ref/\\eqref, rasterize PDFs
4. pandoc                 JSON -> docx (--citeproc with the bibliography)

Requirements: pandoc on PATH (or the PANDOC env var), Python packages
panflute and pymupdf (``pip install panflute pymupdf``).

Usage
-----
    python convert.py path/to/manuscript.tex -o output.docx
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import preprocess as pre

HERE = Path(__file__).parent


def find_pandoc() -> str:
    cand = os.environ.get("PANDOC", "")
    if cand and Path(cand).exists():
        return cand
    which = shutil.which("pandoc")
    if which:
        return which
    for p in (
        Path.home() / "AppData/Local/Pandoc/pandoc.exe",
        Path("C:/Program Files/Pandoc/pandoc.exe"),
    ):
        if p.exists():
            return str(p)
    sys.exit(
        "pandoc not found. Install pandoc (https://pandoc.org/installing.html) "
        "or set the PANDOC environment variable to the pandoc executable."
    )


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], **kwargs)
    if result.returncode != 0:
        sys.exit(f"command failed (exit code {result.returncode}): {cmd[0]}")
    return result


def find_bibliography(input_tex: Path, resource_dir: Path) -> Path | None:
    for name in (f"{input_tex.stem}.bib", "ref.bib"):
        cand = resource_dir / name
        if cand.exists():
            return cand
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Convert a LaTeX manuscript to Word, keeping tables, "
        "equations, figures and citations."
    )
    ap.add_argument("input", type=Path, help="input .tex manuscript")
    ap.add_argument("-o", "--output", type=Path,
                    help="output .docx (default: alongside the input tex)")
    ap.add_argument("--bibliography", type=Path,
                    help="bib file for citeproc (default: auto-detect next to input)")
    ap.add_argument("--csl", type=Path, default=HERE / "ieee.csl",
                    help="citation style (default: bundled ieee.csl)")
    ap.add_argument("--template", type=Path, default=HERE / "template.docx",
                    help="Word reference template (default: bundled template.docx)")
    ap.add_argument("--resource-dir", type=Path,
                    help="directory with figures/bib (default: input's directory)")
    ap.add_argument("--build-dir", type=Path, default=HERE / "build",
                    help="scratch directory for intermediate files")
    ap.add_argument("--no-macros", action="store_true",
                    help="skip the optional vanvliet macro expansion table")
    args = ap.parse_args(argv)

    if not args.input.exists():
        sys.exit(f"input not found: {args.input}")

    pandoc = find_pandoc()
    resource_dir = (args.resource_dir or args.input.parent).resolve()
    build = args.build_dir
    output = args.output or args.input.with_suffix(".docx")

    # stage 1: normalize LaTeX + collect labels
    clean_tex, labels_path = pre.preprocess(
        args.input, build, resource_dir, macros=not args.no_macros
    )
    print(f"[1/4] preprocessed -> {clean_tex}")

    # stage 2: latex -> JSON AST
    ast_path = build / "ast.json"
    run([pandoc, clean_tex, "-f", "latex+raw_tex", "-t", "json", "-o", ast_path])
    print(f"[2/4] pandoc AST -> {ast_path}")

    # stage 3: AST filter (number floats, resolve refs, rasterize PDFs)
    filtered_path = build / "filtered.json"
    run([sys.executable, HERE / "filter_latex2docx.py",
         ast_path, labels_path, filtered_path])
    print(f"[3/4] filtered AST -> {filtered_path}")

    # stage 4: JSON -> docx
    cmd = [pandoc, filtered_path, "-f", "json", "--citeproc",
           "--resource-path", resource_dir, "-o", output]
    bib = args.bibliography or find_bibliography(args.input, resource_dir)
    if bib:
        cmd += ["--bibliography", bib]
    else:
        print("[4/4] warning: no .bib file found; citations will be missing. "
              "Pass --bibliography PATH.bib if that is unexpected.")
    if args.csl and Path(args.csl).exists():
        cmd += ["--csl", args.csl]
    if args.template and Path(args.template).exists():
        cmd += ["--reference-doc", args.template]
    run(cmd)
    print(f"[4/4] wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
