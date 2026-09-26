#!/bin/sh
# Regenerate the paper's numbers and tables from experiment results, then build the PDF.
set -e
cd "$(dirname "$0")/.."
python3 paper/make_numbers.py
if command -v tectonic >/dev/null 2>&1; then
  (cd paper && tectonic assay.tex)
elif command -v latexmk >/dev/null 2>&1; then
  (cd paper && latexmk -pdf -interaction=nonstopmode assay.tex && latexmk -c)
else
  echo "no TeX engine found: brew install tectonic (macOS) or install TeX Live" >&2
  exit 1
fi
python3 scripts/check_dashes.py
ls -la paper/assay.pdf
