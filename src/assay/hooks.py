"""Git hook installation and agent configuration snippets."""

import os
import stat
import sys

from .index import commands_for_lang

PRE_COMMIT = """#!/bin/sh
# assay pre-commit hook: refresh the index incrementally (Merkle skip) and stage it.
# Prefers the interpreter that installed the hook (it is known to have Assay), then an
# `assay` on PATH, then python3.
if [ -x "{python}" ] && "{python}" -c "import assay" >/dev/null 2>&1; then ASSAY="{python} -m assay";
elif command -v assay >/dev/null 2>&1; then ASSAY=assay;
else ASSAY="python3 -m assay"; fi
$ASSAY index --quiet || exit 0
git add assay.json 2>/dev/null
exit 0
"""

AGENT_SNIPPET = """## Assay

This repository is indexed by Assay. Before reading source files run `assay brief` (or the MCP
tool `get_brief`) and read `assay.json` for module purposes, exports, and dependencies.
Every claim you make about the code must be attested with evidence:

    assay attest tests-pass --subject <module> --run "<test command>" --as <your-agent-name>

Use the test command of the module's language (`assay module <path>` prints it under
"commands"). Where a language has no test runner the gate requires build-ok and
typecheck-clean instead of tests-pass; `assay gate` lists exactly what is missing.
{commands}Claims are bound to content hashes and go stale when the code they cover (or anything it
depends on) changes. A change merges only when `assay gate` passes: fresh, independently
reviewed evidence for the whole blast radius. Never review your own claims.
"""


def commands_block(index: dict) -> str:
    """Markdown lines listing the detected evidence commands per language, for AGENTS.md."""
    if not index:
        return ""
    p = index.get("project", {})
    rows = []
    for lang in [l for l in p.get("languages", {}) if l != "other"]:
        cmds = commands_for_lang(index, lang)
        parts = [f"{k} `{cmds[k]}`" for k in ("test", "typecheck", "build", "lint") if cmds.get(k)]
        if not cmds.get("test"):
            parts.append("no test runner (gate asks for build-ok and typecheck-clean)")
        rows.append(f"- {lang}: " + ", ".join(parts))
    if not rows:
        return ""
    return "Evidence commands by language, detected at index time:\n\n" + "\n".join(rows) + "\n\n"


def install_pre_commit(root: str) -> str:
    hooks = os.path.join(root, ".git", "hooks")
    if not os.path.isdir(hooks):
        raise FileNotFoundError("not a git repository (no .git/hooks)")
    path = os.path.join(hooks, "pre-commit")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(PRE_COMMIT.format(python=sys.executable))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def write_agent_snippet(root: str, filename: str = "AGENTS.md", index: dict = None) -> str:
    """Append the Assay block to AGENTS.md (or replace an existing block so the command table
    tracks the index)."""
    path = os.path.join(root, filename)
    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    block = AGENT_SNIPPET.format(commands=commands_block(index))
    if "## Assay" in existing:
        start = existing.index("## Assay")
        rest = existing[start + len("## Assay"):]
        nxt = rest.find("\n## ")
        end = len(existing) if nxt < 0 else start + len("## Assay") + nxt + 1
        new = existing[:start] + block + existing[end:]
        if new != existing:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
        return path
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(("\n" if existing and not existing.endswith("\n") else "") + block)
    return path
