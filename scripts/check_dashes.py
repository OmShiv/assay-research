"""Fail if any prose, code, or bibliography file contains an em-dash, an en-dash, or a
LaTeX/Markdown triple hyphen that would render as one. Markdown table rules (|---|) and
comment rulers in code are ignored."""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTS = (".tex", ".md", ".bib", ".py", ".txt", ".toml", ".yml", ".yaml", ".json")
SKIP_DIRS = {".git", "__pycache__", "figures", ".assay", ".pytest_cache", "results", "node_modules", "dist", "build"}


def main() -> int:
    hits = []
    for root, dirs, files in os.walk(ROOT):
        # virtual environments and other dot directories hold third-party text we do not own
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info") and not d.startswith(".")]
        for fn in files:
            if not fn.endswith(EXTS):
                continue
            path = os.path.join(root, fn)
            try:
                text = open(path, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if "\u2014" in line or "\u2013" in line:
                    hits.append((path, n, "unicode dash", line.strip()[:80]))
                if fn.endswith((".tex", ".md", ".bib")) and "---" in line:
                    if re.fullmatch(r"\s*\|?(\s*:?-{3,}:?\s*\|)+\s*", line) or line.lstrip().startswith("#"):
                        continue
                    hits.append((path, n, "triple hyphen", line.strip()[:80]))
    for path, n, kind, excerpt in hits:
        print(f"{os.path.relpath(path, ROOT)}:{n}: {kind}: {excerpt}")
    print(f"dash check: {len(hits)} problem(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
