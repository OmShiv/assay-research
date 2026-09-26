"""The mechanical merge gate.

The gate never asks a model anything. It computes the obligation for the change, then
checks the ledger for a fresh, independently accepted claim for every (predicate, subject)
pair the obligation requires, and verifies the ledger's signatures. It returns a row per
requirement so an agent or a CI job can see exactly what is missing, stale, unreviewed,
failed, or refuted.
"""

import os
import subprocess
from typing import Dict, List, Optional

from .graph import Graph
from .index import commands_for, modules_for_files
from .ledger import Ledger
from .obligation import evidence_obligation
from .store import Store

_JS = ("javascript", "typescript")
# runner marker -> languages it can test; None means any language (a repository-level target)
RUNNER_LANGS = {
    "pytest": ("python",), "unittest": ("python",), "tox": ("python",), "nox": ("python",),
    "npm test": _JS, "yarn test": _JS, "pnpm test": _JS, "bun test": _JS, "deno test": _JS,
    "jest": _JS, "vitest": _JS, "mocha": _JS, "ava": _JS, "playwright test": _JS, "cypress run": _JS,
    "go test": ("go",), "cargo test": ("rust",), "gradle test": ("java", "kotlin"), "mvn test": ("java", "kotlin"),
    "rspec": ("ruby",), "phpunit": ("php",), "dotnet test": ("csharp",), "swift test": ("swift",),
    "ctest": ("c", "cpp"), "make test": None,
}
TEST_RUNNERS = tuple(RUNNER_LANGS)
TYPECHECKERS = ("tsc", "typecheck", "mypy", "pyright", "go vet", "cargo check", "flow check", "javac", "kotlinc",
                "make check", "make lint")
BUILDERS = ("build", "tsc", "go build", "cargo", "make", "gradle", "mvn", "dotnet", "webpack", "esbuild", "vite",
            "rollup", "swift build", "cmake", "ninja")


def changed_files_from_git(root: str, base: Optional[str]) -> List[str]:
    files: List[str] = []
    try:
        if base:
            mb = subprocess.run(["git", "-C", root, "merge-base", base, "HEAD"], capture_output=True, timeout=30)
            ref = mb.stdout.decode().strip() if mb.returncode == 0 else base
            out = subprocess.run(["git", "-C", root, "diff", "--name-only", ref], capture_output=True, timeout=30).stdout
            files.extend(out.decode("utf-8", errors="replace").split())
        out = subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=all"], capture_output=True, timeout=30).stdout
        for line in out.decode("utf-8", errors="replace").splitlines():
            if len(line) > 3:
                files.append(line[3:].strip().split(" -> ")[-1])
    except (OSError, subprocess.TimeoutExpired):
        pass
    return sorted(set(f for f in files if f))


def default_base(root: str) -> Optional[str]:
    for cand in ("origin/main", "main", "origin/master", "master"):
        r = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", cand], capture_output=True)
        if r.returncode == 0:
            return cand
    return None


