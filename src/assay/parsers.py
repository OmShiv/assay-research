"""Lightweight per-language extraction of imports and exports.

Python uses the standard library AST. Other languages use conservative regular
expressions that recover import specifiers and top-level exported names. The goal is a
useful module graph at zero dependency cost, not a full semantic analysis; a tree-sitter
backend can replace any of these functions without touching the rest of the system.
"""

import ast
import os
import re
from dataclasses import dataclass, field
from typing import List

LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".cxx": "cpp",
}

SOURCE_EXTS = set(LANG_BY_EXT)


@dataclass
class FileInfo:
    rel: str
    lang: str
    lines: int
    imports: List[str] = field(default_factory=list)   # raw specifiers, language specific
    exports: List[str] = field(default_factory=list)   # exported names, "f()" for callables
    doc: str = ""                                      # first docstring or comment line

    def to_dict(self) -> dict:
        return {"rel": self.rel, "lang": self.lang, "lines": self.lines,
                "imports": self.imports, "exports": self.exports, "doc": self.doc}

    @classmethod
    def from_dict(cls, d: dict) -> "FileInfo":
        return cls(d["rel"], d["lang"], d["lines"], list(d.get("imports", [])),
                   list(d.get("exports", [])), d.get("doc", ""))


def detect_lang(rel: str) -> str:
    return LANG_BY_EXT.get(os.path.splitext(rel)[1].lower(), "")


# ----------------------------------------------------------------------------- python

def _py_package_of(rel: str) -> List[str]:
    parts = rel.split("/")[:-1]
    return parts


def _parse_python(rel: str, text: str, info: FileInfo) -> None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return
    pkg = _py_package_of(rel)
    imports: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base: List[str]
            if node.level and node.level > 0:
                # relative import: level 1 is the current package
                cut = len(pkg) - (node.level - 1)
                base = pkg[:max(cut, 0)]
                if node.module:
                    base = base + node.module.split(".")
            else:
                base = node.module.split(".") if node.module else []
            if base:
                imports.append(".".join(base))
            for alias in node.names:
                if alias.name != "*":
                    imports.append(".".join(base + [alias.name]))
    exports: List[str] = []
    all_names = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                exports.append(node.name + "()")
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                exports.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                    all_names = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if all_names:
        exports = [n + "()" if any(e == n + "()" for e in exports) else n for n in all_names]
    doc = ast.get_docstring(tree) or ""
    info.imports = _dedupe(imports)
    info.exports = _dedupe(exports)
    info.doc = doc.strip().splitlines()[0][:120] if doc.strip() else ""


# ----------------------------------------------------------------------------- js / ts

_JS_IMPORT = re.compile(r"""(?:^|[^\w$])import\s+(?:type\s+)?(?:[\w*\s{},$]*?\s+from\s+)?['"]([^'"]+)['"]""", re.M)
_JS_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
_JS_DYNIMPORT = re.compile(r"""import\(\s*['"]([^'"]+)['"]\s*\)""")
_JS_REEXPORT = re.compile(r"""export\s+(?:\*|\{[^}]*\})\s+from\s+['"]([^'"]+)['"]""")
_JS_EXPORT_DECL = re.compile(
    r"""export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?(function\*?|class|const|let|var|interface|type|enum|namespace)\s+([A-Za-z_$][\w$]*)"""
)
_JS_EXPORT_LIST = re.compile(r"""export\s*\{([^}]*)\}(?!\s*from)""")
_JS_MODULE_EXPORTS = re.compile(r"""module\.exports\s*=\s*([A-Za-z_$][\w$]*)""")
_JS_EXPORTS_DOT = re.compile(r"""(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=""")
_JS_EXPORT_DEFAULT_ANON = re.compile(r"""export\s+default\s+(?:async\s+)?(?:function|class)\b(?!\s+[A-Za-z_$])""")
_LINE_COMMENT = re.compile(r"^\s*(?://|/\*+|\*)\s*(.*)$")


def _parse_js(rel: str, text: str, info: FileInfo) -> None:
    imports = []
    for rx in (_JS_IMPORT, _JS_REQUIRE, _JS_DYNIMPORT, _JS_REEXPORT):
        imports.extend(rx.findall(text))
    exports = []
    for m in _JS_EXPORT_DECL.finditer(text):
        exports.append(m.group(2) + ("()" if m.group(1).startswith("function") else ""))
    for m in _JS_EXPORT_LIST.finditer(text):
        for name in m.group(1).split(","):
            name = name.strip()
            if not name:
                continue
            name = name.split(" as ")[-1].strip()
            if re.match(r"^[A-Za-z_$][\w$]*$", name):
                exports.append(name)
    for m in _JS_MODULE_EXPORTS.finditer(text):
        exports.append(m.group(1))
    for m in _JS_EXPORTS_DOT.finditer(text):
        exports.append(m.group(1))
    if _JS_EXPORT_DEFAULT_ANON.search(text):
        exports.append("default")
    info.imports = _dedupe(imports)
    info.exports = _dedupe(exports)
    info.doc = _first_comment(text)


