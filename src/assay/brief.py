"""The situation brief: what an agent reads before opening a single source file.

It carries the project's shape (hubs, hot spots, test command), the change under way
(changed modules, blast radius, obligation level), the state of the evidence (stale
claims, open findings, missing requirements), and a module directory trimmed to a token
budget. Both the doer and the reviewer start from the same brief.
"""

from typing import List, Optional

from .gate import evaluate_gate
from .graph import Graph
from .index import commands_for_lang
from .ledger import Ledger
from .obligation import evidence_obligation
from .store import Store
from .tokens import estimate_tokens


def _fmt_mod(index: dict, m: str, max_exports: int = 6) -> str:
    v = index["modules"][m]
    ex = ", ".join(v["exports"][:max_exports])
    if len(v["exports"]) > max_exports:
        ex += ", .."
    deps = ", ".join(v["depends_on"][:5]) + (", .." if len(v["depends_on"]) > 5 else "")
    line = f"- {m}: {v['purpose'][:70]}"
    if ex:
        line += f" | exports: {ex}"
    if deps:
        line += f" | deps: {deps}"
    line += f" | fan-in {len(v['depended_by'])}, risk {v['risk']:.2f}"
    return line


def commands_line(index: dict) -> str:
    """Per-language evidence commands, one clause per language: `python: test pytest;
    typescript: typecheck npx tsc --noEmit, build npm run build (no test runner)`."""
    p = index["project"]
    detected = p.get("commands")
    if detected is None:      # index from before per-language detection
        return f"test {p.get('test_command') or 'unknown'}"
    clauses = []
    for lang in [l for l in p.get("languages", {}) if l != "other"][:4]:
        cmds = commands_for_lang(index, lang)
        parts = [f"{k} {cmds[k]}" for k in ("test", "typecheck", "build") if cmds.get(k)]
        if not cmds.get("test"):
            parts.append("no test runner")
        clauses.append(f"{lang}: " + ", ".join(parts))
    return "; ".join(clauses) or "none detected"


def make_brief(store: Optional[Store], index: dict, changed: Optional[List[str]] = None,
               budget_tokens: int = 600, ledger: Optional[Ledger] = None, base: Optional[str] = None) -> str:
    p = index["project"]
    mods = index["modules"]
    graph = Graph.from_index(index)
    langs = ", ".join(f"{k} {v}" for k, v in list(p.get("languages", {}).items())[:3])
    lines: List[str] = []
    lines.append(f"# Assay brief: {p['name']} @ {index['root_hash'][:10]} | {p['modules']} modules, {p['files']} files, {p['lines']} lines | {langs}")
    fw = ", ".join(p.get("frameworks", [])) or "none detected"
    lines.append(f"Commands: {commands_line(index)} | Frameworks: {fw}")
    hubs = index["hints"].get("most_depended", [])[:5]
    if hubs:
        lines.append("Hubs (touch carefully): " + "; ".join(f"{m} (fan-in {len(mods[m]['depended_by'])}, risk {mods[m]['risk']:.2f})" for m in hubs))
    hot = [m for m in index["hints"].get("hottest_90d", [])[:5]]
    if hot:
        lines.append("Hot 90d: " + "; ".join(f"{m} ({mods[m]['activity']['commits_90d']} commits)" for m in hot))

    gate = None
    if store is not None:
        ledger = ledger or Ledger(store)
        gate = evaluate_gate(store, index, changed=changed, base=base, ledger=ledger)
        changed = gate["changed"]
    ob = evidence_obligation(index, changed or [], graph)
    if ob["changed"]:
        lines.append(f"Changed: {', '.join(ob['changed'])} -> blast radius {ob['br_size']}/{p['modules']} modules "
                     f"({ob['br_fraction']:.0%}), max risk {ob['max_risk']:.2f} -> level {ob['level']} ({ob['level_name']})")
        extra = [m for m in ob["blast_radius"] if m not in ob["changed"]]
        if extra:
            lines.append("Dependents in radius: " + ", ".join(extra[:12]) + (", .." if len(extra) > 12 else ""))
        req = "; ".join(f"{r['predicate']}[{', '.join(r['subjects'][:6])}{', ..' if len(r['subjects']) > 6 else ''}]" for r in ob["required"])
        lines.append(f"Required evidence: {req}")
        rv = ob["review"]
        lines.append("Review: independent reviewer" + (", adversarial" if rv["adversarial"] else "") + (", human sign-off" if rv["human_signoff"] else "") + f", max {rv['max_rounds']} rounds")
    else:
        lines.append("Changed: none detected (pass --changed or --base to scope a change)")

    if ledger is not None:
        stale = []
        open_findings = []
        for c in ledger.claims():
            st = ledger.status(c["id"])
            if st in ("refuted", "superseded"):
                continue
            if not ledger.is_fresh(c, index):
                stale.append(f"{c['id']} {c['predicate']}[{', '.join(c['subjects'][:3])}] drift: {', '.join(ledger.stale_subjects(c, index)[:3])}")
            if st == "contested":
                v = ledger.verdicts_for(c["id"])[-1]
                open_findings.append(f"{v['id']} on {c['id']}: {v['reason'][:80]}")
            elif st == "escalated":
                open_findings.append(f"{c['id']} escalated: needs human verdict")
        if stale:
            lines.append("Stale claims: " + " | ".join(stale[:6]))
        if open_findings:
            lines.append("Open findings: " + " | ".join(open_findings[:6]))
        if gate is not None and ob["changed"]:
            missing = [f"{r['predicate']}[{r['subject']}] {r['status']}" for r in gate["rows"] if r["status"] != "ok"]
            lines.append(("Gate: PASS" if gate["passed"] else "Gate: FAIL ") + ("" if gate["passed"] else "; ".join(missing[:8]) + (" .." if len(missing) > 8 else "")))
        if ledger.tampered:
            lines.append(f"LEDGER INTEGRITY FAILURE: {len(ledger.tampered)} altered entries")

    head = "\n".join(lines)
    used = estimate_tokens(head)
    lines.append("Modules (by fan-in):")
    order = sorted(mods, key=lambda m: (-len(mods[m]["depended_by"]), -mods[m]["lines"], m))
    priority = list(dict.fromkeys((ob["changed"] or []) + [m for m in ob["blast_radius"] if m not in (ob["changed"] or [])] + order))
    shown = 0
    for m in priority:
        line = _fmt_mod(index, m)
        cost = estimate_tokens(line)
        if used + cost > budget_tokens:
            break
        lines.append(line)
        used += cost
        shown += 1
    if shown < len(mods):
        lines.append(f"- .. {len(mods) - shown} more modules: `assay module <path>` or the MCP tool get_module")
    return "\n".join(lines)
