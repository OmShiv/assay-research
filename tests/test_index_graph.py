import os
import time

from assay.graph import Graph
from assay.index import build_index, diff_index, modules_for_files
from assay.store import Store
from conftest import write


def test_index_modules_and_edges(repo):
    idx, stats = build_index(repo, depth=2)
    mods = idx["modules"]
    assert {"pkg/core", "pkg/service", "pkg/app", "pkg/util", "tests"} <= set(mods)
    assert "pkg/core" in mods["pkg/service"]["depends_on"]
    assert "pkg/service" in mods["pkg/app"]["depends_on"]
    assert "pkg/app" in mods["tests"]["depends_on"]
    assert "pkg/util" in mods["tests"]["depends_on"]
    assert mods["pkg/core"]["depends_on"] == []
    assert "pkg/service" in mods["pkg/core"]["depended_by"]
    assert "add()" in mods["pkg/core"]["exports"]
    assert "Calculator" in mods["pkg/service"]["exports"]
    assert "_hidden()" not in mods["pkg/core"]["exports"]
    assert idx["project"]["test_command"] == "pytest"
    assert stats["files"] == 11


def test_merkle_cache_skips_unchanged_files(repo):
    store = Store(repo)
    idx1, s1 = build_index(repo, depth=2, store=store)
    assert s1["reparsed"] == s1["files"]
    idx2, s2 = build_index(repo, depth=2, store=store)
    assert s2["reparsed"] == 0 and s2["cached"] == s1["files"]
    assert idx1["root_hash"] == idx2["root_hash"]
    time.sleep(0.01)
    write(repo, "pkg/util/strings.py", "def shout(s):\n    return s.upper() + '!'\n")
    idx3, s3 = build_index(repo, depth=2, store=store)
    assert s3["reparsed"] == 1
    assert idx3["root_hash"] != idx1["root_hash"]


def test_cone_hash_propagates_only_upward(repo):
    store = Store(repo)
    idx1, _ = build_index(repo, depth=2, store=store)
    time.sleep(0.01)
    write(repo, "pkg/core/math.py", "def add(a, b):\n    return b + a\n")
    idx2, _ = build_index(repo, depth=2, store=store)
    d = diff_index(idx1, idx2)
    assert d["changed_modules"] == ["pkg/core"]
    # core changed: its dependents service, app, tests see a cone change; util does not.
    assert set(d["cone_changed_modules"]) == {"pkg/core", "pkg/service", "pkg/app", "tests"}
    assert idx1["modules"]["pkg/util"]["cone_hash"] == idx2["modules"]["pkg/util"]["cone_hash"]
    assert idx1["modules"]["pkg/app"]["hash"] == idx2["modules"]["pkg/app"]["hash"]
    assert idx1["modules"]["pkg/app"]["cone_hash"] != idx2["modules"]["pkg/app"]["cone_hash"]


def test_blast_radius_equals_cone_change_frontier(repo):
    idx, _ = build_index(repo, depth=2)
    g = Graph.from_index(idx)
    assert g.blast_radius(["pkg/core"]) == {"pkg/core", "pkg/service", "pkg/app", "tests"}
    assert g.blast_radius(["pkg/util"]) == {"pkg/util", "tests"}
    assert g.blast_radius(["tests"]) == {"tests"}
    assert g.cone("pkg/app") == {"pkg/app", "pkg/service", "pkg/core"}


def test_scc_handles_cycles():
    g = Graph({"a": {"b"}, "b": {"a"}, "c": {"a"}, "d": set()})
    comps = {frozenset(c) for c in g.sccs()}
    assert frozenset({"a", "b"}) in comps
    h = g.cone_hashes({"a": "1", "b": "2", "c": "3", "d": "4"})
    assert h["a"] == h["b"]
    assert h["c"] != h["a"]
    h2 = g.cone_hashes({"a": "1", "b": "X", "c": "3", "d": "4"})
    assert h2["c"] != h["c"] and h2["d"] == h["d"]


def test_modules_for_files(repo):
    idx, _ = build_index(repo, depth=2)
    assert modules_for_files(idx, ["pkg/core/math.py", "README.md"]) == ["pkg/core"]
    assert modules_for_files(idx, ["pkg/new/thing.py"]) == []


def test_js_go_rust_parsing(tmp_path):
    root = str(tmp_path / "poly")
    write(root, "src/a.ts", "import { b } from './b';\nimport React from 'react';\nexport function a() { return b(); }\nexport const K = 1;\n")
    write(root, "src/b.ts", "export const b = () => 1;\nexport default class Foo {}\n")
    write(root, "go.mod", "module example.com/m\n")
    write(root, "cmd/main.go", 'package main\nimport (\n\t"fmt"\n\t"example.com/m/lib"\n)\nfunc main() { fmt.Println(lib.Hello()) }\n')
    write(root, "lib/lib.go", "package lib\nfunc Hello() string { return \"hi\" }\ntype Thing struct{}\nfunc hidden() {}\n")
    write(root, "rs/src/lib.rs", "pub mod util;\npub fn top() -> i32 { crate::util::helper() }\n")
    write(root, "rs/src/util.rs", "pub fn helper() -> i32 { 1 }\n")
    idx, _ = build_index(root, depth=2, use_cache=False)
    m = idx["modules"]
    assert "a()" in m["src"]["exports"] and "K" in m["src"]["exports"] and "Foo" in m["src"]["exports"]
    assert idx["files"]["src/a.ts"]["imports"] == ["src/b.ts"]
    assert "react" in idx["hints"]["external_top"]
    assert "lib" in m["cmd"]["depends_on"]
    assert "Hello()" in m["lib"]["exports"] and "Thing" in m["lib"]["exports"] and "hidden()" not in m["lib"]["exports"]
    assert idx["files"]["rs/src/lib.rs"]["imports"] == ["rs/src/util.rs"]
    assert "top()" in m["rs/src"]["exports"]
