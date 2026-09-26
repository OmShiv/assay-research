import os
import subprocess
import sys
import textwrap

import pytest

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(text))
    return path


@pytest.fixture
def repo(tmp_path):
    """A small Python project: app -> service -> core, util standalone, tests import app."""
    root = str(tmp_path / "proj")
    os.makedirs(root)
    write(root, "pkg/core/__init__.py", '"""Core primitives."""\nfrom .math import add\n__all__ = ["add"]\n')
    write(root, "pkg/core/math.py", "def add(a, b):\n    return a + b\n\ndef _hidden():\n    pass\n")
    write(root, "pkg/service/__init__.py", "")
    write(root, "pkg/service/calc.py", "from pkg.core import add\n\nclass Calculator:\n    def total(self, xs):\n        t = 0\n        for x in xs:\n            t = add(t, x)\n        return t\n")
    write(root, "pkg/app/__init__.py", "")
    write(root, "pkg/app/main.py", "from pkg.service.calc import Calculator\n\ndef run(xs):\n    return Calculator().total(xs)\n")
    write(root, "pkg/util/__init__.py", "")
    write(root, "pkg/util/strings.py", "def shout(s):\n    return s.upper()\n")
    write(root, "tests/__init__.py", "")
    write(root, "tests/test_app.py", "from pkg.app.main import run\n\ndef test_run():\n    assert run([1, 2, 3]) == 6\n")
    write(root, "tests/test_util.py", "from pkg.util.strings import shout\n\ndef test_shout():\n    assert shout('a') == 'A'\n")
    write(root, "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", root, "add", "."], check=True)
    subprocess.run(["git", "-C", root, "commit", "-q", "-m", "init"], check=True)
    return root
