#!/usr/bin/env bash
# tex2docx: one-command LaTeX -> Word conversion.
# Usage: ./bash.sh manuscript/tex/manuscript.tex -o output.docx
exec python "$(dirname "$0")/convert.py" "$@"
