"""Module dependency graph, blast radius, and Merkle cone hashes.

The dependency *cone* of a module is the module plus everything it transitively depends
on. The cone hash is a Merkle hash over that cone, computed on the condensation DAG so
that dependency cycles are handled (every member of a strongly connected component shares
one cone hash). A claim bound to a cone hash is stale exactly when something in the cone
changed, which is the same set of modules as the blast radius of the change seen from the
other direction.
"""

import hashlib
from collections import deque
from typing import Dict, Iterable, List, Set


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class Graph:
    def __init__(self, deps: Dict[str, Set[str]]):
        self.nodes: List[str] = sorted(deps)
        self.deps: Dict[str, Set[str]] = {n: set(d) - {n} for n, d in deps.items()}
        for n in list(self.deps):
            for d in self.deps[n]:
                self.deps.setdefault(d, set())
        self.nodes = sorted(self.deps)
        self.dependents: Dict[str, Set[str]] = {n: set() for n in self.deps}
        for n, ds in self.deps.items():
            for d in ds:
                self.dependents[d].add(n)

    @classmethod
    def from_index(cls, index: dict) -> "Graph":
        return cls({m: set(v.get("depends_on", [])) for m, v in index["modules"].items()})

    # ---- closures -----------------------------------------------------------------
    def _closure(self, start: Iterable[str], adj: Dict[str, Set[str]]) -> Set[str]:
        seen: Set[str] = set()
        dq = deque(s for s in start if s in adj)
        while dq:
            n = dq.popleft()
            for m in adj.get(n, ()):
                if m not in seen:
                    seen.add(m)
                    dq.append(m)
        return seen

    def transitive_deps(self, mods: Iterable[str]) -> Set[str]:
        return self._closure(mods, self.deps)

    def transitive_dependents(self, mods: Iterable[str]) -> Set[str]:
        return self._closure(mods, self.dependents)

    def cone(self, mod: str) -> Set[str]:
        return {mod} | self.transitive_deps([mod])

    def blast_radius(self, changed: Iterable[str]) -> Set[str]:
        changed = {c for c in changed if c in self.deps}
        return changed | self.transitive_dependents(changed)

    def fan_in(self, mod: str) -> int:
        return len(self.dependents.get(mod, ()))

    # ---- strongly connected components (Tarjan, iterative) -------------------------------
    def sccs(self) -> List[List[str]]:
        index = 0
        stack: List[str] = []
        on_stack: Set[str] = set()
        idx: Dict[str, int] = {}
        low: Dict[str, int] = {}
        out: List[List[str]] = []
        for root in self.nodes:
            if root in idx:
                continue
            work = [(root, iter(sorted(self.deps[root])))]
            idx[root] = low[root] = index
            index += 1
            stack.append(root)
            on_stack.add(root)
            while work:
                node, it = work[-1]
                advanced = False
                for nxt in it:
                    if nxt not in idx:
                        idx[nxt] = low[nxt] = index
                        index += 1
                        stack.append(nxt)
                        on_stack.add(nxt)
                        work.append((nxt, iter(sorted(self.deps[nxt]))))
                        advanced = True
                        break
                    elif nxt in on_stack:
                        low[node] = min(low[node], idx[nxt])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == idx[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    out.append(sorted(comp))
        return out  # emitted successors-first (reverse topological order of the condensation)

    # ---- Merkle cone hashes ------------------------------------------------------------
    def cone_hashes(self, module_hashes: Dict[str, str]) -> Dict[str, str]:
        comp_of: Dict[str, int] = {}
        comps = self.sccs()
        for i, comp in enumerate(comps):
            for m in comp:
                comp_of[m] = i
        comp_hash: Dict[int, str] = {}
        for i, comp in enumerate(comps):
            succ = set()
            for m in comp:
                for d in self.deps[m]:
                    j = comp_of[d]
                    if j != i:
                        succ.add(j)
            own = "\n".join(sorted(f"{m}:{module_hashes.get(m, '')}" for m in comp))
            below = "\n".join(sorted(comp_hash[j] for j in succ))
            comp_hash[i] = sha256_hex(own + "\n--\n" + below)
        return {m: comp_hash[comp_of[m]] for m in self.nodes}
