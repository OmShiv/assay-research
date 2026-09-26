# Assay

Claims that decay with the code.

Assay is a content-addressed evidence graph for AI-assisted software delivery. It joins
two things that normally live in separate tools: a compact index of a codebase (what an
agent needs to know) and a ledger of evidence-backed claims with an adversarial review
protocol and a mechanical merge gate (what an agent must prove). One primitive connects
them. Every claim is bound to the Merkle hash of the dependency cone of the code it is
about. When that code, or anything it depends on, changes, the claim goes stale by
construction.

This repository holds the reference implementation, the test suite, the scripts and
results for the five experiments, and the source of the paper.

Zero runtime dependencies. Python 3.9 or later. Works on Python, TypeScript and
JavaScript, Go, Rust, Java and Kotlin, Ruby, and C and C++ repositories.

## The idea in one minute

```
index       assay.json: modules, exports, dependencies, activity, risk, and two hashes per
            module: its content hash and its cone hash (a Merkle hash over everything it
            depends on, computed on the strongly connected components of the import graph)
claim       "tests-pass on pkg/auth", produced by actually running the test command; the
            claim records the command, exit code, output hash, and the cone hashes it was
            bound to at that moment
staleness   a claim is fresh while its bindings match the current index; an edit anywhere
            in the cone invalidates it, and nothing else does (sound and minimal)
radius      the blast radius of a change is the set of modules whose cone hash changed,
            which is the same set as the claims that went stale
obligation  the evidence a change owes grows with its blast radius and the risk inside it
review      an independent reviewer accepts, counters, refutes, or escalates each claim; the
            attestor can accept a finding or contest it by naming the harm; three rounds
            escalate to a human
gate        merges only when every required (predicate, module) pair has a fresh,
            independently accepted claim and every ledger signature verifies; no model is
            consulted
```

## Quick start

```bash
pip install -e .
cd your-repo
assay init --hook --agents      # index, pre-commit hook, AGENTS.md block
assay brief                     # about 600 tokens: hubs, hot spots, radius, obligations
assay blast src/auth            # what a change to src/auth would reach
```

A doer agent makes a change and attests it:

```bash
assay attest tests-pass --subject src/auth,src/api --run "pytest -q" --as doer-1
assay gate --changed src/auth   # FAIL: unreviewed
```

A different agent, or a human, reviews it:

```bash
assay review c-0001 accept --as reviewer-1
assay gate --changed src/auth   # PASS
```

Or they disagree:

```bash
assay review c-0001 counter --reason "no test for the expired-token path" --as reviewer-1
assay respond c-0001 contest --reason "that path is unreachable since the refresh rewrite" --as doer-1
assay review c-0001 accept --as reviewer-1
```

Try to cheat and the ledger refuses:

```bash
assay review c-0001 accept --as doer-1
# refused: separation of duties: an attestor cannot review their own claim
```

Edit `src/core` afterwards and the claim is stale, because `src/auth` depends on it:

```bash
assay index && assay claims
# c-0001  tests-pass  accepted  fresh: NO
assay gate --changed src/core   # FAIL: stale
```

## Commands

| Command | Purpose |
|---|---|
| `assay init [--depth N] [--hook] [--agents]` | Build `assay.json`, create `.assay/`, optionally install the pre-commit hook and the AGENTS.md block |
| `assay index` | Incremental rebuild; unchanged files are skipped through a per-file cache |
| `assay brief [--changed a,b] [--base REF] [--budget N]` | The situation brief an agent reads first |
| `assay modules`, `assay module P`, `assay find Q` | Browse the index |
| `assay blast a,b` | Blast radius and evidence obligation for a hypothetical change |
| `assay obligation [--base REF]` | Obligation for the current working-tree change |
| `assay attest PRED --subject a,b --run CMD --as AGENT` | Run evidence and record a bound claim |
| `assay claims`, `assay claim ID` | Ledger view with derived status and freshness |
| `assay review ID accept\|counter\|refute\|escalate --as AGENT [--role human]` | Verdicts |
| `assay respond ID accept-finding\|contest --reason ... --as AGENT` | Attestor's response to a counter |
| `assay gate [--changed a,b] [--base REF] [--json]` | The mechanical merge gate; exit code 1 on FAIL |
| `assay verify [--strict]` | Ledger signature check and staleness report |
| `assay diff --base REF` | Modules whose hash or cone hash changed since a ref's `assay.json` |
| `assay export` | `DEPENDENCIES.md` with a Mermaid graph |
| `assay mcp` | MCP server over stdio |

