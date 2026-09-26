"""Conceptual figures for the paper: lineages, cone hashes and staleness, the claim
lifecycle, the obligation policy, and the system architecture."""

from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from common import save_fig, style, BLUE, ORANGE, AQUA, YELLOW, ORDINAL4, GOOD, CRITICAL, INK, INK2, MUTED
from assay.obligation import LEVEL_NAMES, obligation_level


def box(ax, x, y, w, h, text, fc, ec=None, fs=8, tc=INK, weight="normal", radius=0.02, lw=0.8):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0.008,rounding_size={radius}", fc=fc, ec=ec or fc, lw=lw)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=tc, fontweight=weight, linespacing=1.25)
    return p


def arrow(ax, a, b, color=MUTED, lw=1.2, style="-|>", rad=0.0, ls="-"):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=11, color=color, lw=lw,
                                 connectionstyle=f"arc3,rad={rad}", linestyle=ls, shrinkA=2, shrinkB=2))


def lab(ax, x, y, t, color=INK2, fs=6.6):
    ax.text(x, y, t, fontsize=fs, color=color, ha="center", va="center", bbox=dict(fc="white", ec="none", pad=1.2))


def fig_lineages():
    plt = style()
    fig, ax = plt.subplots(figsize=(7.4, 3.3))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    box(ax, 0.02, 0.58, 0.27, 0.36, "Context tools\n(repo maps, indexes)\nwhat the agent KNOWS\n\nmodules, exports, dependencies,\nactivity, hints", "#e3eefb", BLUE, fs=7.4)
    box(ax, 0.71, 0.58, 0.27, 0.36, "Governance frameworks\n(contracts, doer/reviewer pairs)\nwhat the agent must PROVE\n\nstates, gates, verdicts,\nrole boundaries, escalation", "#fdeae2", ORANGE, fs=7.4)
    box(ax, 0.30, 0.08, 0.40, 0.32, "Evidence graph\nclaims bound to Merkle cone hashes\n\nstale exactly when the covered code\nor anything it depends on changes", "#e3f6ee", AQUA, fs=8, weight="bold")
    arrow(ax, (0.22, 0.58), (0.42, 0.40), BLUE, 1.6)
    arrow(ax, (0.78, 0.58), (0.58, 0.40), ORANGE, 1.6)
    ax.text(0.50, 0.92, "same object, two timescales", ha="center", fontsize=8.2, color=INK2, style="italic")
    ax.text(0.50, 0.84, "now: what is true of the code", ha="center", fontsize=6.3, color=MUTED)
    ax.text(0.50, 0.78, "then: what was verified, at which hash, by whom", ha="center", fontsize=6.3, color=MUTED)
    arrow(ax, (0.31, 0.70), (0.69, 0.70), MUTED, 0.8, style="<|-|>")
    ax.text(0.50, 0.01, "outputs: situation brief (tokens), signed ledger (audit), mechanical gate (merge)", ha="center", fontsize=7.4, color=INK2)
    save_fig(fig, "f1_lineages")


def fig_cone():
    plt = style()
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.3))
    pos = {"core": (0.5, 0.12), "service": (0.5, 0.40), "app": (0.5, 0.68), "tests": (0.25, 0.92), "util": (0.05, 0.55)}
    edges = [("service", "core"), ("app", "service"), ("tests", "app"), ("tests", "util")]
    for ax, changed in zip(axes, (set(), {"core"})):
        ax.axis("off")
        ax.set_xlim(-0.12, 1.08)
        ax.set_ylim(-0.02, 1.08)
        stale = {"core", "service", "app", "tests"} if changed else set()
        for a, b in edges:
            arrow(ax, pos[a], pos[b], MUTED, 1.0)
        for n, (x, y) in pos.items():
            if n in changed:
                fc, ec, tc = ORANGE, ORANGE, "white"
            elif n in stale:
                fc, ec, tc = "#fdeae2", ORANGE, INK
            else:
                fc, ec, tc = "#e3eefb", BLUE, INK
            ax.add_patch(plt.Circle((x, y), 0.075, fc=fc, ec=ec, lw=1.4))
            ax.text(x, y, n, ha="center", va="center", fontsize=7.4, color=tc, fontweight="bold")
        tags = {"app": ("claim c-1: tests-pass\nbound to cone(app)", 0.66, 0.68), "util": ("claim c-2: tests-pass\nbound to cone(util)", -0.10, 0.28)}
        for n, (t, x, y) in tags.items():
            is_stale = n in stale
            box(ax, x, y - 0.08, 0.40, 0.16, t + ("\nSTALE" if is_stale else "\nfresh"), "#fff4f2" if is_stale else "#eef8f3",
                CRITICAL if is_stale else GOOD, fs=6.3, tc=CRITICAL if is_stale else "#006300")
        ax.text(0.48, 1.05, "before: two claims, both fresh" if not changed else "after editing core: cone(app) changed, cone(util) did not",
                ha="center", fontsize=8, color=INK2)
    fig.text(0.5, 0.02, "cone(m) = m plus everything it depends on; the cone hash is a Merkle hash over that set.   "
             "Blast radius of {core} = {core, service, app, tests} = exactly the claims that went stale.",
             ha="center", fontsize=6.8, color=MUTED)
    fig.subplots_adjust(bottom=0.10, top=0.95, left=0.02, right=0.98, wspace=0.05)
    save_fig(fig, "f2_cone")


