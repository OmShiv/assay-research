"""Per-language evidence commands and the no-runner fallback obligation.

Fixture: a repository with a Python package tested by pytest and a TypeScript app that has a
tsconfig and a build script but no test script. Before these fixes the gate demanded
tests-pass on the TypeScript modules and the brief told agents to run pytest there.
"""

import json
import os
import subprocess

import pytest

from assay.brief import make_brief
from assay.gate import evaluate_gate, evidence_is_weak
from assay.hooks import write_agent_snippet
from assay.index import build_index, commands_for, detect_project, module_lang
from assay.index import test_command_for as command_for_tests   # not a test
from assay.ledger import Ledger
from assay.obligation import evidence_obligation
from assay.protocol import attest, review
from assay.store import Store
from conftest import write

DOER = {"agent": "doer-1", "role": "doer"}
REVIEWER = {"agent": "reviewer-1", "role": "reviewer"}


@pytest.fixture
def mixed(tmp_path):
    root = str(tmp_path / "mixed")
    os.makedirs(root)
    write(root, "pkg/core/__init__.py", "def add(a, b):\n    return a + b\n")
    write(root, "tests/test_core.py", "from pkg.core import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    write(root, "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    write(root, "src/shared/types.ts", "export interface Msg { id: string }\n")
    write(root, "src/main/index.ts", "import { Msg } from '../shared/types';\nexport function run(m: Msg) { return m.id }\n")
    write(root, "src/renderer/app.tsx", "import { run } from '../main/index';\nexport const App = () => run({ id: 'x' });\n")
    write(root, "tsconfig.json", json.dumps({"compilerOptions": {"strict": True}, "include": ["src"]}))
    write(root, "package.json", json.dumps({"name": "mixed", "scripts": {"build": "tsc && vite build", "lint": "eslint ."}}))
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-q", "-m", "init"], check=True)
    store = Store(root)
    store.ensure()
    idx, _ = build_index(root, depth=2, store=store)
    store.save_index(idx)
    return root, store, idx


def test_commands_are_detected_per_language(mixed):
    root, store, idx = mixed
    p = idx["project"]
    assert p["test_commands"] == {"python": "pytest"}
    assert p["commands"]["typescript"] == {"typecheck": "npx tsc --noEmit", "build": "npm run build", "lint": "npm run lint"}
    assert "test" not in p["commands"]["javascript"]
    assert p["test_command"] == "pytest"          # dominant language with a runner
    assert module_lang(idx, "src/main") == "typescript"
    assert module_lang(idx, "pkg/core") == "python"
    assert command_for_tests(idx, "pkg/core") == "pytest"
    assert command_for_tests(idx, "src/main") == ""
    assert commands_for(idx, "src/main")["typecheck"] == "npx tsc --noEmit"


def test_lockfile_selects_package_manager(tmp_path):
    root = str(tmp_path / "pn")
    write(root, "package.json", json.dumps({"scripts": {"test": "vitest", "build": "tsc"}}))
    write(root, "pnpm-lock.yaml", "")
    write(root, "src/a.ts", "export const a = 1\n")
    p = detect_project(root, ["src/a.ts"])
    assert p["commands"]["typescript"]["test"] == "pnpm test"
    assert p["commands"]["typescript"]["build"] == "pnpm run build"


def test_no_runner_obligation_is_satisfiable(mixed):
    root, store, idx = mixed
    ob = evidence_obligation(idx, ["src/shared"])
    preds = {(r["predicate"], r["scope"]) for r in ob["required"]}
    assert ("build-ok", "cone") in preds and ("typecheck-clean", "self") in preds
    assert not any(r["predicate"] == "tests-pass" and "src/shared" in r["subjects"] for r in ob["required"])
    reason = next(r["reason"] for r in ob["required"] if r["predicate"] == "build-ok")
    assert "no test runner for typescript" in reason and "npm run build" in reason
    # the python module keeps its tests-pass requirement
    py = evidence_obligation(idx, ["pkg/core"])["required"]
    assert [r["subjects"] for r in py if r["predicate"] == "tests-pass"] == [["pkg/core", "tests"]]
    assert not any(r["predicate"] in ("build-ok", "typecheck-clean") for r in py)
    # gate rows carry the explanation
    g = evaluate_gate(store, idx, changed=["src/shared"])
    assert not g["passed"]
    assert all(r["status"] == "missing" for r in g["rows"])
    assert any("no test runner" in r["note"] for r in g["rows"])


def test_gate_passes_on_typescript_module_with_build_and_typecheck(mixed):
    root, store, idx = mixed
    ob = evidence_obligation(idx, ["src/renderer"])      # leaf module: level 0, radius 1
    assert ob["level"] == 0 and [r["predicate"] for r in ob["required"]] == ["build-ok", "typecheck-clean"]
    subjects = ob["blast_radius"]
    # the floor is a command that looks like a build or a type checker; real commands are exercised in the dogfooding run
    b = attest(store, idx, "build-ok", subjects, DOER, command="echo build ok", scope="cone")
    t = attest(store, idx, "typecheck-clean", subjects, DOER, command="echo tsc ok", scope="self")
    review(store, idx, b["id"], "accept", "", REVIEWER)
    review(store, idx, t["id"], "accept", "", REVIEWER)
    g = evaluate_gate(store, idx, changed=["src/renderer"])
    assert g["passed"], g["rows"]
    # both claims go stale when an upstream module changes: the cone-scoped build claim through
    # the cone hash, the self-scoped typecheck claim only when its own module changes
    write(root, "src/shared/types.ts", "export interface Msg { id: string; ts: number }\n")
    idx2, _ = build_index(root, depth=2, store=store)
    store.save_index(idx2)
    g2 = evaluate_gate(store, idx2, changed=["src/renderer"])
    assert not g2["passed"]
    assert {(r["predicate"], r["status"]) for r in g2["rows"]} == {("build-ok", "stale"), ("typecheck-clean", "ok")}
    write(root, "src/renderer/app.tsx", "import { run } from '../main/index';\nexport const App = () => run({ id: 'y' });\n")
    idx3, _ = build_index(root, depth=2, store=store)
    store.save_index(idx3)
    g3 = evaluate_gate(store, idx3, changed=["src/renderer"])
    assert not g3["passed"] and {r["status"] for r in g3["rows"]} == {"stale"}


def test_runner_must_match_subject_language(mixed):
    root, store, idx = mixed
    c = attest(store, idx, "tests-pass", ["src/main"], DOER, command="python -m pytest -q tests")
    weak = evidence_is_weak(c, idx, Ledger(store))
    assert weak and "does not test typescript" in weak
    ok = attest(store, idx, "tests-pass", ["pkg/core"], DOER, command="python -m pytest -q tests")
    assert evidence_is_weak(ok, idx, Ledger(store)) is None
    bad = attest(store, idx, "build-ok", ["src/main"], DOER, command="echo nothing")
    assert "does not look like a build" in evidence_is_weak(bad, idx, None)


def test_policy_overrides_and_survives_reindex(mixed):
    root, store, idx = mixed
    idx["policy"] = {"no_runner": ["tests-pass"]}
    store.save_index(idx)
    ob = evidence_obligation(idx, ["src/renderer"])
    assert [r["predicate"] for r in ob["required"]] == ["tests-pass"]
    idx["policy"] = {"commands": {"typescript": {"test": "npx vitest run"}}}
    store.save_index(idx)
    assert command_for_tests(idx, "src/main") == "npx vitest run"
    ob = evidence_obligation(idx, ["src/renderer"])
    assert [r["predicate"] for r in ob["required"]] == ["tests-pass"]
    idx2, _ = build_index(root, depth=2, store=store)
    assert idx2["policy"] == idx["policy"]


def test_brief_and_agents_snippet_list_commands_per_language(mixed):
    root, store, idx = mixed
    text = make_brief(store, idx, changed=["src/shared"], budget_tokens=600)
    assert "typescript: typecheck npx tsc --noEmit, build npm run build, no test runner" in text
    assert "python: test pytest" in text
    assert "build-ok[" in text and "typecheck-clean[" in text
    path = write_agent_snippet(root, index=idx)
    body = open(path, encoding="utf-8").read()
    assert "- typescript: typecheck `npx tsc --noEmit`" in body and "- python: test `pytest`" in body
    # a second call with a changed index replaces the block instead of appending
    idx["policy"] = {"commands": {"typescript": {"test": "npx vitest run"}}}
    write_agent_snippet(root, index=idx)
    body2 = open(path, encoding="utf-8").read()
    assert body2.count("## Assay") == 1 and "npx vitest run" in body2
