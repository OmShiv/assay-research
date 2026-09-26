"""Minimal MCP (Model Context Protocol) server over stdio, newline-delimited JSON-RPC 2.0.

Exposes the index, the brief, blast radius, obligations, the ledger, and the gate as
tools so that any MCP-capable agent (Claude Code, Cursor, and others) can consume the
same artifacts the CLI produces. Attest and review are exposed too; the separation of
duties and every other invariant are enforced by the ledger, not by the transport.
"""

import json
import sys
from typing import Any, Dict, Optional

from . import __version__
from .brief import make_brief
from .gate import evaluate_gate
from .graph import Graph
from .index import build_index, commands_for
from .ledger import Ledger, ProtocolError
from .obligation import evidence_obligation
from .protocol import attest, respond, review
from .store import Store

TOOLS = [
    {"name": "get_brief", "description": "Situation brief for the repository (read this first).",
     "inputSchema": {"type": "object", "properties": {"changed": {"type": "array", "items": {"type": "string"}}, "budget": {"type": "integer"}}}},
    {"name": "get_overview", "description": "Project summary, hints, hubs, hot modules.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_module", "description": "Details of one module.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "find_module", "description": "Search modules by name, purpose, or export.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_blast_radius", "description": "Blast radius and evidence obligation for a set of changed modules.",
     "inputSchema": {"type": "object", "properties": {"changed": {"type": "array", "items": {"type": "string"}}}, "required": ["changed"]}},
    {"name": "list_claims", "description": "Claims with derived status and freshness.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_claim", "description": "One claim with its verdicts and responses.", "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "attest", "description": "Run a command as evidence and record a claim bound to the current cone hashes.",
     "inputSchema": {"type": "object", "properties": {"predicate": {"type": "string"}, "subjects": {"type": "array", "items": {"type": "string"}},
                                                     "command": {"type": "string"}, "statement": {"type": "string"}, "agent": {"type": "string"},
                                                     "role": {"type": "string"}, "scope": {"type": "string"}, "supersedes": {"type": "string"}},
                     "required": ["predicate", "subjects", "agent"]}},
    {"name": "review", "description": "Issue a verdict (accept, counter, refute, escalate) on a claim as an independent reviewer.",
     "inputSchema": {"type": "object", "properties": {"claim": {"type": "string"}, "decision": {"type": "string"}, "reason": {"type": "string"},
                                                     "agent": {"type": "string"}, "role": {"type": "string"}}, "required": ["claim", "decision", "agent"]}},
    {"name": "respond", "description": "Attestor's response to a counter: accept-finding or contest (naming the harm).",
     "inputSchema": {"type": "object", "properties": {"claim": {"type": "string"}, "action": {"type": "string"}, "reason": {"type": "string"}, "agent": {"type": "string"}},
                     "required": ["claim", "action", "agent"]}},
    {"name": "gate", "description": "Evaluate the mechanical merge gate for the current change.",
     "inputSchema": {"type": "object", "properties": {"changed": {"type": "array", "items": {"type": "string"}}, "base": {"type": "string"}}}},
    {"name": "reindex", "description": "Rebuild the index incrementally.", "inputSchema": {"type": "object", "properties": {}}},
]


class Server:
    def __init__(self, root: str):
        self.store = Store(root)
        self._index: Optional[dict] = None

    @property
    def index(self) -> dict:
        if self._index is None:
            if not self.store.has_index():
                idx, _ = build_index(self.store.root, store=self.store)
                self.store.save_index(idx)
            self._index = self.store.load_index()
        return self._index

    def _text(self, obj: Any, is_error: bool = False) -> dict:
        text = obj if isinstance(obj, str) else json.dumps(obj, indent=1)
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    def call(self, name: str, args: Dict[str, Any]) -> dict:
        idx = self.index
        try:
            if name == "get_brief":
                return self._text(make_brief(self.store, idx, changed=args.get("changed"), budget_tokens=int(args.get("budget", 600))))
            if name == "get_overview":
                return self._text({"project": idx["project"], "hints": idx["hints"], "root_hash": idx["root_hash"]})
            if name == "get_module":
                m = idx["modules"].get(args["path"])
                if m is None:
                    return self._text(f"unknown module {args['path']}", is_error=True)
                return self._text(dict(m, commands=commands_for(idx, args["path"])))
            if name == "find_module":
                q = args["query"].lower()
                hits = {m: v["purpose"] for m, v in idx["modules"].items()
                        if q in m.lower() or q in v["purpose"].lower() or any(q in e.lower() for e in v["exports"])}
                return self._text(hits)
            if name == "get_blast_radius":
                return self._text(evidence_obligation(idx, args["changed"], Graph.from_index(idx)))
            if name == "list_claims":
                return self._text(Ledger(self.store).table(idx))
            if name == "get_claim":
                led = Ledger(self.store)
                c = led.claim(args["id"])
                if c is None:
                    return self._text(f"unknown claim {args['id']}", is_error=True)
                return self._text({"claim": c, "status": led.status(c["id"]), "fresh": led.is_fresh(c, idx),
                                   "verdicts": led.verdicts_for(c["id"]), "responses": led.responses_for(c["id"])})
            if name == "attest":
                e = attest(self.store, idx, args["predicate"], args["subjects"], {"agent": args["agent"], "role": args.get("role", "doer")},
                           command=args.get("command"), statement=args.get("statement", ""), scope=args.get("scope", "cone"),
                           supersedes=args.get("supersedes"), note=args.get("statement", ""))
                return self._text({"id": e["id"], "exit_code": e["evidence"].get("exit_code"), "bindings": e["bindings"]})
            if name == "review":
                e = review(self.store, idx, args["claim"], args["decision"], args.get("reason", ""), {"agent": args["agent"], "role": args.get("role", "reviewer")})
                return self._text({"id": e["id"], "decision": e["decision"], "status": Ledger(self.store).status(args["claim"])})
            if name == "respond":
                e = respond(self.store, args["claim"], args["action"], args.get("reason", ""), {"agent": args["agent"], "role": "doer"})
                return self._text({"id": e["id"], "action": e["action"]})
            if name == "gate":
                return self._text(evaluate_gate(self.store, idx, changed=args.get("changed"), base=args.get("base")))
            if name == "reindex":
                new, stats = build_index(self.store.root, depth=idx.get("module_depth", 3), store=self.store)
                self.store.save_index(new)
                self._index = new
                return self._text(stats)
            return self._text(f"unknown tool {name}", is_error=True)
        except (ProtocolError, ValueError, KeyError) as exc:
            return self._text(f"{type(exc).__name__}: {exc}", is_error=True)

    def handle(self, req: dict) -> Optional[dict]:
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                                                            "serverInfo": {"name": "assay", "version": __version__}}}
        if method == "notifications/initialized" or rid is None:
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = req.get("params") or {}
            return {"jsonrpc": "2.0", "id": rid, "result": self.call(params.get("name", ""), params.get("arguments") or {})}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(root: str) -> None:
    server = Server(root)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = server.handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
