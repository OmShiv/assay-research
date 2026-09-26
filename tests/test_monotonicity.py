import time

from assay.gate import evaluate_gate
from assay.index import build_index
from assay.protocol import attest, review
from assay.store import Store
from conftest import write

DOER = {"agent": "doer-1", "role": "doer"}
REVIEWER = {"agent": "reviewer-1", "role": "reviewer"}
HUMAN = {"agent": "om", "role": "human"}


def test_deleting_tests_is_flagged_until_human_signs_off(repo):
    store = Store(repo)
    store.ensure()
    idx, _ = build_index(repo, depth=2, store=store)
    store.save_index(idx)
    c0 = attest(store, idx, "tests-pass", ["pkg/util", "tests"], DOER, command="python -m pytest -q")
    assert c0["evidence"]["tests_passed"] == 2
    review(store, idx, c0["id"], "accept", "", REVIEWER)
    assert evaluate_gate(store, idx, changed=["pkg/util"])["passed"]

    # break util, delete the test that would catch it, re-attest: count drops 2 -> 1
    time.sleep(0.01)
    write(repo, "pkg/util/strings.py", "def shout(s):\n    return s.lower()\n")
    write(repo, "tests/test_util.py", "def test_nothing():\n    assert True\n")
    idx2, _ = build_index(repo, depth=2, store=store)
    store.save_index(idx2)
    c1 = attest(store, idx2, "tests-pass", ["pkg/util", "tests"], DOER, command="python -m pytest -q")
    assert c1["evidence"]["exit_code"] == 0 and c1["evidence"]["tests_passed"] == 2  # test_app still runs
    # narrow the run to hide the drop even further: only one test executed
    c2 = attest(store, idx2, "tests-pass", ["pkg/util", "tests"], DOER, command="python -m pytest -q tests/test_util.py")
    review(store, idx2, c2["id"], "accept", "", REVIEWER)
    g = evaluate_gate(store, idx2, changed=["pkg/util", "tests"])
    statuses = {(r["subject"], r["status"]) for r in g["rows"]}
    assert not g["passed"]
    assert any(s == "weak" for _, s in statuses)
    assert any("dropped" in r["note"] for r in g["rows"])

    # a fresh human-approved claim on the subject lifts the block
    attest(store, idx2, "human-approved", ["tests"], HUMAN, note="test removal reviewed")
    g2 = evaluate_gate(store, idx2, changed=["pkg/util", "tests"])
    assert all(r["status"] in ("ok", "weak") for r in g2["rows"])
    assert not any("dropped" in r["note"] for r in g2["rows"])
