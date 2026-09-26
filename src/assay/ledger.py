"""Append-only, signed ledger of claims, verdicts, and responses.

A *claim* asserts a predicate about a set of subject modules and carries the evidence
that was produced when the claim was made (the command that ran, its exit code, a hash of
its output) together with the content hashes the claim is bound to. A *verdict* is an
independent reviewer's decision about a claim: accept, counter, refute, or escalate. A
*response* is the attestor's reply to a counter: accept the finding (and supersede the
claim with a new one) or contest it by naming the concrete harm the requested change
would cause.

Claim status is *derived* from the ledger, never stored, so the ledger cannot drift from
its own history. Freshness is derived from the index: a claim is fresh while every binding
still matches the current hash of its subject.
"""

import hashlib
import hmac
import json
import os
import time
from typing import Dict, List, Optional

from .store import Store

MAX_ROUNDS = 3
SCOPES = ("cone", "self", "repo")
DECISIONS = ("accept", "counter", "refute", "escalate")
RESPONSES = ("accept-finding", "contest")
REVIEW_ROLES = ("reviewer", "human")
TERMINAL = ("refuted", "superseded")
PREDICATES = (
    "tests-pass", "lint-clean", "typecheck-clean", "build-ok", "behavior-preserved",
    "no-secrets", "human-approved", "spec-satisfied", "custom",
)


class ProtocolError(Exception):
    """Raised when an append would violate a protocol invariant."""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign(entry: dict, key: bytes) -> str:
    return hmac.new(key, canonical(entry).encode("utf-8"), hashlib.sha256).hexdigest()


def binding_for(index: dict, subject: str, scope: str) -> Optional[str]:
    if scope == "repo":
        return index.get("root_hash")
    mod = index.get("modules", {}).get(subject)
    if mod is None:
        return None
    return mod["cone_hash"] if scope == "cone" else mod["hash"]


