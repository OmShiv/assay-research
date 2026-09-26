import json
import os
import time

import pytest

from assay.brief import make_brief
from assay.gate import evaluate_gate
from assay.index import build_index
from assay.ledger import Ledger, ProtocolError
from assay.obligation import evidence_obligation, obligation_level
from assay.protocol import attest, respond, review
from assay.store import Store
from assay.tokens import estimate_tokens
from conftest import write

DOER = {"agent": "doer-1", "role": "doer"}
REVIEWER = {"agent": "reviewer-1", "role": "reviewer"}
HUMAN = {"agent": "om", "role": "human"}


def setup(repo):
    store = Store(repo)
    store.ensure()
    idx, _ = build_index(repo, depth=2, store=store)
    store.save_index(idx)
    return store, idx


def test_attest_binds_cone_hashes_and_runs_evidence(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py")
    assert c["evidence"]["exit_code"] == 0
    assert c["bindings"]["pkg/app"] == idx["modules"]["pkg/app"]["cone_hash"]
    led = Ledger(store)
    assert led.status(c["id"]) == "attested"
    assert led.is_fresh(c, idx)
    assert not led.tampered


def test_failed_evidence_is_recorded_not_hidden(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py && false")
    assert c["evidence"]["exit_code"] != 0
    led = Ledger(store)
    assert led.status(c["id"]) == "failed"
    with pytest.raises(ProtocolError):
        review(store, idx, c["id"], "accept", "", REVIEWER)


def test_separation_of_duties(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py")
    with pytest.raises(ProtocolError, match="separation of duties"):
        review(store, idx, c["id"], "accept", "", {"agent": "doer-1", "role": "reviewer"})
    with pytest.raises(ProtocolError, match="cannot issue verdicts"):
        review(store, idx, c["id"], "accept", "", {"agent": "someone", "role": "doer"})
    v = review(store, idx, c["id"], "accept", "", REVIEWER)
    assert v["decision"] == "accept"
    assert Ledger(store).status(c["id"]) == "accepted"


def test_stale_claim_cannot_be_accepted_and_upstream_change_stales_dependents(repo):
    store, idx = setup(repo)
    c_app = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py")
    c_util = attest(store, idx, "tests-pass", ["pkg/util"], DOER, command="python -m pytest -q tests/test_util.py")
    time.sleep(0.01)
    write(repo, "pkg/core/math.py", "def add(a, b):\n    return a + b + 0\n")
    idx2, _ = build_index(repo, depth=2, store=store)
    store.save_index(idx2)
    led = Ledger(store)
    assert not led.is_fresh(led.claim(c_app["id"]), idx2)        # app depends on core: stale
    assert led.is_fresh(led.claim(c_util["id"]), idx2)           # util does not: fresh
    with pytest.raises(ProtocolError, match="stale"):
        review(store, idx2, c_app["id"], "accept", "", REVIEWER)
    # counter and refute remain possible on a stale claim; accept does not
    v = review(store, idx2, c_app["id"], "counter", "re-run after core change", REVIEWER)
    assert v["decision"] == "counter"


def test_counter_contest_rounds_and_escalation(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py")
    review(store, idx, c["id"], "counter", "no negative-number test", REVIEWER)
    led = Ledger(store)
    assert led.status(c["id"]) == "contested"
    # reviewer cannot pile on before the attestor responds
    with pytest.raises(ProtocolError, match="must respond"):
        review(store, idx, c["id"], "counter", "also missing docs", REVIEWER)
    # a contest must name the harm
    with pytest.raises(ProtocolError, match="concrete harm"):
        respond(store, c["id"], "contest", "no", DOER)
    with pytest.raises(ProtocolError, match="only the attestor"):
        respond(store, c["id"], "contest", "adding that test would double the runtime of CI", REVIEWER)
    respond(store, c["id"], "contest", "adding that test would double the runtime of CI", DOER)
    review(store, idx, c["id"], "counter", "runtime is not a reason to skip correctness", REVIEWER)
    respond(store, c["id"], "contest", "the function is total over ints; negative inputs are covered by add symmetry", DOER)
    review(store, idx, c["id"], "counter", "symmetry is not tested either", REVIEWER)
    led = Ledger(store)
    assert led.rounds(c["id"]) == 3
    assert led.status(c["id"]) == "escalated"
    with pytest.raises(ProtocolError, match="only a human"):
        review(store, idx, c["id"], "accept", "", REVIEWER)
    review(store, idx, c["id"], "accept", "acceptable for this sprint", HUMAN)
    assert Ledger(store).status(c["id"]) == "accepted"


def test_accept_finding_supersedes(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py")
    review(store, idx, c["id"], "counter", "run the whole suite", REVIEWER)
    respond(store, c["id"], "accept-finding", "will run everything", DOER)
    c2 = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q", supersedes=c["id"])
    led = Ledger(store)
    assert led.status(c["id"]) == "superseded"
    assert led.status(c2["id"]) == "attested"
    with pytest.raises(ProtocolError, match="no further verdicts"):
        review(store, idx, c["id"], "accept", "", REVIEWER)


def test_refute_is_terminal(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py")
    review(store, idx, c["id"], "refute", "the test file asserts nothing relevant", REVIEWER)
    assert Ledger(store).status(c["id"]) == "refuted"
    with pytest.raises(ProtocolError):
        review(store, idx, c["id"], "accept", "", HUMAN)


def test_ledger_tamper_detected(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py")
    review(store, idx, c["id"], "accept", "", REVIEWER)
    lines = open(store.ledger_path, encoding="utf-8").read().splitlines()
    forged = json.loads(lines[0])
    forged["evidence"]["exit_code"] = 0
    forged["evidence"]["command"] = "pytest -q"
    forged["subjects"] = ["pkg/app", "pkg/core"]
    with open(store.ledger_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n" + "\n".join(lines[1:]) + "\n")
    led = Ledger(store)
    assert led.tampered == [c["id"]]
    g = evaluate_gate(store, idx, changed=["pkg/app"], ledger=led)
    assert not g["passed"] and g["tampered"]


def test_obligation_levels(repo):
    store, idx = setup(repo)
    ob = evidence_obligation(idx, ["pkg/util"])
    assert ob["blast_radius"] == ["pkg/util", "tests"]
    ob_core = evidence_obligation(idx, ["pkg/core"])
    assert ob_core["level"] >= ob["level"]
    assert ob_core["br_size"] == 4
    assert obligation_level(0.0, 0.0, 1) == 0
    assert obligation_level(0.1, 0.0, 2) == 1
    assert obligation_level(0.25, 0.0, 3) == 2
    assert obligation_level(0.6, 0.0, 2) == 1      # tiny repo: fraction alone does not escalate
    assert obligation_level(0.6, 0.0, 4) == 3
    assert obligation_level(0.0, 0.9, 1) == 3
    assert any(r["predicate"] == "human-approved" for r in evidence_obligation(idx, list(idx["modules"]))["required"])


def test_gate_end_to_end(repo):
    store, idx = setup(repo)
    g = evaluate_gate(store, idx, changed=["pkg/util"])
    assert not g["passed"] and {r["status"] for r in g["rows"]} == {"missing"}
    c = attest(store, idx, "tests-pass", ["pkg/util", "tests"], DOER, command="python -m pytest -q")
    g = evaluate_gate(store, idx, changed=["pkg/util"])
    assert not g["passed"] and {r["status"] for r in g["rows"]} == {"unreviewed"}
    with pytest.raises(ProtocolError):
        review(store, idx, c["id"], "accept", "", DOER | {"role": "reviewer"})
    review(store, idx, c["id"], "accept", "", REVIEWER)
    g = evaluate_gate(store, idx, changed=["pkg/util"])
    assert g["passed"], g
    # partial coverage: a change to core needs evidence on the whole radius
    g = evaluate_gate(store, idx, changed=["pkg/core"])
    assert not g["passed"]
    assert {r["subject"] for r in g["rows"] if r["status"] == "missing"} >= {"pkg/core", "pkg/service", "pkg/app"}
    # drift after acceptance makes the accepted claim stale
    time.sleep(0.01)
    write(repo, "pkg/util/strings.py", "def shout(s):\n    return s.upper() + '!!'\n")
    idx2, _ = build_index(repo, depth=2, store=store)
    store.save_index(idx2)
    g = evaluate_gate(store, idx2, changed=["pkg/util"])
    assert not g["passed"] and {r["status"] for r in g["rows"]} == {"stale"}


def test_weak_evidence_flagged(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/util", "tests"], DOER, command="true")
    review(store, idx, c["id"], "accept", "", REVIEWER)
    g = evaluate_gate(store, idx, changed=["pkg/util"])
    assert not g["passed"] and {r["status"] for r in g["rows"]} == {"weak"}


def test_brief_respects_budget_and_reports_state(repo):
    store, idx = setup(repo)
    c = attest(store, idx, "tests-pass", ["pkg/app"], DOER, command="python -m pytest -q tests/test_app.py")
    time.sleep(0.01)
    write(repo, "pkg/core/math.py", "def add(a, b):\n    return a + b + 0\n")
    idx2, _ = build_index(repo, depth=2, store=store)
    store.save_index(idx2)
    text = make_brief(store, idx2, changed=["pkg/core"], budget_tokens=400)
    assert "Stale claims: " + c["id"] in text
    assert "level" in text and "Required evidence" in text
    assert estimate_tokens(text) <= 400 + 60  # trailer line allowance
    small = make_brief(None, idx2, changed=None, budget_tokens=120)
    assert estimate_tokens(small) <= 200


def test_human_approved_without_command(repo):
    store, idx = setup(repo)
    with pytest.raises(ValueError):
        attest(store, idx, "tests-pass", ["pkg/app"], DOER)
    c = attest(store, idx, "human-approved", ["pkg/app"], HUMAN, note="reviewed the diff in the PR UI")
    assert c["evidence"]["exit_code"] == 0 and c["evidence"]["command"] is None