# ----------------------------------------------------------------------------- go

_GO_IMPORT_SINGLE = re.compile(r"""^import\s+(?:\w+\s+)?"([^"]+)"\s*$""", re.M)
_GO_IMPORT_BLOCK = re.compile(r"""^import\s*\((.*?)\)""", re.M | re.S)
_GO_IMPORT_IN_BLOCK = re.compile(r"""(?:\w+\s+)?"([^"]+)\"""")
_GO_EXPORT = re.compile(r"""^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)|^type\s+([A-Z]\w*)|^(?:var|const)\s+([A-Z]\w*)""", re.M)


def _parse_go(rel: str, text: str, info: FileInfo) -> None:
    imports = _GO_IMPORT_SINGLE.findall(text)
    for block in _GO_IMPORT_BLOCK.findall(text):
        imports.extend(_GO_IMPORT_IN_BLOCK.findall(block))
    exports = []
    for m in _GO_EXPORT.finditer(text):
        name = m.group(1) or m.group(2) or m.group(3)
        exports.append(name + "()" if m.group(1) else name)
    info.imports = _dedupe(imports)
    info.exports = _dedupe(exports)
    info.doc = _first_comment(text)


# ----------------------------------------------------------------------------- rust

_RS_USE = re.compile(r"""^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([A-Za-z_][\w:]*)""", re.M)
_RS_MOD = re.compile(r"""^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;""", re.M)
_RS_EXPORT = re.compile(
    r"""^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?(fn|struct|enum|trait|type|const|static|mod|macro_rules!)\s+([A-Za-z_]\w*)""",
    re.M,
)


def _parse_rust(rel: str, text: str, info: FileInfo) -> None:
    imports = _RS_USE.findall(text)
    imports.extend("mod:" + m for m in _RS_MOD.findall(text))
    exports = []
    for kind, name in _RS_EXPORT.findall(text):
        exports.append(name + "()" if kind == "fn" else name)
    info.imports = _dedupe(imports)
    info.exports = _dedupe(exports)
    info.doc = _first_comment(text)


# ----------------------------------------------------------------------------- java / kotlin

_JAVA_IMPORT = re.compile(r"""^\s*import\s+(?:static\s+)?([\w.]+)""", re.M)
_JAVA_EXPORT = re.compile(r"""\bpublic\s+(?:abstract\s+|final\s+|static\s+|sealed\s+|data\s+|open\s+)*(?:class|interface|enum|record|object|fun)\s+([A-Za-z_]\w*)""")


def _parse_java(rel: str, text: str, info: FileInfo) -> None:
    info.imports = _dedupe(_JAVA_IMPORT.findall(text))
    info.exports = _dedupe(_JAVA_EXPORT.findall(text))
    info.doc = _first_comment(text)


# ----------------------------------------------------------------------------- ruby

_RB_REQ_REL = re.compile(r"""require_relative\s+['"]([^'"]+)['"]""")
_RB_REQ = re.compile(r"""^\s*require\s+['"]([^'"]+)['"]""", re.M)
_RB_EXPORT = re.compile(r"""^\s*(?:class|module)\s+([A-Z][\w:]*)""", re.M)


def _parse_ruby(rel: str, text: str, info: FileInfo) -> None:
    info.imports = _dedupe(["rel:" + x for x in _RB_REQ_REL.findall(text)] + _RB_REQ.findall(text))
    info.exports = _dedupe(_RB_EXPORT.findall(text))
    info.doc = _first_comment(text)


# ----------------------------------------------------------------------------- c / c++

_C_INCLUDE = re.compile(r"""^\s*#\s*include\s+"([^"]+)\"""", re.M)


def _parse_c(rel: str, text: str, info: FileInfo) -> None:
    info.imports = _dedupe(["rel:" + x for x in _C_INCLUDE.findall(text)])
    info.exports = []
    info.doc = _first_comment(text)


# ----------------------------------------------------------------------------- generic

_PARSERS = {
    "python": _parse_python,
    "javascript": _parse_js,
    "typescript": _parse_js,
    "go": _parse_go,
    "rust": _parse_rust,
    "java": _parse_java,
    "kotlin": _parse_java,
    "ruby": _parse_ruby,
    "c": _parse_c,
    "cpp": _parse_c,
}


def _dedupe(items):
    seen = set()
    out = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _first_comment(text: str) -> str:
    for line in text.splitlines()[:15]:
        m = _LINE_COMMENT.match(line)
        if m and m.group(1).strip():
            s = m.group(1).strip().strip("/*! ")
            if len(s) > 3:
                return s[:120]
    return ""


def parse_file(rel: str, text: str) -> FileInfo:
    lang = detect_lang(rel)
    info = FileInfo(rel=rel, lang=lang, lines=text.count("\n") + (1 if text and not text.endswith("\n") else 0))
    fn = _PARSERS.get(lang)
    if fn is not None:
        fn(rel, text, info)
    return info