def evidence_is_weak(claim: dict, index: dict, ledger: Optional[Ledger] = None) -> Optional[str]:
    """Cheap plausibility checks on evidence. Not a proof of adequacy, only a floor.

    Three checks: the command must look like a test runner; it must have produced
    output; and evidence monotonicity: the number of tests that ran must not drop
    below what earlier evidence on an overlapping subject reported, unless a fresh
    human-approved claim covers the subject. The last check is the mechanical
    counterpart of "do not delete the failing test".
    """
    ev = claim.get("evidence") or {}
    cmd = (ev.get("command") or "").lower()
    if claim["predicate"] == "typecheck-clean":
        return None if any(t in cmd for t in TYPECHECKERS) else "command does not look like a type checker"
    if claim["predicate"] == "build-ok":
        return None if any(b in cmd for b in BUILDERS) else "command does not look like a build"
    if claim["predicate"] != "tests-pass":
        return None
    matched = [r for r in TEST_RUNNERS if r in cmd]
    if not matched:
        return "command does not look like a test runner"
    if ev.get("output_bytes", 0) < 20:
        return "test runner produced almost no output"
    # the runner must be able to test the language of every subject (a pytest run says
    # nothing about a TypeScript module)
    for subject in claim["subjects"]:
        lang = commands_for(index, subject).get("lang")
        if not lang:
            continue
        if not any(RUNNER_LANGS[r] is None or lang in RUNNER_LANGS[r] for r in matched):
            return f"runner '{matched[0]}' does not test {lang} ({subject})"
    cur = ev.get("tests_passed")
    if cur is None or ledger is None:
        return None
    subjects = set(claim["subjects"])
    prior_best = None
    for c in ledger.claims():
        if c["id"] == claim["id"]:
            break
        if c["predicate"] != "tests-pass" or not (set(c["subjects"]) & subjects):
            continue
        if ledger.status(c["id"]) == "refuted":
            continue
        n = (c.get("evidence") or {}).get("tests_passed")
        if n is not None:
            prior_best = n if prior_best is None else max(prior_best, n)
    if prior_best is not None and cur < prior_best:
        for h in ledger.claims():
            if h["predicate"] == "human-approved" and (set(h["subjects"]) & subjects) \
                    and ledger.is_fresh(h, index) and ledger.status(h["id"]) in ("attested", "accepted"):
                return None
        return f"test count dropped from {prior_best} to {cur}; needs human-approved on the subject"
    return None


def evaluate_gate(store: Store, index: dict, changed: Optional[List[str]] = None, base: Optional[str] = None,
                  ledger: Optional[Ledger] = None) -> Dict:
    ledger = ledger or Ledger(store)
    graph = Graph.from_index(index)
    if changed is None:
        base = base or default_base(store.root)
        files = changed_files_from_git(store.root, base)
        changed = modules_for_files(index, files)
    obligation = evidence_obligation(index, changed, graph)
    rows: List[dict] = []
    for req in obligation["required"]:
        pred = req["predicate"]
        for subject in req["subjects"]:
            best = None
            for c in ledger.claims():
                if c.get("_tampered") or c["predicate"] != pred or subject not in c["subjects"]:
                    continue
                st = ledger.status(c["id"])
                fresh = ledger.is_fresh(c, index)
                weak = evidence_is_weak(c, index, ledger)
                if st == "accepted" and fresh and not weak:
                    verdict = "ok"
                elif st == "accepted" and fresh and weak:
                    verdict = "weak"
                elif st == "accepted" and not fresh:
                    verdict = "stale"
                elif st in ("attested", "contested") and fresh:
                    verdict = "unreviewed"
                elif st in ("attested", "contested") and not fresh:
                    verdict = "stale"
                elif st == "failed":
                    verdict = "failed"
                elif st == "escalated":
                    verdict = "escalated"
                elif st == "refuted":
                    verdict = "refuted"
                else:
                    continue
                rank = {"ok": 0, "weak": 1, "unreviewed": 2, "stale": 3, "escalated": 4, "failed": 5, "refuted": 6}[verdict]
                if best is None or rank < best[0]:
                    best = (rank, verdict, c["id"], weak)
            reason = req.get("reason", "")
            if best is None:
                rows.append({"predicate": pred, "subject": subject, "status": "missing", "claim": None, "note": reason})
            else:
                note = "; ".join(n for n in (best[3], reason) if n)
                rows.append({"predicate": pred, "subject": subject, "status": best[1], "claim": best[2], "note": note})
    tampered = list(ledger.tampered)
    passed = not tampered and all(r["status"] == "ok" for r in rows)
    return {
        "passed": passed,
        "changed": obligation["changed"],
        "level": obligation["level"],
        "level_name": obligation["level_name"],
        "blast_radius": obligation["blast_radius"],
        "br_fraction": obligation["br_fraction"],
        "max_risk": obligation["max_risk"],
        "rows": rows,
        "tampered": tampered,
        "base": base,
        "summary": {s: sum(1 for r in rows if r["status"] == s) for s in
                    ("ok", "missing", "stale", "unreviewed", "weak", "failed", "refuted", "escalated")},
    }
