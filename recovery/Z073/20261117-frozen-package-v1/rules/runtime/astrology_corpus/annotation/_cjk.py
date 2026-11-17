"""Canonical CJK detection for English-field write guards (single source of truth).

Used by both the write-side Pydantic validator (``schemas.AnnotatedChunkV2``'s
``summary_en`` / ``keywords_en`` CJK guard) and the scripts-side merge/audit
guards (``scripts/_summary_en_shared.has_cjk``), so the two never drift.

Rationale: see ``docs/plan-corpus-english-annotation-consumption-v1.md`` §2.5
(pre-shard 预填陷阱) — English metadata fields must stay CJK-free or they poison
dense embedding + BM25 sparse (an English query can never match a Chinese token).
"""

from __future__ import annotations

import re

# Explicit \u escapes (pure ASCII source) so the ranges are unambiguous: the glyph
# 豈 has two visually-identical codepoints (U+8C48 unified vs U+F900 compatibility);
# the compatibility-block start U+F900 is the intended one. This compiles to a class
# byte-equivalent to scripts/_summary_en_shared.py's ``_CJK_RE`` — asserted identical
# by a full-BMP drift test (tests/scripts/test_summary_en_shared.py).
_CJK_RE = re.compile(
    "["
    "　-〿"  # CJK symbols & punctuation
    "㐀-䶿"  # CJK ext-A
    "一-鿿"  # CJK unified ideographs
    "豈-﫿"  # CJK compatibility ideographs
    "＀-￯"  # halfwidth & fullwidth forms
    "]"
)


def contains_cjk(text: object) -> bool:
    """True if *text* is a str containing any CJK ideograph / punct / fullwidth char.

    Non-str inputs return ``False`` (validators may receive odd values before
    coercion; we only assert on real strings).
    """
    if not isinstance(text, str):
        return False
    return _CJK_RE.search(text) is not None
