"""E1: token cost of orienting an agent in a repository.

Four ways an agent can learn where things live, measured in tokens on real repositories:

  full dump        every source file concatenated (Repomix-style)
  exploration      a directory listing plus the ten most-imported files (what an agent
                   typically reads before it can act)
  index            assay.json, the complete module and file index
  brief            `assay brief`, the situation brief at its default 600-token budget

The estimator is tiktoken cl100k_base when available, otherwise chars/4; the same
estimator is used for every condition.
"""

import json
import os

from common import corpora, fresh_index, save_fig, save_json, style, SERIES, INK2, MUTED
from assay.brief import make_brief
from assay.tokens import estimate_tokens, estimator_name


def measure(name, root):
    idx, _ = fresh_index(root)
    files = idx["files"]
    full = 0
    texts = {}
    for rel in files:
        try:
            with open(os.path.join(root, rel), "r", encoding="utf-8", errors="replace") as fh:
                t = fh.read()
        except OSError:
            continue
        texts[rel] = t
        full += estimate_tokens(t)
    indeg = {rel: 0 for rel in files}
    for rel, v in files.items():
        for t in v["imports"]:
            if t in indeg:
                indeg[t] += 1
    top = sorted(indeg, key=lambda r: (-indeg[r], -files[r]["lines"]))[:10]
    listing = "\n".join(sorted(files))
    exploration = estimate_tokens(listing) + sum(estimate_tokens(texts.get(r, "")) for r in top)
    index_tokens = estimate_tokens(json.dumps(idx, separators=(",", ":")))
    modules_only = estimate_tokens(json.dumps({"project": idx["project"], "hints": idx["hints"], "modules": idx["modules"]}, separators=(",", ":")))
    brief = make_brief(None, idx, changed=None, budget_tokens=600)
    hub = idx["hints"]["most_depended"][0] if idx["hints"]["most_depended"] else next(iter(idx["modules"]))
    brief_changed = make_brief(None, idx, changed=[hub], budget_tokens=600)
    return {
        "corpus": name, "files": idx["project"]["files"], "lines": idx["project"]["lines"], "modules": idx["project"]["modules"],
        "full_dump": full, "exploration": exploration, "index": index_tokens, "index_modules_only": modules_only,
        "brief": estimate_tokens(brief), "brief_with_change": estimate_tokens(brief_changed),
        "top_files": top, "brief_text": brief_changed,
    }


def plot(rows):
    plt = style()
    labels = [f"{r['corpus']}\n{r['lines']:,} lines" for r in rows]
    keys = [("full_dump", "Full dump"), ("exploration", "Exploration (listing + 10 files)"), ("index_modules_only", "Index, modules section"), ("brief", "Brief (600-token budget)")]
    n = len(rows)
    h = 0.19
    fig, ax = plt.subplots(figsize=(7.2, 0.62 * n + 1.2))
    for j, (key, label) in enumerate(keys):
        ys = [i + (j - 1.5) * h for i in range(n)]
        vals = [max(r[key], 1) for r in rows]
        ax.barh(ys, vals, height=h - 0.03, color=SERIES[j], label=label, linewidth=0)
        for y, v in zip(ys, vals):
            ax.text(v * 1.12, y, f"{v:,}", va="center", ha="left", fontsize=6.8, color=INK2)
    ax.set_xscale("log")
    ax.set_yticks(range(n))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel(f"tokens ({estimator_name()}), log scale")
    ax.set_xlim(100, max(r["full_dump"] for r in rows) * 6)
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right", ncol=1)
    ax.set_title("Tokens needed to orient an agent, by method")
    save_fig(fig, "e1_tokens")


def main():
    rows = []
    for name, root in corpora():
        print(f"E1 {name}")
        rows.append(measure(name, root))
    for r in rows:
        r["reduction_vs_full"] = round(r["full_dump"] / max(r["brief"], 1), 1)
        r["reduction_vs_exploration"] = round(r["exploration"] / max(r["brief"], 1), 1)
    save_json("e1_tokens", {"estimator": estimator_name(), "rows": rows})
    plot(rows)
    print(f"{'corpus':10} {'lines':>8} {'full':>9} {'explore':>8} {'index':>8} {'modules':>8} {'brief':>6} {'x-full':>7} {'x-expl':>7}")
    for r in rows:
        print(f"{r['corpus']:10} {r['lines']:8,} {r['full_dump']:9,} {r['exploration']:8,} {r['index']:8,} {r['index_modules_only']:8,} {r['brief']:6,} {r['reduction_vs_full']:7} {r['reduction_vs_exploration']:7}")


if __name__ == "__main__":
    main()
