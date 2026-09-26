"""On-disk layout and locking for an Assay-enabled repository.

    <root>/assay.json            the index (committed)
    <root>/.assay/ledger.jsonl   append-only claim ledger (committed; the audit trail)
    <root>/.assay/cache.json     per-file hash and parse cache for Merkle skipping (ignored)
    <root>/.assay/key            HMAC key used to sign ledger entries (ignored, mode 600)
    <root>/.assay/lock           flock target for atomic ledger appends (ignored)
"""

import contextlib
import fcntl
import json
import os
import secrets
import tempfile

INDEX_FILE = "assay.json"
STATE_DIR = ".assay"


def _atomic_write(path: str, data: str) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".assay-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.chmod(tmp, 0o644)   # mkstemp creates 0600; the index is a shared, committed file
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.index_path = os.path.join(self.root, INDEX_FILE)
        self.state_dir = os.path.join(self.root, STATE_DIR)
        self.ledger_path = os.path.join(self.state_dir, "ledger.jsonl")
        self.cache_path = os.path.join(self.state_dir, "cache.json")
        self.key_path = os.path.join(self.state_dir, "key")
        self.lock_path = os.path.join(self.state_dir, "lock")

    # ---- layout -----------------------------------------------------------------
    def ensure(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        gi = os.path.join(self.state_dir, ".gitignore")
        if not os.path.exists(gi):
            with open(gi, "w", encoding="utf-8") as fh:
                fh.write("key\ncache.json\nlock\n")

    @contextlib.contextmanager
    def lock(self):
        """Exclusive lock for read-modify-write of the ledger. Hold time must stay small."""
        self.ensure()
        with open(self.lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ---- index --------------------------------------------------------------------
    def has_index(self) -> bool:
        return os.path.exists(self.index_path)

    def load_index(self) -> dict:
        with open(self.index_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_index(self, index: dict) -> None:
        _atomic_write(self.index_path, json.dumps(index, indent=1, sort_keys=False) + "\n")

    # ---- cache --------------------------------------------------------------------
    def load_cache(self) -> dict:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def save_cache(self, cache: dict) -> None:
        self.ensure()
        _atomic_write(self.cache_path, json.dumps(cache, separators=(",", ":")))

    # ---- signing key ----------------------------------------------------------------
    def key(self) -> bytes:
        """HMAC key. Taken from ASSAY_KEY when set (so a supervisor can hold it outside the
        agent's reach), otherwise from .assay/key, created on first use."""
        env = os.environ.get("ASSAY_KEY")
        if env:
            return env.encode("utf-8")
        self.ensure()
        if not os.path.exists(self.key_path):
            with open(self.key_path, "w", encoding="utf-8") as fh:
                fh.write(secrets.token_hex(32))
            os.chmod(self.key_path, 0o600)
        with open(self.key_path, "r", encoding="utf-8") as fh:
            return fh.read().strip().encode("utf-8")
