"""Build the content-addressed index (assay.json).

Pipeline: list files (git aware) -> per-file hash + parse with a Merkle skip cache ->
resolve imports to in-repo files -> group files into modules (directories, capped depth)
-> module Merkle hashes -> module graph -> SCC condensation and cone hashes -> git activity
-> risk -> hints. The result is a single JSON document that an agent reads instead of
exploring, and that the ledger binds claims against.
"""

import hashlib
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set

from .graph import Graph, sha256_hex
from .parsers import FileInfo, SOURCE_EXTS, parse_file
from .store import Store

INDEX_VERSION = 1
CHURN_SATURATION = 20.0   # commits in the activity window at which churn risk reads 1.0
FANIN_SATURATION = 10.0   # dependent modules at which fan-in risk reads 1.0

IGNORE_PARTS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "target",
    ".assay", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "vendor",
    "site-packages", ".next", "coverage", ".idea", ".vscode", "third_party", "testdata",
}

JS_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts"]


# ------------------------------------------------------------------------------ file listing

def list_source_files(root: str) -> List[str]:
    rels: List[str] = []
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True, timeout=60,
        ).stdout
        for raw in out.split(b"\0"):
            if not raw:
                continue
            rel = raw.decode("utf-8", errors="replace")
            rels.append(rel)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_PARTS and not d.startswith(".")]
            for fn in filenames:
                rels.append(os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/"))
    keep = []
    for rel in rels:
        parts = rel.split("/")
        if any(p in IGNORE_PARTS for p in parts[:-1]):
            continue
        if os.path.splitext(rel)[1].lower() not in SOURCE_EXTS:
            continue
        if not os.path.isfile(os.path.join(root, rel)):
            continue
        keep.append(rel)
    return sorted(set(keep))


def module_of(rel: str, depth: int) -> str:
    parts = rel.split("/")[:-1]
    if not parts:
        return "."
    return "/".join(parts[:depth])


# ------------------------------------------------------------------------------ resolution

class ProjectContext:
    def __init__(self, root: str, files: List[str]):
        self.root = root
        self.fileset: Set[str] = set(files)
        self.dirs: Set[str] = set()
        for f in files:
            d = os.path.dirname(f)
            while True:
                self.dirs.add(d)
                if not d:
                    break
                d = os.path.dirname(d)
        self.dir_files: Dict[str, List[str]] = defaultdict(list)
        for f in files:
            self.dir_files[os.path.dirname(f)].append(f)
        self.go_module = self._read_go_module()
        self.java_by_base: Dict[str, List[str]] = defaultdict(list)
        for f in files:
            if f.endswith((".java", ".kt")):
                self.java_by_base[os.path.basename(f)].append(f)
        self.py_roots = [""] + [d for d in ("src", "lib", "python") if d in self.dirs]
        self.top_dirs = {d for d in self.dirs if d and "/" not in d}
        self.rust_crates = self._rust_crates()

    def _read_go_module(self) -> str:
        p = os.path.join(self.root, "go.mod")
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("module "):
                        return line.split()[1].strip()
        except OSError:
            pass
        return ""

    def _rust_crates(self) -> Dict[str, str]:
        crates: Dict[str, str] = {}
        for f in self.fileset:
            if f.endswith("src/lib.rs") or f.endswith("src/main.rs"):
                crate_dir = f.rsplit("/src/", 1)[0] if "/src/" in f else ""
                name = os.path.basename(crate_dir) if crate_dir else os.path.basename(self.root)
                crates[name.replace("-", "_")] = f
        return crates


def _resolve_python(spec: str, ctx: ProjectContext) -> Optional[str]:
    parts = spec.split(".")
    for n in range(len(parts), 0, -1):
        p = "/".join(parts[:n])
        for base in ctx.py_roots:
            prefix = (base + "/") if base else ""
            for cand in (prefix + p + ".py", prefix + p + "/__init__.py"):
                if cand in ctx.fileset:
                    return cand
    return None


def _resolve_js(rel: str, spec: str, ctx: ProjectContext) -> Optional[str]:
    if spec.startswith("@/") or spec.startswith("~/"):
        base = "src/" + spec[2:]
    elif spec.startswith("."):
        base = os.path.normpath(os.path.join(os.path.dirname(rel), spec)).replace(os.sep, "/")
    elif spec.split("/")[0] in ctx.top_dirs:
        base = spec
    else:
        return None
    if base.startswith("./"):
        base = base[2:]
    cands = [base]
    stem = re.sub(r"\.(js|mjs|cjs|jsx)$", "", base)
    if stem != base:
        cands.append(stem)
    for c in list(cands):
        for ext in JS_EXTS:
            cands.append(c + ext)
            cands.append(c + "/index" + ext)
    for c in cands:
        if c in ctx.fileset:
            return c
    return None


def _resolve_go(spec: str, ctx: ProjectContext) -> List[str]:
    if not ctx.go_module or not (spec == ctx.go_module or spec.startswith(ctx.go_module + "/")):
        return []
    d = spec[len(ctx.go_module):].lstrip("/")
    return [f for f in ctx.dir_files.get(d, []) if f.endswith(".go") and not f.endswith("_test.go")]


def _resolve_rust(rel: str, spec: str, ctx: ProjectContext) -> Optional[str]:
    here = os.path.dirname(rel)
    crate_root = rel.rsplit("/src/", 1)[0] + "/src" if "/src/" in rel else ("src" if rel.startswith("src/") else here)
    if spec.startswith("mod:"):
        name = spec[4:]
        stem = os.path.splitext(os.path.basename(rel))[0]
        bases = [here]
        if stem not in ("mod", "lib", "main"):
            bases.insert(0, os.path.join(here, stem))
        for b in bases:
            for cand in (os.path.join(b, name + ".rs"), os.path.join(b, name, "mod.rs")):
                cand = cand.replace(os.sep, "/")
                if cand in ctx.fileset:
                    return cand
        return None
    segs = spec.split("::")
    if segs[0] == "crate":
        base = crate_root
        segs = segs[1:]
    elif segs[0] == "super":
        base = os.path.dirname(here)
        segs = segs[1:]
    elif segs[0] == "self":
        base = here
        segs = segs[1:]
    elif segs[0] in ctx.rust_crates:
        return ctx.rust_crates[segs[0]]
    else:
        return None
    for n in range(len(segs), 0, -1):
        p = os.path.join(base, *segs[:n]).replace(os.sep, "/")
        for cand in (p + ".rs", p + "/mod.rs"):
            if cand in ctx.fileset:
                return cand
    return os.path.join(base, "lib.rs").replace(os.sep, "/") if os.path.join(base, "lib.rs").replace(os.sep, "/") in ctx.fileset else None


def _resolve_java(spec: str, ctx: ProjectContext) -> Optional[str]:
    parts = spec.split(".")
    for n in range(len(parts), 0, -1):
        base = parts[n - 1]
        suffix = "/".join(parts[:n])
        for ext in (".java", ".kt"):
            for f in ctx.java_by_base.get(base + ext, []):
                if f.endswith(suffix + ext):
                    return f
    return None


def _resolve_relative(rel: str, spec: str, ctx: ProjectContext, exts: List[str]) -> Optional[str]:
    base = os.path.normpath(os.path.join(os.path.dirname(rel), spec)).replace(os.sep, "/")
    cands = [base] + [base + e for e in exts]
    for c in cands:
        if c in ctx.fileset:
            return c
    return None


def resolve_imports(info: FileInfo, ctx: ProjectContext) -> (Set[str], Set[str]):
    """Return (internal file targets, external specifiers)."""
    internal: Set[str] = set()
    external: Set[str] = set()
    for spec in info.imports:
        target = None
        targets: List[str] = []
        if info.lang == "python":
            target = _resolve_python(spec, ctx)
        elif info.lang in ("javascript", "typescript"):
            target = _resolve_js(info.rel, spec, ctx)
        elif info.lang == "go":
            targets = _resolve_go(spec, ctx)
        elif info.lang == "rust":
            target = _resolve_rust(info.rel, spec, ctx)
        elif info.lang in ("java", "kotlin"):
            target = _resolve_java(spec, ctx)
        elif info.lang == "ruby":
            if spec.startswith("rel:"):
                target = _resolve_relative(info.rel, spec[4:], ctx, [".rb"])
        elif info.lang in ("c", "cpp"):
            if spec.startswith("rel:"):
                target = _resolve_relative(info.rel, spec[4:], ctx, [])
        if targets:
            internal.update(t for t in targets if t != info.rel)
        elif target and target != info.rel:
            internal.add(target)
        else:
            if not spec.startswith(".") and not spec.startswith("rel:") and not spec.startswith("mod:"):
                external.add(spec.split("/")[0] if info.lang in ("javascript", "typescript") and not spec.startswith("@") else spec.split(".")[0] if info.lang == "python" else spec)
    return internal, external


# ------------------------------------------------------------------------------ git activity

def git_activity(root: str, since_days: int = 90) -> (Dict[str, int], Dict[str, Set[str]]):
    """Return (commits touching each file, commit ids touching each file)."""
    file_commits: Dict[str, Set[str]] = defaultdict(set)
    try:
        out = subprocess.run(
            ["git", "-C", root, "log", f"--since={since_days}.days", "--name-only", "--pretty=format:%H", "--no-renames"],
            capture_output=True, check=True, timeout=120,
        ).stdout.decode("utf-8", errors="replace")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {}, {}
    cur = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[0-9a-f]{40}", line):
            cur = line
        elif cur:
            file_commits[line].add(cur)
    return {f: len(c) for f, c in file_commits.items()}, file_commits


# ------------------------------------------------------------------------------ hints

def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


FRAMEWORK_MARKERS = {
    "react": ["react"], "next": ["next"], "vue": ["vue"], "electron": ["electron"], "vite": ["vite"],
    "express": ["express"], "fastapi": ["fastapi"], "flask": ["flask"], "django": ["django"],
    "pytest": ["pytest"], "gin": ["github.com/gin-gonic/gin"], "echo": ["github.com/labstack/echo"],
    "tokio": ["tokio"], "axum": ["axum"], "actix": ["actix-web"], "streamlit": ["streamlit"],
}


JS_LANGS = ("javascript", "typescript")


def _js_runner(root: str) -> str:
    if os.path.exists(os.path.join(root, "pnpm-lock.yaml")):
        return "pnpm"
    if os.path.exists(os.path.join(root, "yarn.lock")):
        return "yarn"
    if os.path.exists(os.path.join(root, "bun.lockb")) or os.path.exists(os.path.join(root, "bun.lock")):
        return "bun"
    return "npm"


def detect_commands(root: str, files: List[str], manifests: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    """Per-language evidence commands: {"test": .., "typecheck": .., "build": .., "lint": ..} keyed
    by language. Detection is per language, not per repository, so a TypeScript module in a
    repository whose only test runner is pytest is not told to run pytest. The key "*" holds a
    repository-wide fallback (a Makefile test target) used only when a language has none."""
    out: Dict[str, Dict[str, str]] = defaultdict(dict)
    blob = "\n".join(manifests.values()).lower()
    exists = lambda *parts: os.path.exists(os.path.join(root, *parts))  # noqa: E731

    pkg = manifests.get("package.json", "")
    if pkg:
        try:
            scripts = json.loads(pkg).get("scripts", {}) or {}
        except ValueError:
            scripts = {}
        pm = _js_runner(root)
        run = (lambda name: f"{pm} run {name}") if pm != "yarn" else (lambda name: f"yarn {name}")
        js: Dict[str, str] = {}
        if "test" in scripts:
            js["test"] = f"{pm} test"
        if "typecheck" in scripts:
            js["typecheck"] = run("typecheck")
        if "build" in scripts:
            js["build"] = run("build")
        if "lint" in scripts:
            js["lint"] = run("lint")
        for lang in JS_LANGS:
            out[lang].update(js)
        if exists("tsconfig.json") and "typecheck" not in out["typescript"]:
            out["typescript"]["typecheck"] = "npx tsc --noEmit"

    py_markers = ("pytest" in blob or exists("pytest.ini") or exists("conftest.py")
                  or any((f.startswith("tests/") or f.startswith("test/")) and f.endswith(".py") for f in files))
    if py_markers:
        out["python"]["test"] = "pytest"
    elif manifests.get("tox.ini"):
        out["python"]["test"] = "tox"
    if "mypy" in blob:
        out["python"]["typecheck"] = "mypy ."
    elif "pyright" in blob:
        out["python"]["typecheck"] = "pyright"
    if "ruff" in blob:
        out["python"]["lint"] = "ruff check ."
    elif "flake8" in blob:
        out["python"]["lint"] = "flake8"

    if manifests.get("go.mod"):
        if any(f.endswith("_test.go") for f in files):
            out["go"]["test"] = "go test ./..."
        out["go"]["build"] = "go build ./..."
        out["go"]["typecheck"] = "go vet ./..."

    if manifests.get("Cargo.toml"):
        out["rust"]["test"] = "cargo test"
        out["rust"]["build"] = "cargo build"
        out["rust"]["typecheck"] = "cargo check"

    if exists("build.gradle") or exists("build.gradle.kts"):
        for lang in ("java", "kotlin"):
            out[lang]["test"] = "gradle test"
            out[lang]["build"] = "gradle build"
    elif exists("pom.xml"):
        out["java"]["test"] = "mvn test"
        out["java"]["build"] = "mvn package"

    if re.search(r"^test:", manifests.get("Makefile", ""), re.M):
        out["*"]["test"] = "make test"
    if re.search(r"^build:", manifests.get("Makefile", ""), re.M):
        out["*"]["build"] = "make build"
    return {k: dict(v) for k, v in out.items() if v}


def detect_project(root: str, files: List[str]) -> dict:
    manifests = {
        "package.json": _read(os.path.join(root, "package.json")),
        "pyproject.toml": _read(os.path.join(root, "pyproject.toml")),
        "requirements.txt": _read(os.path.join(root, "requirements.txt")),
        "go.mod": _read(os.path.join(root, "go.mod")),
        "Cargo.toml": _read(os.path.join(root, "Cargo.toml")),
        "Makefile": _read(os.path.join(root, "Makefile")),
        "tox.ini": _read(os.path.join(root, "tox.ini")),
    }
    blob = "\n".join(manifests.values()).lower()
    frameworks = [name for name, marks in FRAMEWORK_MARKERS.items() if any(m in blob for m in marks)]
    commands = detect_commands(root, files, manifests)
    name = os.path.basename(os.path.abspath(root))
    pkg = manifests["package.json"]
    if pkg:
        try:
            name = json.loads(pkg).get("name", name) or name
        except ValueError:
            pass
    return {"name": name, "frameworks": sorted(frameworks), "commands": commands,
            "test_commands": {lang: c["test"] for lang, c in commands.items() if c.get("test")},
            "test_command": ""}


def primary_test_command(project: dict) -> str:
    """The test command of the dominant language, else the repository-wide fallback, else the
    first one detected. Kept for callers that want one string; per-module callers should use
    test_command_for."""
    tests = project.get("test_commands", {})
    for lang in project.get("languages", {}):
        if tests.get(lang):
            return tests[lang]
    if tests.get("*"):
        return tests["*"]
    return next(iter(tests.values()), "")


def module_lang(index: dict, mod: str) -> str:
    """Dominant language of a module ("" when unknown)."""
    langs = index["modules"].get(mod, {}).get("languages", {})
    for lang in langs:      # sorted by file count at build time
        if lang and lang != "other":
            return lang
    return ""


def commands_for_lang(index: dict, lang: str) -> Dict[str, str]:
    """Evidence commands for one language: repository-wide fallback, then detected per
    language, then policy overrides from assay.json (policy.commands.<lang>.<kind>).
    Keys among test, typecheck, build, lint; absent when nothing is known."""
    detected = index.get("project", {}).get("commands") or {}
    policy = (index.get("policy") or {}).get("commands") or {}
    out: Dict[str, str] = {}
    for table in (detected, policy):
        out.update(table.get("*") or {})
        out.update(table.get(lang) or {})
    return {k: v for k, v in out.items() if v}


def commands_for(index: dict, mod: str) -> Dict[str, str]:
    """commands_for_lang for a module's dominant language, plus the key "lang"."""
    lang = module_lang(index, mod)
    out = commands_for_lang(index, lang)
    out["lang"] = lang
    return out


def test_command_for(index: dict, mod: str) -> str:
    """Test command that covers a module, or "" when no runner covers its language."""
    return commands_for(index, mod).get("test", "")


def _purpose(root: str, mod: str, infos: List[FileInfo]) -> str:
    d = os.path.join(root, mod) if mod != "." else root
    for readme in ("README.md", "readme.md", "README.rst", "README"):
        text = _read(os.path.join(d, readme))
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("[!") and not s.startswith("<") and len(s) > 10:
                return s[:120]
    for pref in ("__init__.py", "index.ts", "index.js", "mod.rs", "lib.rs", "main.go", "doc.go"):
        for info in infos:
            if os.path.basename(info.rel) == pref and info.doc:
                return info.doc
    for info in infos:
        if info.doc:
            return info.doc
    return os.path.basename(mod) if mod != "." else "repository root"


# ------------------------------------------------------------------------------ build

def build_index(root: str, depth: int = 3, use_cache: bool = True, store: Optional[Store] = None,
                since_days: int = 90, export_cap: int = 25) -> (dict, dict):
    t0 = time.time()
    root = os.path.abspath(root)
    store = store or Store(root)
    files = list_source_files(root)
    cache = store.load_cache() if use_cache else {}
    new_cache: Dict[str, dict] = {}
    infos: Dict[str, FileInfo] = {}
    hashes: Dict[str, str] = {}
    reparsed = cached = 0
    for rel in files:
        path = os.path.join(root, rel)
        try:
            st = os.stat(path)
        except OSError:
            continue
        c = cache.get(rel)
        if c and c.get("size") == st.st_size and c.get("mtime_ns") == st.st_mtime_ns:
            info = FileInfo.from_dict(c["info"])
            h = c["hash"]
            cached += 1
        else:
            with open(path, "rb") as fh:
                data = fh.read()
            h = hashlib.sha256(data).hexdigest()
            text = data.decode("utf-8", errors="replace")
            info = parse_file(rel, text)
            reparsed += 1
        infos[rel] = info
        hashes[rel] = h
        new_cache[rel] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "hash": h, "info": info.to_dict()}
    t_scan = time.time()

    ctx = ProjectContext(root, list(infos))
    file_deps: Dict[str, Set[str]] = {}
    externals: Dict[str, int] = defaultdict(int)
    for rel, info in infos.items():
        internal, external = resolve_imports(info, ctx)
        file_deps[rel] = internal
        for e in external:
            externals[e] += 1

    # modules
    mod_files: Dict[str, List[str]] = defaultdict(list)
    for rel in infos:
        mod_files[module_of(rel, depth)].append(rel)
    module_hashes: Dict[str, str] = {}
    for mod, fl in mod_files.items():
        module_hashes[mod] = sha256_hex("\n".join(f"{f}:{hashes[f]}" for f in sorted(fl)))
    mod_deps: Dict[str, Set[str]] = {m: set() for m in mod_files}
    for rel, targets in file_deps.items():
        m = module_of(rel, depth)
        for t in targets:
            tm = module_of(t, depth)
            if tm != m:
                mod_deps[m].add(tm)
    graph = Graph(mod_deps)
    cone = graph.cone_hashes(module_hashes)

    # activity and risk
    _, commit_sets = git_activity(root, since_days)
    mod_commits: Dict[str, int] = {}
    for mod, fl in mod_files.items():
        s: Set[str] = set()
        for f in fl:
            s |= commit_sets.get(f, set())
        mod_commits[mod] = len(s)
    modules_out: Dict[str, dict] = {}
    for mod in sorted(mod_files):
        fl = sorted(mod_files[mod])
        exports: List[str] = []
        for f in fl:
            exports.extend(infos[f].exports)
        # Risk uses absolute scales so that it is comparable across repositories:
        # 20 commits in 90 days saturates churn, 10 dependents saturates fan-in.
        churn = min(1.0, mod_commits[mod] / CHURN_SATURATION)
        fanin = min(1.0, graph.fan_in(mod) / FANIN_SATURATION)
        risk = round(0.5 * churn + 0.5 * fanin, 3)
        level = "high" if mod_commits[mod] >= 10 else "medium" if mod_commits[mod] >= 3 else "low"
        langs: Dict[str, int] = defaultdict(int)
        for f in fl:
            langs[infos[f].lang or "other"] += 1
        modules_out[mod] = {
            "hash": module_hashes[mod],
            "cone_hash": cone[mod],
            "purpose": _purpose(root, mod, [infos[f] for f in fl]),
            "files": fl,
            "lines": sum(infos[f].lines for f in fl),
            "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
            "exports": exports[:export_cap] + ([f"+{len(exports) - export_cap} more"] if len(exports) > export_cap else []),
            "depends_on": sorted(graph.deps[mod]),
            "depended_by": sorted(graph.dependents[mod]),
            "activity": {"commits_90d": mod_commits[mod], "level": level},
            "risk": risk,
        }

    files_out: Dict[str, dict] = {}
    for rel in sorted(infos):
        info = infos[rel]
        files_out[rel] = {
            "hash": hashes[rel][:16],
            "module": module_of(rel, depth),
            "lang": info.lang,
            "lines": info.lines,
            "imports": sorted(file_deps[rel]),
            "exports": info.exports[:export_cap],
        }

    project = detect_project(root, files)
    lang_counts: Dict[str, int] = defaultdict(int)
    for info in infos.values():
        lang_counts[info.lang or "other"] += 1
    project["languages"] = dict(sorted(lang_counts.items(), key=lambda kv: -kv[1]))
    project["test_command"] = primary_test_command(project)
    # policy (obligation thresholds, command overrides, no-runner fallback) is authored by the
    # repository owner in assay.json and must survive every rebuild
    policy = None
    if store.has_index():
        try:
            policy = store.load_index().get("policy")
        except (OSError, ValueError):
            policy = None
    project["files"] = len(infos)
    project["lines"] = sum(i.lines for i in infos.values())
    project["modules"] = len(modules_out)

    edges = sorted((m, d) for m, ds in mod_deps.items() for d in ds)
    root_hash = sha256_hex("\n".join(f"{m}:{module_hashes[m]}" for m in sorted(module_hashes)))
    most_depended = sorted(modules_out, key=lambda m: (-len(modules_out[m]["depended_by"]), m))[:8]
    hottest = sorted(modules_out, key=lambda m: (-modules_out[m]["activity"]["commits_90d"], m))[:8]

    index = {
        "version": INDEX_VERSION,
        "generator": "assay",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root_hash": root_hash,
        "module_depth": depth,
        "project": project,
        "hints": {
            "test_command": project["test_command"],
            "test_commands": project["test_commands"],
            "most_depended": most_depended,
            "hottest_90d": [m for m in hottest if modules_out[m]["activity"]["commits_90d"] > 0],
            "external_top": [k for k, _ in sorted(externals.items(), key=lambda kv: -kv[1])[:12]],
            "read_first": "Read assay.json (or `assay brief`) before opening source files. Bind every claim with `assay attest`.",
        },
        "modules": modules_out,
        "files": files_out,
        "edges": edges,
    }
    if policy:
        index["policy"] = policy
    if use_cache:
        store.save_cache(new_cache)
    stats = {
        "files": len(infos), "reparsed": reparsed, "cached": cached, "modules": len(modules_out),
        "edges": len(edges), "scan_s": round(t_scan - t0, 4), "total_s": round(time.time() - t0, 4),
    }
    return index, stats


def diff_index(old: dict, new: dict) -> dict:
    """Modules whose content hash changed, plus added and removed modules and files."""
    om, nm = old.get("modules", {}), new.get("modules", {})
    changed = sorted(m for m in nm if m in om and om[m]["hash"] != nm[m]["hash"])
    added = sorted(m for m in nm if m not in om)
    removed = sorted(m for m in om if m not in nm)
    of, nf = old.get("files", {}), new.get("files", {})
    changed_files = sorted(f for f in nf if f in of and of[f]["hash"] != nf[f]["hash"])
    added_files = sorted(f for f in nf if f not in of)
    removed_files = sorted(f for f in of if f not in nf)
    cone_changed = sorted(m for m in nm if m not in om or om[m].get("cone_hash") != nm[m].get("cone_hash"))
    return {
        "changed_modules": changed, "added_modules": added, "removed_modules": removed,
        "changed_files": changed_files, "added_files": added_files, "removed_files": removed_files,
        "cone_changed_modules": cone_changed,
        "root_changed": old.get("root_hash") != new.get("root_hash"),
    }


def modules_for_files(index: dict, files: List[str]) -> List[str]:
    depth = index.get("module_depth", 3)
    mods = set()
    for f in files:
        f = f.replace(os.sep, "/")
        if f in index.get("files", {}):
            mods.add(index["files"][f]["module"])
        elif os.path.splitext(f)[1].lower() in SOURCE_EXTS:
            mods.add(module_of(f, depth))
    return sorted(m for m in mods if m in index["modules"])
