"""Shared helpers for the Assay experiments: corpora discovery, styling, output paths."""

import json
import os
import shutil
import sys

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(EXP_DIR)
SRC = os.path.join(PROJECT_DIR, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

RESULTS = os.path.join(EXP_DIR, "results")
FIGURES = os.path.join(EXP_DIR, "figures")
PAPER_FIGURES = os.path.join(PROJECT_DIR, "paper", "figures")
for d in (RESULTS, FIGURES, PAPER_FIGURES):
    os.makedirs(d, exist_ok=True)

DEFAULT_CORPORA = os.environ.get("ASSAY_CORPORA", os.path.join(PROJECT_DIR, "..", "..", "corpora"))
CORPUS_NAMES = ["express", "flask", "gin", "axum", "fastapi"]

# Validated categorical palette (light surface), fixed slot order; never cycled past 4 here.
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
MAGENTA, GREEN, VIOLET, RED = "#e87ba4", "#008300", "#4a3aa7", "#e34948"
SERIES = [BLUE, ORANGE, AQUA, YELLOW]
# Sequential blue ramp (100 -> 700) and an ordinal 4-step subset that clears the surface floor.
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
ORDINAL4 = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
# Status palette (reserved for state, never for series)
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
# Chrome
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#ffffff", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"


def corpora():
    """Ordered list of (name, path) for corpora that exist on disk, plus Assay itself."""
    out = []
    for name in CORPUS_NAMES:
        p = os.path.abspath(os.path.join(DEFAULT_CORPORA, name))
        if os.path.isdir(p):
            out.append((name, p))
    out.append(("assay", PROJECT_DIR))
    return out


def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.titleweight": "semibold",
        "axes.labelsize": 9,
        "axes.labelcolor": INK2,
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "grid.linestyle": "-",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "figure.dpi": 150,
    })
    return plt


def save_fig(fig, name):
    for ext in ("png", "pdf"):
        path = os.path.join(FIGURES, f"{name}.{ext}")
        fig.savefig(path, dpi=220 if ext == "png" else None, bbox_inches="tight", pad_inches=0.08)
        shutil.copyfile(path, os.path.join(PAPER_FIGURES, f"{name}.{ext}"))
    print(f"  figure {name}")


def save_json(name, obj):
    path = os.path.join(RESULTS, f"{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
    print(f"  results {os.path.relpath(path, PROJECT_DIR)}")
    return path


def load_json(name):
    with open(os.path.join(RESULTS, f"{name}.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def fresh_index(root, depth=3):
    from assay.index import build_index
    idx, stats = build_index(root, depth=depth, use_cache=False)
    return idx, stats
