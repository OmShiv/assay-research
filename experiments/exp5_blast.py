"""E5: how large is a change, really? Blast radius and obligation levels on real repositories.

For every module m: the blast radius of editing only m, as a fraction of all modules, and
the obligation level the policy assigns to that single-module edit. Most edits are cheap
to verify; a few hubs are expensive. Risk-proportional review spends reviewer effort
where the radius is.
"""

from common import corpora, fresh_index, save_fig, save_json, style, BLUE, ORDINAL4, INK2, MUTED
from assay.graph import Graph
from assay.obligation import LEVEL_NAMES, evidence_obligation


def measure(name, root):
    idx, _ = fresh_index(root)
    g = Graph.from_index(idx)
    mods = sorted(idx["modules"])
    n = len(mods)
    fracs = []
    levels = [0, 0, 0, 0]
    per = []
    for m in mods:
        ob = evidence_obligation(idx, [m], g)
        fracs.append(ob["br_fraction"])
        levels[ob["level"]] += 1
        per.append({"module": m, "br": ob["br_size"], "risk": idx["modules"][m]["risk"], "level": ob["level"]})
    fracs.sort()
    median = fracs[n // 2]
    top = sorted(per, key=lambda r: -r["br"])[:5]
    return {"corpus": name, "modules": n, "median_br_fraction": median, "mean_br_fraction": sum(fracs) / n,
            "p90_br_fraction": fracs[int(0.9 * (n - 1))], "levels": levels, "fracs": fracs, "top": top,
            "single_module_share": sum(1 for f in fracs if f * n <= 1.0001) / n}


def plot(rows):
    plt = style()
    from common import ORANGE, AQUA, GRID
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.4, 3.0), gridspec_kw={"width_ratios": [1.15, 1]})
    # left: per corpus, the median, p90, and max blast radius of a one-module edit
    for i, r in enumerate(rows):
        fr = r["fracs"]
        n = len(fr)
        med, p90, mx = fr[n // 2] * 100, fr[int(0.9 * (n - 1))] * 100, fr[-1] * 100
        ax1.plot([0, mx], [i, i], color=GRID, linewidth=3, solid_capstyle="round", zorder=1)
        ax1.scatter([med], [i], color=BLUE, s=34, zorder=3, label="median" if i == 0 else None)
        ax1.scatter([p90], [i], color=ORANGE, s=34, zorder=3, marker="D", label="p90" if i == 0 else None)
        ax1.scatter([mx], [i], color=AQUA, s=34, zorder=3, marker="s", label="max" if i == 0 else None)
        ax1.text(mx + 3, i, f"self-only {r['single_module_share']*100:.0f}%", fontsize=6.4, color=INK2, va="center")
    ax1.set_yticks(range(len(rows)))
    ax1.set_yticklabels([r["corpus"] for r in rows])
    ax1.invert_yaxis()
    ax1.set_xlim(0, 135)
    ax1.set_xticks([0, 25, 50, 75, 100])
    ax1.grid(axis="y", visible=False)
    ax1.set_xlabel("blast radius of a one-module edit, % of modules")
    ax1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=3, fontsize=6.8, handletextpad=0.3, columnspacing=1.2)
    ax1.set_title("Typical edits are small, hubs are not")
    # right: stacked obligation level distribution per corpus (ordinal ramp)
    names = [r["corpus"] for r in rows]
    left = [0] * len(rows)
    for lvl in range(4):
        vals = [r["levels"][lvl] / r["modules"] * 100 for r in rows]
        ax2.barh(range(len(rows)), vals, left=left, color=ORDINAL4[lvl], height=0.62, label=f"L{lvl} {LEVEL_NAMES[lvl]}", linewidth=0, edgecolor="white")
        for i, (v, l0) in enumerate(zip(vals, left)):
            if v >= 12:
                ax2.text(l0 + v / 2, i, f"{v:.0f}%", ha="center", va="center", fontsize=6.6, color="white" if lvl >= 1 else INK2)
        left = [l0 + v for l0, v in zip(left, vals)]
    ax2.set_yticks(range(len(rows)))
    ax2.set_yticklabels(names)
    ax2.invert_yaxis()
    ax2.set_xlim(0, 100)
    ax2.set_xlabel("single-module edits by obligation level, %")
    ax2.grid(axis="y", visible=False)
    ax2.legend(loc="lower center", bbox_to_anchor=(0.5, -0.42), ncol=4, fontsize=6.8, handlelength=1.2)
    ax2.set_title("Obligation is proportional to radius and risk")
    fig.tight_layout()
    save_fig(fig, "e5_blast")


def main():
    rows = []
    for name, root in corpora():
        print(f"E5 {name}")
        rows.append(measure(name, root))
    save_json("e5_blast", {"rows": [{k: v for k, v in r.items() if k != "fracs"} | {"fracs": r["fracs"]} for r in rows]})
    plot(rows)
    for r in rows:
        print(f"{r['corpus']:10} n={r['modules']:4d} median BR {r['median_br_fraction']*100:5.1f}%  p90 {r['p90_br_fraction']*100:5.1f}%  levels {r['levels']}  top: {r['top'][0]['module']} ({r['top'][0]['br']})")


if __name__ == "__main__":
    main()
