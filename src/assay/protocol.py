"""Claim lifecycle: attest, review, respond. Also the state machine reference.

The lifecycle (derived states, see Ledger.status):

    attested  --accept-->  accepted
    attested  --counter--> contested --(attestor responds)--> reviewer re-decides
    attested  --refute-->  refuted            (terminal)
    attested  --escalate-> escalated --(human accept/refute)--> accepted / refuted
    contested --3 rounds-> escalated
    any       --new claim with supersedes--> superseded (terminal)
    any       --binding drift-------------> stale (computed; blocks acceptance and the gate)

Forbidden by construction: self review, acceptance of a stale claim, a verdict on a
terminal claim, a non-human resolution of an escalation, a contest without a named harm,
a response by anyone but the attestor, and an unsigned or altered ledger line.
"""

import hashlib
import os
import re
import subprocess
import time
from typing import Dict, List, Optional

from .ledger import Ledger, PREDICATES
from .store import Store

STATES = ("attested", "accepted", "contested", "refuted", "escalated", "superseded", "failed", "stale")

TRANSITIONS = [
    ("attested", "accept", "accepted"),
    ("attested", "counter", "contested"),
    ("attested", "refute", "refuted"),
    ("attested", "escalate", "escalated"),
    ("contested", "respond:contest + accept", "accepted"),
    ("contested", "respond:contest + counter", "contested"),
    ("contested", "respond:contest + refute", "refuted"),
    ("contested", "respond:accept-finding + new claim", "superseded"),
    ("contested", "3 counters", "escalated"),
    ("escalated", "human accept", "accepted"),
    ("escalated", "human refute", "refuted"),
    ("accepted", "binding drift", "stale"),
    ("attested", "binding drift", "stale"),
    ("stale", "re-attest (new claim)", "attested"),
]

FORBIDDEN = [
    "attestor reviews own claim",
    "accept while stale",
    "verdict on refuted or superseded claim",
    "non-human resolves escalation",
    "contest without named harm",
    "response by non-attestor",
    "unsigned or altered ledger entry",
    "gate pass without fresh accepted evidence covering the blast radius",
]


def run_evidence(command: str, cwd: str, timeout: int = 900, tail_chars: int = 600) -> dict:
    t0 = time.time()
    try:
        proc = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, timeout=timeout)
        out = proc.stdout + b"\n" + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") + b"\n[assay] timeout"
        code = 124
    text = out.decode("utf-8", errors="replace")
    ev = {
        "command": command,
        "exit_code": code,
        "output_hash": hashlib.sha256(out).hexdigest(),
        "output_bytes": len(out),
        "output_tail": text[-tail_chars:],
        "duration_s": round(time.time() - t0, 3),
        "cwd": os.path.relpath(cwd, cwd) if cwd else ".",
    }
    counted = _count_passed_tests(text)
    if counted is not None:
        ev["tests_passed"] = counted
    return ev


_PASSED_PATTERNS = (
    re.compile(r"(\d+) passed"),                      # pytest, cargo test, vitest
    re.compile(r"Tests:\s+(?:\d+ \w+, )*(\d+) passed"),  # jest
    re.compile(r"(\d+) passing"),                     # mocha
    re.compile(r"(\d+) examples?, 0 failures"),       # rspec
    re.compile(r"Ran (\d+) tests?"),                  # unittest
)


def _count_passed_tests(text: str) -> Optional[int]:
    """Best-effort count of executed tests from runner output; used for evidence monotonicity."""
    best = None
    for rx in _PASSED_PATTERNS:
        for m in rx.finditer(text):
            n = int(m.group(1))
            best = n if best is None else max(best, n)
    if best is None:
        ok_lines = sum(1 for line in text.splitlines() if line.startswith("ok  \t") or line.startswith("ok \t") or line.startswith("ok  "))
        if ok_lines and "go" in text.lower():
            best = ok_lines
    return best


def attest(store: Store, index: dict, predicate: str, subjects: List[str], attestor: Dict[str, str],
           command: Optional[str] = None, statement: str = "", scope: str = "cone",
           supersedes: Optional[str] = None, note: str = "", timeout: int = 900) -> dict:
    if predicate not in PREDICATES:
        predicate_l = "custom"
        statement = statement or predicate
        predicate = predicate_l
    if command:
        evidence = run_evidence(command, store.root, timeout=timeout)
    else:
        if predicate in ("tests-pass", "lint-clean", "typecheck-clean", "build-ok"):
            raise ValueError(f"predicate {predicate} requires a command that produces the evidence")
        evidence = {"command": None, "exit_code": 0, "note": note, "output_hash": hashlib.sha256(note.encode()).hexdigest()}
    ledger = Ledger(store)
    with store.lock():
        ledger._load()
        entry = ledger.add_claim(index, predicate, statement or predicate, subjects, evidence, attestor, scope, supersedes)
    return entry


def review(store: Store, index: dict, claim_id: str, decision: str, reason: str, reviewer: Dict[str, str]) -> dict:
    ledger = Ledger(store)
    with store.lock():
        ledger._load()
        return ledger.add_verdict(index, claim_id, decision, reason, reviewer)


def respond(store: Store, claim_id: str, action: str, reason: str, attestor: Dict[str, str]) -> dict:
    ledger = Ledger(store)
    with store.lock():
        ledger._load()
        return ledger.add_response(claim_id, action, reason, attestor)
