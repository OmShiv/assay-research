"""Run every experiment and produce every figure used in the paper.

    python experiments/run_all.py            # uses ASSAY_CORPORA or ../../corpora
    ASSAY_CORPORA=/path/to/clones python experiments/run_all.py

Corpora are shallow clones of public repositories (see README, "Reproducing the
evaluation"). Results land in experiments/results/*.json, figures in
experiments/figures and paper/figures.
"""

import time

import common  # noqa: F401  (sets sys.path)
import exp1_tokens
import exp2_incremental
import exp3_staleness
import exp4_gate
import exp5_blast
import figures_concept


def main():
    t0 = time.time()
    print("corpora:", ", ".join(f"{n} ({p})" for n, p in common.corpora()))
    figures_concept.main()
    exp1_tokens.main()
    exp2_incremental.main()
    exp3_staleness.main()
    exp5_blast.main()
    exp4_gate.main()
    common.save_json("summary", {
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": round(time.time() - t0, 1),
        "corpora": [n for n, _ in common.corpora()],
    })
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
