"""Command line interface for Assay."""

import argparse
import json
import os
import subprocess
import sys
from typing import List, Optional

from . import __version__
from .brief import commands_line, make_brief
from .gate import evaluate_gate
from .graph import Graph
from .hooks import install_pre_commit, write_agent_snippet
from .index import build_index, commands_for, diff_index, modules_for_files
from .ledger import Ledger, ProtocolError, PREDICATES
from .obligation import evidence_obligation
from .protocol import attest, respond, review, run_evidence
from .store import Store
from .tokens import estimate_tokens, estimator_name


def _root(args) -> str:
    return os.path.abspath(args.root)


def _load_index(store: Store, args) -> dict:
    if not store.has_index():
        idx, _ = build_index(store.root, depth=getattr(args, "depth", 3), store=store)
        store.save_index(idx)
        return idx
    return store.load_index()


def _agent(args, default_role: str) -> dict:
    agent = getattr(args, "as_agent", None) or os.environ.get("ASSAY_AGENT") or ""
    role = getattr(args, "role", None) or os.environ.get("ASSAY_ROLE") or default_role
    if not agent:
        raise SystemExit("error: identify the agent with --as <name> or ASSAY_AGENT")
    out = {"agent": agent, "role": role}
    model = os.environ.get("ASSAY_MODEL")
    if model:
        out["model"] = model
    return out


def _split(csv: Optional[str]) -> Optional[List[str]]:
    if csv is None:
        return None
    return [x.strip() for x in csv.split(",") if x.strip()]


def _changed(store: Store, index: dict, args) -> Optional[List[str]]:
    if getattr(args, "changed", None):
        return _split(args.changed)
    if getattr(args, "files", None):
        return modules_for_files(index, _split(args.files))
    return None


# ------------------------------------------------------------------------------ commands

def cmd_init(args) -> int:
    store = Store(_root(args))
    store.ensure()
    idx, stats = build_index(store.root, depth=args.depth, store=store)
    store.save_index(idx)
    p = idx["project"]
    print(f"assay: indexed {p['files']} files, {p['modules']} modules, {stats['edges']} edges in {stats['total_s']}s")
    print(f"  root hash {idx['root_hash'][:12]}  commands: {commands_line(idx)}  frameworks: {', '.join(p['frameworks']) or 'none'}")
    print(f"  index {store.index_path} ({estimate_tokens(json.dumps(idx))} tokens, {estimator_name()})")
    if args.hook:
        try:
            print("  hook   " + install_pre_commit(store.root))
        except FileNotFoundError as exc:
            print(f"  hook   skipped: {exc}")
    if args.agents:
        print("  agents " + write_agent_snippet(store.root, index=idx))
    return 0


def cmd_index(args) -> int:
    store = Store(_root(args))
    depth = args.depth
    if store.has_index() and not args.depth_set:
        depth = store.load_index().get("module_depth", depth)
    idx, stats = build_index(store.root, depth=depth, store=store, use_cache=not args.no_cache)
    old_root = store.load_index().get("root_hash") if store.has_index() else None
    if old_root == idx["root_hash"] and not args.force:
        if not args.quiet:
            print(f"assay: index unchanged (root {old_root[:12]}), {stats['cached']} cached, {stats['reparsed']} reparsed, {stats['total_s']}s")
        return 0
    store.save_index(idx)
    if not args.quiet:
        print(f"assay: index updated (root {idx['root_hash'][:12]}), {stats['cached']} cached, {stats['reparsed']} reparsed, {stats['total_s']}s")
    if args.stats:
        print(json.dumps(stats))
    return 0


