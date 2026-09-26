"""Assay: content-addressed evidence graphs for accountable AI-assisted software delivery.

Assay fuses two ideas that are usually built as separate tools:

* a compact, content-addressed index of a codebase (what an agent needs to *know*), and
* a ledger of evidence-backed claims with an adversarial review protocol and a mechanical
  merge gate (what an agent must *prove*).

The fusion happens through one primitive: every claim is bound to the Merkle hash of the
dependency cone of the code it talks about. When that code (or anything it depends on)
changes, the claim goes stale automatically. Claims decay with the code.
"""

__version__ = "0.1.0"

from .index import build_index, diff_index  # noqa: F401
from .graph import Graph  # noqa: F401
from .ledger import Ledger, ProtocolError  # noqa: F401
from .obligation import evidence_obligation, LEVEL_NAMES  # noqa: F401
from .gate import evaluate_gate  # noqa: F401
from .brief import make_brief  # noqa: F401
