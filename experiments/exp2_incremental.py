"""E2: index build time, cold versus Merkle-skipped warm rebuilds.

  cold        no cache: every file hashed and parsed
  warm no-op  cache hit on every file (only the graph, cone hashes, and git activity recompute)
  warm 1-file one file edited: exactly one file re-hashed and re-parsed

Median of five runs per condition. The file edit is reverted after measurement.
"""

import os
import statistics
import tempfile
import time

from common import corpora, save_fig, save_json, style, SERIES, INK2
from assay.index import build_index
from assay.store import Store


def timed(fn, runs=5):
    ts = []
    stats = None
    for _ in range(runs):
        t0 = time.perf_counter()
        _, stats = fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts), stats


def measure(name, root):
    tmp = tempfile.mkdtemp(prefix="assay-e2-")
    store = Store(root)
    store.state_dir = tmp
    store.cache_path = os.path.join(tmp, "cache.json")
    cold_s, st = timed(lambda: build_index(root, store=store, use_cache=False))
    build_index(root, store=store, use_cache=True)  # prime the cache
    warm_s, st_warm = timed(lambda: build_index(root, store=store, use_cache=True))
    idx, _ = build_index(root, store=store, use_cache=True)
    victim = max(idx["files"], key=lambda f: idx["files"][f]["lines"])
    path = os.path.join(root, victim)
    with open(path, "rb") as fh:
        original = fh.read()
    st_touch = None
    ts = []
    for _ in range(5):
        with open(path, "ab") as fh:
            fh.write(b"\n// assay e2 touch\n" if not victim.endswith(".py") else b"\n# assay e2 touch\n")
        time.sleep(0.002)
        t0 = time.perf_counter()
        _, st_touch = build_index(root, store=store, use_cache=True)   # timed: exactly one file re-parsed
        ts.append(time.perf_counter() - t0)
        with open(path, "wb") as fh:
            fh.write(original)
        build_index(root, store=store, use_cache=True)                 # revert and re-prime, untimed
    touch_s = statistics.median(ts)
    return {
        "corpus": name, "files": st["files"], "modules": st["modules"], "edges": st["edges"],
        "cold_s": round(cold_s, 4), "warm_noop_s": round(warm_s, 4), "warm_1file_s": round(touch_s, 4),
        "warm_reparsed": st_warm["reparsed"], "touch_reparsed": st_touch["reparsed"],
        "speedup_noop": round(cold_s / max(warm_s, 1e-6), 1), "speedup_1file": round(cold_s / max(touch_s, 1e-6), 1),
    }


def plot(rows):
    plt = style()
    n = len(rows)
    keys = [("cold_s", "Cold (hash + parse every file)"), ("warm_1file_s", "Warm, one file edited"), ("warm_noop_s", "Warm, nothing changed")]
    h = 0.24
    fig, ax = plt.subplots(figsize=(7.2, 0.58 * n + 1.1))
    for j, (key, label) in enumerate(keys):
        ys = [i + (j - 1) * h for i in range(n)]
        vals = [r[key] * 1000 for r in rows]
        ax.barh(ys, vals, height=h - 0.04, color=SERIES[j], label=label, linewidth=0)
        for y, v in zip(ys, vals):
            ax.text(v * 1.1, y, f"{v:,.0f} ms", va="center", ha="left", fontsize=6.8, color=INK2)
    ax.set_xscale("log")
    ax.set_yticks(range(n))
    ax.set_yticklabels([f"{r['corpus']}\n{r['files']} files" for r in rows])
    ax.invert_yaxis()
    ax.set_xlabel("wall time per index build, ms (median of 5), log scale")
    ax.set_xlim(5, max(r["cold_s"] for r in rows) * 1000 * 5)
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right")
    ax.set_title("Incremental indexing: Merkle skip makes rebuilds cheap")
    save_fig(fig, "e2_incremental")


def main():
    rows = []
    for name, root in corpora():
        print(f"E2 {name}")
        rows.append(measure(name, root))
    save_json("e2_incremental", {"rows": rows})
    plot(rows)
    for r in rows:
        print(f"{r['corpus']:10} files {r['files']:5d}  cold {r['cold_s']*1000:8.1f} ms  warm-noop {r['warm_noop_s']*1000:7.1f} ms  warm-1file {r['warm_1file_s']*1000:7.1f} ms  (reparsed {r['touch_reparsed']})")


if __name__ == "__main__":
    main()