def fig_lifecycle():
    plt = style()
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    W, H = 0.19, 0.12
    S = {
        "attested": (0.03, 0.74, "#e3eefb", BLUE), "contested": (0.405, 0.74, "#fdeae2", ORANGE), "accepted": (0.78, 0.74, "#eef8f3", GOOD),
        "stale": (0.03, 0.10, "#f0efec", MUTED), "escalated": (0.405, 0.42, "#fff7e0", YELLOW), "refuted": (0.405, 0.10, "#fff4f2", CRITICAL),
        "superseded": (0.78, 0.10, "#f0efec", MUTED),
    }
    for name, (x, y, fc, ec) in S.items():
        label = name if name != "contested" else "contested\n(up to 3 rounds)"
        box(ax, x, y, W, H, label, fc, ec, fs=8 if name != "contested" else 7.4, weight="bold")

    def c(name, dx=0.5, dy=0.5):
        x, y, _, _ = S[name]
        return (x + W * dx, y + H * dy)

    arrow(ax, c("attested", 0.6, 1), c("accepted", 0.4, 1), GOOD, 1.3, rad=-0.28); lab(ax, 0.50, 0.985, "accept: fresh claim, independent reviewer")
    arrow(ax, c("attested", 1, 0.75), c("contested", 0, 0.75), ORANGE, 1.3); lab(ax, 0.31, 0.86, "counter")
    arrow(ax, c("contested", 1, 0.5), c("accepted", 0, 0.5), GOOD, 1.3); lab(ax, 0.69, 0.76, "contest, then accept")
    arrow(ax, c("contested", 0.5, 0), c("escalated", 0.5, 1), YELLOW, 1.3); lab(ax, 0.565, 0.64, "3 rounds")
    arrow(ax, c("contested", 1, 0.1), c("superseded", 0.15, 1), MUTED, 1.3); lab(ax, 0.86, 0.46, "accept-finding\n+ new claim")
    arrow(ax, c("attested", 1, 0.45), c("escalated", 0, 0.75), YELLOW, 1.3); lab(ax, 0.33, 0.66, "escalate")
    arrow(ax, c("attested", 1, 0.15), c("refuted", 0, 0.6), CRITICAL, 1.3, rad=0.25); lab(ax, 0.20, 0.36, "refute")
    arrow(ax, c("escalated", 0.5, 0), c("refuted", 0.5, 1), CRITICAL, 1.3); lab(ax, 0.565, 0.32, "human refute")
    arrow(ax, c("escalated", 1, 0.8), c("accepted", 0.5, 0), GOOD, 1.3); lab(ax, 0.80, 0.60, "human accept")
    arrow(ax, c("attested", 0.3, 0), c("stale", 0.3, 1), MUTED, 1.1, ls="--"); lab(ax, 0.045, 0.48, "drift")
    arrow(ax, c("stale", 0.7, 1), c("attested", 0.7, 0), BLUE, 1.1, ls="--"); lab(ax, 0.215, 0.48, "re-attest")
    ax.text(0.5, 0.005, "Solid arrows are ledger appends; dashed arrows are computed from the index. Any non-terminal claim becomes stale on binding drift "
            "and cannot be accepted until re-attested. Terminal: refuted, superseded.", ha="center", fontsize=6.6, color=INK2)
    save_fig(fig, "f3_lifecycle")


