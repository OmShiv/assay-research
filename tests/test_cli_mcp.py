import json
import os

from assay.cli import main
from assay.mcp import Server


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_flow(repo, capsys):
    code, out, _ = run(["--root", repo, "init", "--depth", "2", "--hook", "--agents"], capsys)
    assert code == 0 and "indexed" in out
    assert os.path.exists(os.path.join(repo, "assay.json"))
    assert os.path.exists(os.path.join(repo, ".git", "hooks", "pre-commit"))
    assert "## Assay" in open(os.path.join(repo, "AGENTS.md")).read()

    code, out, _ = run(["--root", repo, "index"], capsys)
    assert code == 0 and "unchanged" in out

    code, out, _ = run(["--root", repo, "blast", "pkg/core"], capsys)
    assert code == 0 and "blast radius: 4/" in out

    code, out, _ = run(["--root", repo, "gate", "--changed", "pkg/util"], capsys)
    assert code == 1 and "FAIL" in out and "missing" in out

    code, out, _ = run(["--root", repo, "attest", "tests-pass", "--subject", "pkg/util,tests", "--run", "python -m pytest -q", "--as", "doer-1"], capsys)
    assert code == 0 and "evidence ok" in out

    code, _, err = run(["--root", repo, "review", "c-0001", "accept", "--as", "doer-1"], capsys)
    assert code == 2 and "separation of duties" in err

    code, out, _ = run(["--root", repo, "review", "c-0001", "accept", "--as", "reviewer-1"], capsys)
    assert code == 0 and "accepted" in out

    code, out, _ = run(["--root", repo, "gate", "--changed", "pkg/util"], capsys)
    assert code == 0 and "PASS" in out

    code, out, _ = run(["--root", repo, "claims"], capsys)
    assert code == 0 and "c-0001" in out and "accepted" in out

    code, out, _ = run(["--root", repo, "brief", "--changed", "pkg/util"], capsys)
    assert code == 0 and out.startswith("# Assay brief") and "Gate: PASS" in out

    code, out, _ = run(["--root", repo, "verify"], capsys)
    assert code == 0 and "0 tampered" in out

    code, out, _ = run(["--root", repo, "export"], capsys)
    assert code == 0 and os.path.exists(os.path.join(repo, "DEPENDENCIES.md"))

    code, out, _ = run(["--root", repo, "find", "Calc"], capsys)
    assert "pkg/service" in out


def test_mcp_server(repo):
    s = Server(repo)
    init = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "assay"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in tools["result"]["tools"]}
    assert {"get_brief", "get_blast_radius", "attest", "review", "gate"} <= names

    def call(name, **args):
        r = s.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return r["result"]

    r = call("get_brief")
    assert not r["isError"] and r["content"][0]["text"].startswith("# Assay brief")
    r = call("get_blast_radius", changed=["pkg/core"])
    ob = json.loads(r["content"][0]["text"])
    assert ob["br_size"] == 4
    r = call("attest", predicate="tests-pass", subjects=["pkg/app", "tests"], command="python -m pytest -q", agent="doer-1")
    cid = json.loads(r["content"][0]["text"])["id"]
    r = call("review", claim=cid, decision="accept", agent="doer-1")
    assert r["isError"] and "separation of duties" in r["content"][0]["text"]
    r = call("review", claim=cid, decision="accept", agent="reviewer-1")
    assert json.loads(r["content"][0]["text"])["status"] == "accepted"
    r = call("gate", changed=["pkg/app"])
    assert json.loads(r["content"][0]["text"])["passed"] is True
    r = call("get_module", path="pkg/service")
    assert "Calculator" in r["content"][0]["text"]
    r = call("nope")
    assert r["isError"]