class Ledger:
    def __init__(self, store: Store):
        self.store = store
        self.entries: List[dict] = []
        self.tampered: List[str] = []
        self._load()

    # ---- persistence -----------------------------------------------------------------
    def _load(self) -> None:
        self.entries = []
        self.tampered = []
        if not os.path.exists(self.store.ledger_path):
            return
        key = self.store.key()
        with open(self.store.ledger_path, "r", encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    self.tampered.append(f"line {ln}: unparsable")
                    continue
                if not hmac.compare_digest(entry.get("sig", ""), sign(entry, key)):
                    self.tampered.append(entry.get("id", f"line {ln}"))
                    entry["_tampered"] = True
                self.entries.append(entry)

    def _append(self, entry: dict) -> dict:
        self.store.ensure()
        entry["sig"] = sign(entry, self.store.key())
        with open(self.store.ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        self.entries.append(entry)
        return entry

    def _next_id(self, prefix: str) -> str:
        n = sum(1 for e in self.entries if e.get("id", "").startswith(prefix + "-"))
        return f"{prefix}-{n + 1:04d}"

    # ---- queries ---------------------------------------------------------------------
    def claims(self) -> List[dict]:
        return [e for e in self.entries if e.get("kind") == "claim"]

    def claim(self, claim_id: str) -> Optional[dict]:
        for e in self.entries:
            if e.get("kind") == "claim" and e.get("id") == claim_id:
                return e
        return None

    def verdicts_for(self, claim_id: str) -> List[dict]:
        return [e for e in self.entries if e.get("kind") == "verdict" and e.get("claim") == claim_id]

    def responses_for(self, claim_id: str) -> List[dict]:
        return [e for e in self.entries if e.get("kind") == "response" and e.get("claim") == claim_id]

    def superseded_by(self, claim_id: str) -> Optional[str]:
        for e in self.entries:
            if e.get("kind") == "claim" and e.get("supersedes") == claim_id:
                return e["id"]
        return None

    def status(self, claim_id: str) -> str:
        """Derive the claim's protocol status from its history."""
        c = self.claim(claim_id)
        if c is None:
            return "unknown"
        if self.superseded_by(claim_id):
            return "superseded"
        ev = c.get("evidence") or {}
        if ev.get("exit_code") not in (0, None):
            return "failed"
        # A human attestation is the escalation authority itself, so it needs no reviewer;
        # verdicts on it (for example a refute after failing evidence) still apply below.
        status = "accepted" if (c.get("attestor") or {}).get("role") == "human" else "attested"
        counters = 0
        for v in self.verdicts_for(claim_id):
            d = v.get("decision")
            role = (v.get("reviewer") or {}).get("role")
            if d == "accept":
                status = "accepted"
            elif d == "counter":
                counters += 1
                status = "contested"
            elif d == "refute":
                return "refuted"
            elif d == "escalate":
                status = "escalated"
            if role == "human" and d in ("accept", "refute"):
                return "accepted" if d == "accept" else "refuted"
        if status == "contested" and counters >= MAX_ROUNDS:
            status = "escalated"
        return status

    def is_fresh(self, claim: dict, index: dict) -> bool:
        scope = claim.get("scope", "cone")
        for subject, bound in (claim.get("bindings") or {}).items():
            if binding_for(index, subject, scope) != bound:
                return False
        return True

    def stale_subjects(self, claim: dict, index: dict) -> List[str]:
        scope = claim.get("scope", "cone")
        return [s for s, b in (claim.get("bindings") or {}).items() if binding_for(index, s, scope) != b]

    def rounds(self, claim_id: str) -> int:
        return sum(1 for v in self.verdicts_for(claim_id) if v.get("decision") == "counter")

    # ---- appends with invariants ------------------------------------------------------
    def add_claim(self, index: dict, predicate: str, statement: str, subjects: List[str], evidence: dict,
                  attestor: dict, scope: str = "cone", supersedes: Optional[str] = None) -> dict:
        if scope not in SCOPES:
            raise ProtocolError(f"unknown scope {scope!r}; expected one of {SCOPES}")
        if not subjects:
            raise ProtocolError("a claim needs at least one subject module")
        if not attestor.get("agent"):
            raise ProtocolError("attestor.agent is required")
        bindings: Dict[str, str] = {}
        for s in subjects:
            b = binding_for(index, s, scope)
            if b is None:
                raise ProtocolError(f"unknown subject module {s!r}; run `assay index`")
            bindings[s] = b
        if supersedes:
            old = self.claim(supersedes)
            if old is None:
                raise ProtocolError(f"cannot supersede unknown claim {supersedes}")
            if old["attestor"]["agent"] != attestor["agent"]:
                raise ProtocolError("only the original attestor may supersede a claim")
            if self.status(supersedes) not in ("contested", "attested", "failed", "escalated"):
                raise ProtocolError(f"claim {supersedes} is {self.status(supersedes)} and cannot be superseded")
        entry = {
            "kind": "claim", "id": self._next_id("c"), "predicate": predicate, "statement": statement,
            "subjects": sorted(subjects), "scope": scope, "bindings": bindings,
            "index_root": index.get("root_hash"), "evidence": evidence, "attestor": attestor,
            "created_at": now_iso(),
        }
        if supersedes:
            entry["supersedes"] = supersedes
        return self._append(entry)

    def add_verdict(self, index: dict, claim_id: str, decision: str, reason: str, reviewer: dict) -> dict:
        c = self.claim(claim_id)
        if c is None:
            raise ProtocolError(f"unknown claim {claim_id}")
        if decision not in DECISIONS:
            raise ProtocolError(f"unknown decision {decision!r}; expected one of {DECISIONS}")
        if not reviewer.get("agent"):
            raise ProtocolError("reviewer.agent is required")
        role = reviewer.get("role", "reviewer")
        if role not in REVIEW_ROLES:
            raise ProtocolError(f"role {role!r} cannot issue verdicts; expected one of {REVIEW_ROLES}")
        if reviewer["agent"] == c["attestor"]["agent"]:
            raise ProtocolError("separation of duties: an attestor cannot review their own claim")
        status = self.status(claim_id)
        if status in TERMINAL:
            raise ProtocolError(f"claim {claim_id} is {status}; no further verdicts")
        if status == "failed":
            raise ProtocolError(f"claim {claim_id} recorded failing evidence; re-attest instead of reviewing")
        if status == "escalated" and role != "human":
            raise ProtocolError(f"claim {claim_id} is escalated; only a human verdict can resolve it")
        if decision == "accept" and not self.is_fresh(c, index):
            stale = ", ".join(self.stale_subjects(c, index))
            raise ProtocolError(f"claim {claim_id} is stale (drift in: {stale}); it must be re-attested before acceptance")
        if not reason.strip() and decision != "accept":
            raise ProtocolError("a counter, refute, or escalate verdict needs a reason")
        if status == "contested":
            resp = self.responses_for(claim_id)
            last_v = self.verdicts_for(claim_id)[-1]
            if not any(r.get("verdict") == last_v["id"] for r in resp):
                raise ProtocolError(f"claim {claim_id} is contested; the attestor must respond before a new verdict")
        entry = {
            "kind": "verdict", "id": self._next_id("v"), "claim": claim_id, "decision": decision,
            "reason": reason.strip(), "reviewer": reviewer, "round": self.rounds(claim_id) + (1 if decision == "counter" else 0),
            "created_at": now_iso(),
        }
        return self._append(entry)

    def add_response(self, claim_id: str, action: str, reason: str, attestor: dict) -> dict:
        c = self.claim(claim_id)
        if c is None:
            raise ProtocolError(f"unknown claim {claim_id}")
        if action not in RESPONSES:
            raise ProtocolError(f"unknown response {action!r}; expected one of {RESPONSES}")
        if attestor.get("agent") != c["attestor"]["agent"]:
            raise ProtocolError("only the attestor may respond to a finding")
        if self.status(claim_id) != "contested":
            raise ProtocolError(f"claim {claim_id} is {self.status(claim_id)}; responses apply to contested claims")
        if action == "contest" and len(reason.strip()) < 12:
            raise ProtocolError("a contest must name the concrete harm the requested change would cause")
        last_v = self.verdicts_for(claim_id)[-1]
        entry = {
            "kind": "response", "id": self._next_id("r"), "claim": claim_id, "verdict": last_v["id"],
            "action": action, "reason": reason.strip(), "attestor": attestor, "created_at": now_iso(),
        }
        return self._append(entry)

    # ---- reporting -------------------------------------------------------------------
    def table(self, index: Optional[dict] = None) -> List[dict]:
        rows = []
        for c in self.claims():
            rows.append({
                "id": c["id"], "predicate": c["predicate"], "subjects": c["subjects"], "scope": c.get("scope", "cone"),
                "status": self.status(c["id"]), "fresh": self.is_fresh(c, index) if index else None,
                "attestor": c["attestor"].get("agent"), "rounds": self.rounds(c["id"]),
                "exit_code": (c.get("evidence") or {}).get("exit_code"), "tampered": bool(c.get("_tampered")),
            })
        return rows
