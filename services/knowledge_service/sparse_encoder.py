from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any


_ASCII_TOKEN = re.compile(r"[a-zA-Z0-9_]+")
_CJK_START = 0x4E00
_CJK_END = 0x9FFF
_INDEX_BITS = 2**31 - 1


def tokenize(text: str) -> list[str]:
    """Mixed tokenization for sparse lexical vectors.

    ASCII words become lowercase tokens; CJK text is split into bigrams so the
    sparse channel still matches Chinese security terms without a dictionary.
    """
    tokens: list[str] = []
    for match in _ASCII_TOKEN.finditer(text):
        token = match.group(0).lower()
        if len(token) >= 2:
            tokens.append(token)
    cjk = [ch for ch in text if _CJK_START <= ord(ch) <= _CJK_END]
    tokens.extend(
        cjk[index] + cjk[index + 1]
        for index in range(len(cjk) - 1)
    )
    return tokens


def sparse_encode(
    text: str,
    *,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """Bag-of-words sparse vector with sublinear term frequency.

    Token indices are derived from SHA-256 so stored points stay comparable
    across processes, unlike Python's randomized string hashing.
    """
    counts = Counter(tokenize(text))
    pairs: list[tuple[int, float]] = []
    for token, count in counts.most_common(max_tokens):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % _INDEX_BITS
        pairs.append((index, 1.0 + math.log(count)))
    pairs.sort(key=lambda pair: pair[0])
    return {
        "indices": [index for index, _ in pairs],
        "values": [value for _, value in pairs],
    }
