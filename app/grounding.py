"""Grounding check: every finding must quote the source. If the quote isn't
really there, the finding is an unsupported claim and is NOT shown as fact."""
import re
from difflib import SequenceMatcher


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%/.]", " ", s.lower())).strip()


def verify_quote(quote: str, source: str, fuzzy: float = 0.9):
    """Return (status, score). status: exact | fuzzy | missing."""
    q, s = _norm(quote), _norm(source)
    if not q:
        return "missing", 0.0
    if q in s:
        return "exact", 1.0
    # fuzzy: best window of similar length (handles tiny whitespace/typo drift)
    L = len(q)
    best = 0.0
    step = max(1, L // 4)
    for i in range(0, max(1, len(s) - L + 1), step):
        r = SequenceMatcher(None, q, s[i:i + L]).ratio()
        best = max(best, r)
        if best >= fuzzy:
            return "fuzzy", best
    return "missing", best
