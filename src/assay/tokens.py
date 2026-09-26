"""Token estimation.

Uses tiktoken (cl100k_base) when it is installed, otherwise a documented heuristic of
four characters per token, which is within roughly 15 percent of BPE counts for source
code and English prose. All comparisons in the experiments use the same estimator, so
relative numbers are stable even when the heuristic is used.
"""

_ENC = None


def _encoder():
    global _ENC
    if _ENC is None:
        try:
            import tiktoken  # type: ignore

            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:  # pragma: no cover - depends on environment
            _ENC = False
    return _ENC or None


def estimator_name() -> str:
    return "tiktoken/cl100k_base" if _encoder() is not None else "heuristic/chars-div-4"


def estimate_tokens(text: str) -> int:
    enc = _encoder()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    return max(1, (len(text) + 3) // 4)
