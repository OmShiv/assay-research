"""E3: what a claim should be bound to.

One claim per module. Random edits of k modules. Under dependency semantics (the
correctness criterion of incremental build systems) a claim about module m must be
re-verified iff some module in m's dependency cone changed. Three binding strategies:

  repo hash   bind to the root hash: every claim is stale on every edit (sound, maximal cost)
  self hash   bind to the module's own hash: stale only if m itself changed (cheap, unsound)
  cone hash   bind to the Merkle hash of m's dependency cone (sound and minimal)

Cost = fraction of claims re-verified. Missed = fraction of claims that should have been
invalidated but were not. 100 seeded trials per k per corpus.
"""

import random

from common import corpora, fresh_index, save_fig, save_json, style, SERIES, INK2, MUTED
from assay.graph import Graph

KS = [1, 2, 4, 8, 16, 32]
TRIALS = 100


def measure(name, root, seed=7):
    idx, _ = fresh_index(root)
    g = Graph.from_index(idx)
    mods = sorted(idx["modules"])
    n = len(mods)
    rng = random.Random(seed)
    out = {"corpus": name, "modules": n, "edges": len(idx["edges"]), "k": [], "cost": {"repo": [], "self": [], "cone": []}, "missed": {"repo": [], "self": [], "cone": []}}
    for k in KS:
        if k > n:
            break
        cost = {"repo": [], "self": [], "cone": []}
        missed = {"repo": [], "self": [], "cone": []}
        for _ in range(TRIALS):
            delta = set(rng.sample(mods, k))
            truth = g.blast_radius(delta)              # claims that must be re-verified
            stale = {"repo": set(mods), "self": set(delta), "cone": set(truth)}
            for s, st in stale.items():
                cost[s].append(len(st) / n)
                missed[s].append(len(truth - st) / max(len(truth), 1))
        out["k"].append(k)
        for s in cost:
            out["cost"][s].append(sum(cost[s]) / len(cost[s]))
            out["missed"][s].append(sum(missed[s]) / len(missed[s]))
    return out


def plot(rows):
    plt = style()
    cols = 3
    nrows = (len(rows) + cols - 1) // cols
    fig, axes = plt.subplots(nrows, cols, figsize=(7.2, 2.3 * nrows), sharey=True)
    axes = axes.flatten()
    labels = {"repo": "Repo hash (sound, re-verify everything)", "cone": "Cone hash (sound, minimal)", "self": "Self hash (unsound)"}
    for ax, r in zip(axes, rows):
        for j, s in enumerate(["repo", "cone", "self"]):
            ax.plot(r["k"], [c * 100 for c in r["cost"][s]], color=SERIES[j], linewidth=2, marker="o", markersize=4, label=labels[s])
        ms = r["missed"]["self"]
        i = min(2, len(ms) - 1)
        ax.annotate(f"self hash misses\n{ms[i]*100:.0f}% at k={r['k'][i]}", xy=(r["k"][i], r["cost"]["self"][i] * 100),
                    xytext=(0.55, 0.62), textcoords="axes fraction", fontsize=7, color=INK2,
                    arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.7))
        ax.set_xscale("log", base=2)
        ax.set_xticks(r["k"])
        ax.set_xticklabels([str(k) for k in r["k"]])
        ax.set_title(f"{r['corpus']} ({r['modules']} modules)")
        ax.set_ylim(0, 105)
    for ax in axes[len(rows):]:
        ax.axis("off")
    for ax in axes[-cols:]:
        ax.set_xlabel("modules edited (k)")
    for ax in axes[::cols]:
        ax.set_ylabel("claims re-verified, %")
    handles, labels_ = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels_, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle("Re-verification cost by binding strategy (100 random edits per k)", y=1.01)
    fig.tight_layout()
    save_fig(fig, "e3_staleness")


def main():
    rows = []
    for name, root in corpora():
        print(f"E3 {name}")
        rows.append(measure(name, root))
    save_json("e3_staleness", {"trials": TRIALS, "rows": rows})
    plot(rows)
    for r in rows:
        k4 = r["k"].index(4) if 4 in r["k"] else -1
        print(f"{r['corpus']:10} n={r['modules']:4d} k=4: cost repo {r['cost']['repo'][k4]*100:5.1f}%  cone {r['cost']['cone'][k4]*100:5.1f}%  self {r['cost']['self'][k4]*100:5.1f}%  | self misses {r['missed']['self'][k4]*100:5.1f}%")


if __name__ == "__main__":
    main()
