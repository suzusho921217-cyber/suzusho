"""ハッシュタグ ＆ 視聴者参加キャプションの選定（§9 publish 段階）。

`config/hashtags.yaml` の `always`（毎回）＋ `pool` から日付シードで `per_video` 本。
`config/captions.yaml` から concept 相性 or general の CTA を1つ。
どれも (brand/concept, platform, date) が同じなら決定的。learning とは別レイヤー。
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping
from typing import Any

from src.common.config import load


def _seed(*parts: str) -> int:
    return int(hashlib.sha1("|".join(parts).encode()).hexdigest()[:8], 16)


def select_caption_cta(
    concept_tag: str,
    *,
    date: str,
    config: Mapping[str, Any] | None = None,
) -> str:
    """視聴者参加をうながす一言。concept 相性 > general。無ければ空文字。"""
    cfg = config if config is not None else load("captions")
    pool = list((cfg.get("by_concept") or {}).get(concept_tag) or [])
    pool = pool + list(cfg.get("general") or [])
    if not pool:
        return ""
    return pool[_seed(concept_tag, date) % len(pool)]


def select_hashtags(
    brand: str,
    platform: str,
    *,
    date: str,
    config: Mapping[str, Any] | None = None,
) -> list[str]:
    cfg = (config if config is not None else load("hashtags"))
    spec = ((cfg.get(brand) or {}).get(platform) or {})
    tags: list[str] = list(spec.get("always") or [])

    pool = list(spec.get("pool") or [])
    n = int(spec.get("per_video", 3))
    if pool and n > 0:
        rng = random.Random(_seed(brand, platform, date))
        rng.shuffle(pool)
        tags += pool[:n]

    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out