def cmd_brief(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    text = make_brief(store, idx, changed=_changed(store, idx, args), budget_tokens=args.budget, base=args.base)
    print(text)
    if args.show_tokens:
        print(f"\n[{estimate_tokens(text)} tokens, {estimator_name()}]", file=sys.stderr)
    return 0


def cmd_modules(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    rows = sorted(idx["modules"].items(), key=lambda kv: (-len(kv[1]["depended_by"]), kv[0]))
    print(f"{'module':40} {'files':>5} {'lines':>7} {'fan-in':>6} {'fan-out':>7} {'risk':>5}  purpose")
    for m, v in rows:
        print(f"{m[:40]:40} {len(v['files']):5d} {v['lines']:7d} {len(v['depended_by']):6d} {len(v['depends_on']):7d} {v['risk']:5.2f}  {v['purpose'][:50]}")
    return 0


def cmd_module(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    m = idx["modules"].get(args.path)
    if m is None:
        print(f"unknown module {args.path}", file=sys.stderr)
        return 1
    print(json.dumps({args.path: dict(m, commands=commands_for(idx, args.path))}, indent=1))
    return 0


def cmd_find(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    q = args.query.lower()
    for m, v in idx["modules"].items():
        hit_exports = [e for e in v["exports"] if q in e.lower()]
        if q in m.lower() or q in v["purpose"].lower() or hit_exports:
            print(f"{m}: {v['purpose'][:60]}" + (f"  exports: {', '.join(hit_exports[:5])}" if hit_exports else ""))
    return 0


def cmd_blast(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    ob = evidence_obligation(idx, _split(args.modules), Graph.from_index(idx))
    if args.json:
        print(json.dumps(ob, indent=1))
        return 0
    print(f"changed: {', '.join(ob['changed']) or '(none known)'}")
    print(f"blast radius: {ob['br_size']}/{len(idx['modules'])} modules ({ob['br_fraction']:.0%}); max risk {ob['max_risk']:.2f} ({', '.join(ob['riskiest'])})")
    print(f"obligation: level {ob['level']} ({ob['level_name']})")
    for r in ob["required"]:
        print(f"  - {r['predicate']} [{r['scope']}] on {', '.join(r['subjects'])}" + (f"  ({r['reason']})" if r.get("reason") else ""))
    extra = [m for m in ob["blast_radius"] if m not in ob["changed"]]
    if extra:
        print("dependents: " + ", ".join(extra))
    return 0


def cmd_obligation(args) -> int:
    args.modules = args.changed or ""
    store = Store(_root(args))
    idx = _load_index(store, args)
    changed = _changed(store, idx, args)
    if changed is None:
        g = evaluate_gate(store, idx, base=args.base)
        changed = g["changed"]
    args.modules = ",".join(changed)
    return cmd_blast(args)


def cmd_attest(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    who = _agent(args, "doer")
    try:
        e = attest(store, idx, args.predicate, _split(args.subject) or [], who, command=args.run,
                   statement=args.statement or "", scope=args.scope, supersedes=args.supersedes, note=args.note or "",
                   timeout=args.timeout)
    except (ProtocolError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ev = e["evidence"]
    ok = ev.get("exit_code") == 0
    print(f"{e['id']}: {e['predicate']} on {', '.join(e['subjects'])} [{e['scope']}] -> {'evidence ok' if ok else 'EVIDENCE FAILED (exit ' + str(ev.get('exit_code')) + ')'}")
    if ev.get("command"):
        print(f"  ran: {ev['command']}  ({ev.get('duration_s')}s, output sha256 {ev['output_hash'][:12]})")
    if not ok and ev.get("output_tail"):
        print("  tail: " + ev["output_tail"].strip().splitlines()[-1][:160] if ev["output_tail"].strip() else "")
    return 0 if ok else 1


def cmd_claims(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    led = Ledger(store)
    rows = led.table(idx)
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    print(f"{'id':8} {'predicate':18} {'status':10} {'fresh':5} {'rounds':6} {'attestor':14} subjects")
    for r in rows:
        if not args.all and r["status"] in ("superseded", "refuted"):
            continue
        print(f"{r['id']:8} {r['predicate']:18} {r['status']:10} {'yes' if r['fresh'] else 'NO':5} {r['rounds']:6d} {str(r['attestor'])[:14]:14} {', '.join(r['subjects'])[:60]}" + ("  TAMPERED" if r["tampered"] else ""))
    if led.tampered:
        print(f"LEDGER INTEGRITY FAILURE: {', '.join(led.tampered)}", file=sys.stderr)
        return 1
    return 0


def cmd_claim(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    led = Ledger(store)
    c = led.claim(args.id)
    if c is None:
        print(f"unknown claim {args.id}", file=sys.stderr)
        return 1
    print(json.dumps({"claim": c, "status": led.status(c["id"]), "fresh": led.is_fresh(c, idx),
                      "stale_subjects": led.stale_subjects(c, idx), "verdicts": led.verdicts_for(c["id"]),
                      "responses": led.responses_for(c["id"])}, indent=1))
    return 0


def cmd_review(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    who = _agent(args, "reviewer")
    try:
        e = review(store, idx, args.id, args.decision, args.reason or "", who)
    except ProtocolError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"{e['id']}: {e['decision']} on {args.id} -> status {Ledger(store).status(args.id)}")
    return 0


def cmd_respond(args) -> int:
    store = Store(_root(args))
    who = _agent(args, "doer")
    try:
        e = respond(store, args.id, args.action, args.reason or "", who)
    except ProtocolError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"{e['id']}: {e['action']} on {args.id}")
    return 0


def cmd_gate(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    g = evaluate_gate(store, idx, changed=_changed(store, idx, args), base=args.base)
    if args.json:
        print(json.dumps(g, indent=1))
        return 0 if g["passed"] else 1
    print(f"gate: {'PASS' if g['passed'] else 'FAIL'} | changed {', '.join(g['changed']) or '(none)'} | radius {len(g['blast_radius'])}/{len(idx['modules'])} | level {g['level']} ({g['level_name']})")
    for r in g["rows"]:
        mark = "ok " if r["status"] == "ok" else "!! "
        print(f"  {mark}{r['predicate']:16} {r['subject']:32} {r['status']:10} {r['claim'] or '-':8} {r['note']}")
    if g["tampered"]:
        print(f"  !! ledger integrity failure: {', '.join(g['tampered'])}")
    return 0 if g["passed"] else 1


def cmd_verify(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    led = Ledger(store)
    stale = [c["id"] for c in led.claims() if not led.is_fresh(c, idx) and led.status(c["id"]) not in ("refuted", "superseded")]
    print(f"ledger: {len(led.entries)} entries, {len(led.claims())} claims, {len(led.tampered)} tampered, {len(stale)} stale")
    for cid in stale:
        c = led.claim(cid)
        print(f"  stale {cid} {c['predicate']} drift in {', '.join(led.stale_subjects(c, idx))}")
    return 1 if led.tampered or (args.strict and stale) else 0


def cmd_diff(args) -> int:
    store = Store(_root(args))
    new = _load_index(store, args)
    base = args.base or "HEAD"
    try:
        raw = subprocess.run(["git", "-C", store.root, "show", f"{base}:assay.json"], capture_output=True, check=True).stdout
        old = json.loads(raw.decode("utf-8"))
    except (subprocess.CalledProcessError, ValueError):
        print(f"no assay.json at {base}", file=sys.stderr)
        return 1
    d = diff_index(old, new)
    print(json.dumps(d, indent=1))
    return 0


def cmd_export(args) -> int:
    store = Store(_root(args))
    idx = _load_index(store, args)
    lines = ["# Dependencies", "", "Generated by Assay. Edges point from a module to what it depends on.", "", "```mermaid", "graph LR"]
    ids = {m: f"n{i}" for i, m in enumerate(sorted(idx["modules"]))}
    for m, i in ids.items():
        lines.append(f"  {i}[\"{m}\"]")
    for a, b in idx["edges"]:
        lines.append(f"  {ids[a]} --> {ids[b]}")
    lines.append("```")
    out = args.output or os.path.join(store.root, "DEPENDENCIES.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(out)
    return 0


def cmd_hook(args) -> int:
    store = Store(_root(args))
    if args.action == "install":
        print(install_pre_commit(store.root))
    return 0


def cmd_setup(args) -> int:
    store = Store(_root(args))
    idx = store.load_index() if store.has_index() else None
    print(write_agent_snippet(store.root, args.file, index=idx))
    return 0


def cmd_mcp(args) -> int:
    from .mcp import serve
    serve(_root(args))
    return 0


def cmd_run(args) -> int:
    ev = run_evidence(args.command, _root(args))
    print(json.dumps(ev, indent=1))
    return ev["exit_code"]


# ------------------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="assay", description="Assay: content-addressed evidence graphs. Claims that decay with the code.")
    p.add_argument("--root", default=".", help="repository root (default: current directory)")
    p.add_argument("--version", action="version", version=f"assay {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="build the index and set up .assay/")
    s.add_argument("--depth", type=int, default=3, help="directory depth that defines a module (default 3)")
    s.add_argument("--hook", action="store_true", help="install the pre-commit hook")
    s.add_argument("--agents", action="store_true", help="append Assay instructions to AGENTS.md")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("index", help="rebuild the index incrementally")
    s.add_argument("--depth", type=int, default=3)
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--force", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--stats", action="store_true")
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("brief", help="print the situation brief")
    s.add_argument("--changed", help="comma separated changed modules")
    s.add_argument("--files", help="comma separated changed files")
    s.add_argument("--base", help="git ref to diff against")
    s.add_argument("--budget", type=int, default=600, help="token budget (default 600)")
    s.add_argument("--show-tokens", action="store_true")
    s.set_defaults(fn=cmd_brief)

    s = sub.add_parser("modules", help="list modules"); s.set_defaults(fn=cmd_modules)
    s = sub.add_parser("module", help="show one module"); s.add_argument("path"); s.set_defaults(fn=cmd_module)
    s = sub.add_parser("find", help="search modules"); s.add_argument("query"); s.set_defaults(fn=cmd_find)

    s = sub.add_parser("blast", help="blast radius and obligation for modules")
    s.add_argument("modules", help="comma separated modules")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_blast)

    s = sub.add_parser("obligation", help="obligation for the current change")
    s.add_argument("--changed"); s.add_argument("--files"); s.add_argument("--base"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_obligation)

    s = sub.add_parser("attest", help="record an evidence-backed claim")
    s.add_argument("predicate", help="one of " + ", ".join(PREDICATES))
    s.add_argument("--subject", required=True, help="comma separated subject modules")
    s.add_argument("--run", help="command whose exit code and output are the evidence")
    s.add_argument("--statement", help="what is claimed, in one sentence")
    s.add_argument("--note", help="evidence note for predicates without a command (for example human-approved)")
    s.add_argument("--scope", default="cone", choices=["cone", "self", "repo"])
    s.add_argument("--supersedes", help="claim id this one replaces")
    s.add_argument("--as", dest="as_agent", help="agent identity (or ASSAY_AGENT)")
    s.add_argument("--role", help="doer (default) or other role (or ASSAY_ROLE)")
    s.add_argument("--timeout", type=int, default=900)
    s.set_defaults(fn=cmd_attest)

    s = sub.add_parser("claims", help="list claims"); s.add_argument("--all", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_claims)
    s = sub.add_parser("claim", help="show one claim"); s.add_argument("id"); s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("review", help="issue a verdict on a claim")
    s.add_argument("id"); s.add_argument("decision", choices=["accept", "counter", "refute", "escalate"])
    s.add_argument("--reason"); s.add_argument("--as", dest="as_agent"); s.add_argument("--role", choices=["reviewer", "human"])
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("respond", help="respond to a counter finding")
    s.add_argument("id"); s.add_argument("action", choices=["accept-finding", "contest"])
    s.add_argument("--reason"); s.add_argument("--as", dest="as_agent"); s.add_argument("--role")
    s.set_defaults(fn=cmd_respond)

    s = sub.add_parser("gate", help="evaluate the mechanical merge gate")
    s.add_argument("--changed"); s.add_argument("--files"); s.add_argument("--base"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("verify", help="check ledger integrity and freshness"); s.add_argument("--strict", action="store_true"); s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("diff", help="modules changed since a git ref's assay.json"); s.add_argument("--base"); s.set_defaults(fn=cmd_diff)
    s = sub.add_parser("export", help="write DEPENDENCIES.md (mermaid)"); s.add_argument("--output"); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("hook", help="git hooks"); s.add_argument("action", choices=["install"]); s.set_defaults(fn=cmd_hook)
    s = sub.add_parser("setup", help="write agent instructions"); s.add_argument("--file", default="AGENTS.md"); s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("mcp", help="serve the MCP stdio server"); s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("run", help="run a command and print its evidence record"); s.add_argument("command"); s.set_defaults(fn=cmd_run)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.depth_set = "--depth" in (argv if argv is not None else sys.argv[1:])
    if not hasattr(args, "depth"):
        args.depth = 3
    return args.fn(args)
