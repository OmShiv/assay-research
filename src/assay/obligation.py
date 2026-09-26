"""Risk-proportional evidence obligation.

Given the modules a change touches, compute the blast radius (the modules whose cone hash
changed, which are the changed modules plus their transitive dependents), the maximum
risk inside it, and from those an obligation level with the predicates that must be
attested fresh and accepted before the gate opens.

    level 0  light        one module, low risk         tests-pass on changed
    level 1  standard     small radius or medium risk  tests-pass on the whole blast radius
    level 2  adversarial  wide radius or high risk     + lint-clean, no-secrets on changed
    level 3  escalate     half the repo or a hub       + human-approved on changed

The thresholds are deliberately simple and explicit; they are policy, not truth, and a
repository can override them in assay.json under "policy".

A tests-pass requirement is only issued for a module whose language has a test runner
(index.project.commands, or policy.commands). Where none covers the module the obligation
requires the predicates in policy.no_runner instead, by default build-ok on the cone and
typecheck-clean on the module itself, so the gate is never unsatisfiable by construction.
Set "no_runner": ["tests-pass"] to insist on tests regardless.
"""

from typing import Dict, Iterable, List

from .graph import Graph
from .index import commands_for

LEVEL_NAMES = ["light", "standard", "adversarial", "escalate"]

DEFAULT_POLICY = {
    # a fraction threshold only counts once the radius has a minimum absolute size,
    # so that a two-module change in a five-module repository is not an escalation
    "escalate": {"br_fraction": 0.5, "min_size": 4, "max_risk": 0.85},
    "adversarial": {"br_fraction": 0.2, "min_size": 3, "br_size": 8, "max_risk": 0.6},
    "standard": {"br_size": 2, "max_risk": 0.3},
    # predicates required on a module whose language has no test runner
    "no_runner": ["build-ok", "typecheck-clean"],
    # per-language command overrides: {"typescript": {"test": "npx vitest run"}}
    "commands": {},
}

# the binding scope each predicate is checked at when it stands in for tests-pass
PREDICATE_SCOPE = {
    "tests-pass": "cone", "build-ok": "cone", "behavior-preserved": "cone", "spec-satisfied": "cone",
    "typecheck-clean": "self", "lint-clean": "self", "no-secrets": "self", "human-approved": "cone",
}


def effective_policy(policy: dict = None) -> dict:
    """DEFAULT_POLICY with the repository's overrides on top (shallow, per key)."""
    return {**DEFAULT_POLICY, **(policy or {})}


def obligation_level(br_fraction: float, max_risk: float, br_size: int, policy: dict = None) -> int:
    p = effective_policy(policy)
    e, a, s = p["escalate"], p["adversarial"], p["standard"]
    if (br_fraction >= e["br_fraction"] and br_size >= e["min_size"]) or max_risk >= e["max_risk"]:
        return 3
    if (br_fraction >= a["br_fraction"] and br_size >= a["min_size"]) or br_size >= a["br_size"] or max_risk >= a["max_risk"]:
        return 2
    if br_size >= s["br_size"] or max_risk >= s["max_risk"]:
        return 1
    return 0


def _test_requirements(index: dict, subjects: List[str], policy: dict) -> List[dict]:
    """tests-pass where a runner covers the module; the no_runner predicates elsewhere."""
    covered = [m for m in subjects if commands_for(index, m).get("test")]
    uncovered = [m for m in subjects if m not in covered]
    out: List[dict] = []
    if covered:
        out.append({"predicate": "tests-pass", "subjects": covered, "scope": "cone"})
    if uncovered:
        by_lang: Dict[str, List[str]] = {}
        for m in uncovered:
            by_lang.setdefault(commands_for(index, m).get("lang") or "unknown", []).append(m)
        for lang, mods in sorted(by_lang.items()):
            cmds = commands_for(index, mods[0])
            for pred in policy.get("no_runner") or DEFAULT_POLICY["no_runner"]:
                known = cmds.get(pred.split("-")[0])   # build-ok -> build, typecheck-clean -> typecheck
                reason = f"no test runner for {lang}; {pred} required instead"
                reason += f" (detected: {known})" if known else " (no command detected; pass one with --run)"
                out.append({"predicate": pred, "subjects": mods, "scope": PREDICATE_SCOPE.get(pred, "cone"),
                            "reason": reason})
    return out


def evidence_obligation(index: dict, changed: Iterable[str], graph: Graph = None) -> Dict:
    graph = graph or Graph.from_index(index)
    mods = index["modules"]
    changed = sorted({c for c in changed if c in mods})
    br = sorted(graph.blast_radius(changed))
    n = max(len(mods), 1)
    br_fraction = len(br) / n
    max_risk = max((mods[m].get("risk", 0.0) for m in br), default=0.0)
    policy = effective_policy(index.get("policy"))
    level = obligation_level(br_fraction, max_risk, len(br), policy)
    required: List[dict] = []
    if changed:
        required.extend(_test_requirements(index, changed if level == 0 else br, policy))
        if level >= 2:
            required.append({"predicate": "lint-clean", "subjects": changed, "scope": "self"})
            required.append({"predicate": "no-secrets", "subjects": changed, "scope": "self"})
        if level >= 3:
            required.append({"predicate": "human-approved", "subjects": changed, "scope": "cone"})
    return {
        "changed": changed,
        "blast_radius": br,
        "br_size": len(br),
        "br_fraction": round(br_fraction, 4),
        "max_risk": round(max_risk, 3),
        "riskiest": sorted(br, key=lambda m: -mods[m].get("risk", 0.0))[:3],
        "level": level,
        "level_name": LEVEL_NAMES[level],
        "required": required,
        "review": {
            "independent_reviewer": True,
            "adversarial": level >= 2,
            "human_signoff": level >= 3,
            "max_rounds": 3,
        },
    }