Predicates: `tests-pass`, `lint-clean`, `typecheck-clean`, `build-ok`, `behavior-preserved`,
`no-secrets`, `spec-satisfied`, `human-approved`, `custom`. The first four require a command.

Evidence commands are detected per language from the manifests (package scripts and the
package manager's lockfile, `tsconfig.json`, pytest markers, mypy or pyright, `go.mod`,
`Cargo.toml`, Gradle or Maven, a Makefile `test:` target as a repository-wide fallback).
A module whose language has no test runner is asked for `build-ok` and `typecheck-clean`
instead of `tests-pass`, so the gate is never unsatisfiable by construction. Both can be
overridden under a `policy` key in `assay.json`, which survives rebuilds:

```json
"policy": {
  "commands": {"typescript": {"test": "npx vitest run"}},
  "no_runner": ["tests-pass"],
  "standard": {"br_size": 2, "max_risk": 0.3}
}
```

## Using it from an agent

Register the MCP server with whichever client you use, for example:

```bash
claude mcp add assay -- python -m assay --root "$PWD" mcp
```

Any MCP client gets `get_brief`, `get_overview`, `get_module`, `find_module`,
`get_blast_radius`, `list_claims`, `get_claim`, `attest`, `review`, `respond`, `gate`, and
`reindex`. `assay init --agents` appends a block to `AGENTS.md` that tells agents to read
the brief before source files, which evidence command applies to each language, and to
attest every claim.

In continuous integration:

```yaml
- run: pip install -e .
- run: assay index --quiet && git diff --quiet assay.json   # committed index is current
- run: assay gate --base origin/main                        # fresh, reviewed evidence for the radius
```

## What the index looks like

```json
{
  "root_hash": "5d1f...",
  "project": {"name": "shop", "languages": {"python": 40}, "frameworks": ["fastapi"],
              "commands": {"python": {"test": "pytest", "typecheck": "mypy ."}}, "modules": 41},
  "hints": {"most_depended": ["shop/core", "shop/db"], "hottest_90d": ["shop/api/orders"]},
  "modules": {
    "shop/api/orders": {
      "hash": "9b3c...", "cone_hash": "e70a...",
      "purpose": "Order placement and status endpoints",
      "exports": ["router", "place_order()", "OrderOut"],
      "depends_on": ["shop/core", "shop/db", "shop/payments"],
      "depended_by": ["tests/api"],
      "activity": {"commits_90d": 14, "level": "high"},
      "risk": 0.4
    }
  },
  "files": {"shop/api/orders.py": {"hash": "31ab...", "module": "shop/api/orders", "imports": ["shop/db/models.py"]}},
  "edges": [["shop/api/orders", "shop/core"]]
}
```

`risk` is `0.5 * min(1, commits_90d / 20) + 0.5 * min(1, fan_in / 10)`. The scales are
absolute so the number is comparable across repositories.

## What the ledger looks like

`.assay/ledger.jsonl` is append-only. Every line is signed with an HMAC key that lives
outside the repository (`ASSAY_KEY`, or `.assay/key`, which is git-ignored). Status is never
stored. It is derived from the history every time it is needed.

```json
{"kind":"claim","id":"c-0001","predicate":"tests-pass","subjects":["shop/api/orders","tests/api"],
 "scope":"cone","bindings":{"shop/api/orders":"e70a...","tests/api":"1c9d..."},
 "evidence":{"command":"pytest -q","exit_code":0,"output_hash":"7f2e...","tests_passed":212,"duration_s":9.1},
 "attestor":{"agent":"doer-1","role":"doer"},"created_at":"2026-09-08T10:12:44Z","sig":"..."}
{"kind":"verdict","id":"v-0001","claim":"c-0001","decision":"accept","reviewer":{"agent":"reviewer-1","role":"reviewer"},"sig":"..."}
```

## What the gate checks

1. Coverage: every `(predicate, module)` pair the obligation requires has a claim.
2. Freshness: the claim's bindings match the current cone hashes.
3. Signatures: every ledger line verifies. One altered byte fails the gate.
4. Separation of duties: the accepting reviewer is not the attestor (enforced at append time).
5. Exit code: evidence that failed is recorded as a failed claim, never hidden.
6. Plausibility: a `tests-pass` command must look like a test runner that can test the
   subject's language, and it must have produced output.