def fig_policy():
    plt = style()
    import numpy as np
    fig, ax = plt.subplots(figsize=(4.6, 3.3))
    ax.grid(False)
    xs = np.linspace(0, 1, 101)
    ys = np.linspace(0, 1, 101)
    Z = np.zeros((len(ys), len(xs)))
    N = 100
    for i, r in enumerate(ys):
        for j, f in enumerate(xs):
            Z[i, j] = obligation_level(f, r, max(1, int(round(f * N))))
    from matplotlib.colors import ListedColormap, BoundaryNorm
    cmap = ListedColormap(ORDINAL4)
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], 4)
    ax.imshow(Z, origin="lower", extent=(0, 100, 0, 1), aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")
    ax.set_xlabel("blast radius, % of modules (100-module repository)")
    ax.set_ylabel("max risk in the radius")
    ax.set_title("Obligation level policy")
    for lvl, (x, y) in {0: (1.2, 0.12), 1: (9, 0.45), 2: (26, 0.3), 3: (66, 0.72)}.items():
        ax.text(x, y, f"L{lvl}\n{LEVEL_NAMES[lvl]}", fontsize=7.4, color="white" if lvl >= 1 else INK, ha="left", va="center", fontweight="bold")
    save_fig(fig, "f4_policy")


def fig_architecture():
    plt = style()
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.02, 1)
    box(ax, 0.02, 0.70, 0.16, 0.22, "repository\nsource files\n+ git history", "#f0efec", MUTED, fs=7.4)
    box(ax, 0.24, 0.70, 0.20, 0.22, "indexer\nparse, resolve, Merkle\nskip cache, SCC cone hashes", "#e3eefb", BLUE, fs=7.0)
    box(ax, 0.50, 0.70, 0.16, 0.22, "assay.json\nmodules, files, edges,\nrisk, hints, hashes", "#e3eefb", BLUE, fs=7.0)
    box(ax, 0.72, 0.70, 0.26, 0.22, "brief and MCP tools\nabout 600 tokens: hubs, hot spots,\nradius, obligation, stale claims", "#e3f6ee", AQUA, fs=7.0)
    box(ax, 0.02, 0.26, 0.20, 0.22, "doer agent\nedits, runs evidence\n(assay attest)", "#fdeae2", ORANGE, fs=7.4)
    box(ax, 0.30, 0.26, 0.20, 0.22, "reviewer agent\naccept / counter /\nrefute / escalate", "#fdeae2", ORANGE, fs=7.4)
    box(ax, 0.58, 0.26, 0.18, 0.22, "signed ledger\nclaims, verdicts,\nresponses", "#fff7e0", YELLOW, fs=7.2)
    box(ax, 0.82, 0.26, 0.16, 0.22, "gate\ncoverage, freshness,\nsignatures, review;\nmerge only on PASS", "#eef8f3", GOOD, fs=6.8)
    box(ax, 0.36, 0.00, 0.28, 0.12, "human: direction, escalations, sign-off", "#f0efec", MUTED, fs=7.2)
    arrow(ax, (0.18, 0.81), (0.24, 0.81)); arrow(ax, (0.44, 0.81), (0.50, 0.81)); arrow(ax, (0.66, 0.81), (0.72, 0.81))
    arrow(ax, (0.74, 0.70), (0.12, 0.48), AQUA, 1.1, rad=0.22); arrow(ax, (0.76, 0.70), (0.40, 0.48), AQUA, 1.1, rad=0.12)
    lab(ax, 0.36, 0.60, "read first", AQUA)
    arrow(ax, (0.58, 0.70), (0.67, 0.48), BLUE, 1.0); lab(ax, 0.665, 0.60, "bindings", BLUE)
    arrow(ax, (0.20, 0.26), (0.60, 0.26), ORANGE, 1.2, rad=0.35); lab(ax, 0.40, 0.165, "claims", ORANGE)
    arrow(ax, (0.50, 0.37), (0.58, 0.37), ORANGE, 1.2); lab(ax, 0.54, 0.42, "verdicts", ORANGE)
    arrow(ax, (0.76, 0.37), (0.82, 0.37), YELLOW, 1.2); lab(ax, 0.79, 0.42, "verify", INK2)
    arrow(ax, (0.12, 0.26), (0.36, 0.06), MUTED, 0.9, rad=0.2); arrow(ax, (0.40, 0.26), (0.45, 0.12), MUTED, 0.9)
    lab(ax, 0.20, 0.10, "escalations", MUTED)
    arrow(ax, (0.64, 0.06), (0.90, 0.26), MUTED, 0.9, rad=-0.2); lab(ax, 0.80, 0.09, "human-approved claims", MUTED)
    ax.text(0.5, 0.97, "Assay: one index feeds both the agents' context and the gate's checks", ha="center", fontsize=8.2, color=INK2)
    save_fig(fig, "f5_architecture")


def main():
    print("concept figures")
    fig_lineages()
    fig_cone()
    fig_lifecycle()
    fig_policy()
    fig_architecture()


if __name__ == "__main__":
    main()