7. Monotonicity: the number of tests that ran must not drop below earlier evidence on the
   same subject unless a fresh `human-approved` claim covers it. This is the mechanical form
   of "do not delete the failing test".
8. Review protocol: refuted or escalated claims block, and escalations resolve only by a human.

## Threat model

Assay is a floor, not a proof. It guarantees that a merge carries fresh, signed,
independently reviewed evidence for the whole blast radius of the change. It does not
guarantee the evidence is sufficient. A reviewer can accept a weak test suite, and two
models from one provider can share blind spots. The signing key must be held by the
supervisor that runs the agents (set `ASSAY_KEY` in the supervisor's environment, not the
agent's). An agent that can read the key can forge entries. The parsers are regular
expressions for every language except Python, so the import graph under-approximates
dependencies in the presence of dynamic imports, reflection, and generated code. A
tree-sitter backend is a drop-in replacement. The token estimator is tiktoken when
installed, otherwise characters divided by four.

## Experiments

Five scripted experiments, no model in the loop, on shallow 90-day clones of Express,
Flask, Gin, Axum, and FastAPI, plus Assay itself.

| | Question | Script |
|---|---|---|
| E1 | Tokens to orient: full dump, exploration proxy, index, brief | `experiments/exp1_tokens.py` |
| E2 | Rebuild time: cold, one edited file, no change | `experiments/exp2_incremental.py` |
| E3 | Binding strategies: repo hash, self hash, cone hash; cost and missed invalidations | `experiments/exp3_staleness.py` |
| E4 | The gate against a scripted adversarial doer, twelve scenarios | `experiments/exp4_gate.py` |
| E5 | Single-module blast radius and obligation distribution | `experiments/exp5_blast.py` |

To reproduce:

```bash
mkdir corpora && cd corpora
for r in expressjs/express pallets/flask gin-gonic/gin tokio-rs/axum tiangolo/fastapi; do
  git clone --shallow-since="90 days ago" https://github.com/$r.git
done
cd ..
pip install -e ".[experiments]"          # matplotlib, networkx, numpy, tiktoken
ASSAY_CORPORA=$PWD/corpora python experiments/run_all.py
```

Results go to `experiments/results/*.json` and figures to `experiments/figures/`. The
committed results are from clones made on 2026-09-23. Absolute numbers drift with upstream
commits, the ratios do not. `exp4_gate.py` asserts that every scenario ends as designed and
fails loudly otherwise.

## Paper

`paper/assay.tex` is the manuscript. Every number in it is a macro in `paper/numbers.tex`,
and every table and data file under `paper/tables/` is generated by
`paper/make_numbers.py` from `experiments/results/`. Nothing is typed by hand. The concept
figures are TikZ and the E3 chart is PGFPlots reading the generated data files.

```bash
make paper     # regenerate numbers and tables, build paper/assay.pdf (needs tectonic)
make arxiv     # self-contained source bundle, test-compiled, in dist/assay-arxiv.zip
```

## Repository layout

```
src/assay/      the package (index, graph, parsers, ledger, protocol, obligation, gate,
                brief, MCP server, hooks, CLI, store)
tests/          pytest suite
experiments/    E1 to E5, shared helpers, results/ and figures/ (committed)
paper/          assay.tex, refs.bib, make_numbers.py; numbers.tex and tables/ are generated
scripts/        check_dashes.py (style audit), build_paper.sh
assay.json      this repository's own index; CI fails if it is stale
```

```bash
make test      # pytest
make check     # style audit, index freshness, tests (what CI runs)
```

## Lineage

Assay is a synthesis of two ideas from two projects. It reuses code from neither.

* Stacklit (glincker/stacklit): a committed, token-efficient index of a codebase with
  Merkle-skipped regeneration and an MCP server. Assay keeps the shape of the index and adds
  the cone hash, risk, and the brief.
* Liza (liza-mas/liza): accountable multi-agent delivery with a behavioral contract,
  adversarial doer and reviewer pairs, binding verdicts (accept, counter, refute, escalate),
  and invariants enforced in compiled code outside the model. Assay keeps the protocol and
  the "mechanical, not instructional" stance and applies them per claim rather than per
  task.

The new part is the binding: claims addressed by cone hash, so that context, staleness,
blast radius, and obligation are the same computation over one graph.

## License

Apache-2.0. See `LICENSE`.
