"""agent-mtg: coordinatorの auto_apply を、限定された安全な操作だけで反映する。

費用・ポリシーに触れる変更は絶対にここを通さない（coordinatorのプロンプト側で
auto_apply に入れないよう指示済みだが、ここでも構造的に不可能にする）。
許可する操作（いずれも「1日6本・尺・投稿頻度・広告」は一切変えない＝費用不変）：
  - add_concept_tag / add_hook_type: config/planning.yaml のプールに1件追加
  - retire_concept_tag / retire_hook_type: 伸びない企画/フックをプールから外す
                      （最低数は残す。cat/dog の核を空にしない）
  - set_hashtags:    config/hashtags.yaml の <brand>.<platform>.pool を置き換え
                      （always・per_video は触らない）
  - set_level_range: config/planning.yaml の <brand>.reality_level / oddity_level の
                      [min,max] を差し替え（1〜5 の範囲、min<=max）
  - set_allocation_ratio: config/scoring.yaml の allocation.exploit_ratio /
                      explore_ratio（勝ちパターン活用 vs 新規探索の比率。合計1・
                      exploit は 0.3〜0.9 に制限）

コメント・整形を保つため ruamel.yaml のラウンドトリップローダを使う。
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
_VALID_BRANDS = {"cat", "dog"}
_VALID_PLATFORMS = {"youtube", "tiktok", "instagram", "x"}

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096  # 長い日本語コメント行を折り返させない


class ApplyError(Exception):
    pass


def _load(path: Path):
    with path.open(encoding="utf-8") as fh:
        return _yaml.load(fh)


def _dump(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as fh:
        _yaml.dump(data, fh)


def _require_brand(brand: str) -> str:
    if brand not in _VALID_BRANDS:
        raise ApplyError(f"未知のbrand: {brand!r}（cat/dogのみ許可）")
    return brand


def apply_add_concept_tag(brand: str, tag: str) -> str:
    brand = _require_brand(brand)
    if not tag or not isinstance(tag, str):
        raise ApplyError("tag が空、または文字列でない")
    path = CONFIG_DIR / "planning.yaml"
    data = _load(path)
    tags = data["brands"][brand]["concept_tags"]
    if tag in tags:
        return f"[skip] {brand}.concept_tags に既に '{tag}' がある"
    tags.append(tag)
    _dump(path, data)
    return f"[applied] {brand}.concept_tags に '{tag}' を追加"


def apply_add_hook_type(brand: str, hook: str) -> str:
    brand = _require_brand(brand)
    if not hook or not isinstance(hook, str):
        raise ApplyError("hook が空、または文字列でない")
    path = CONFIG_DIR / "planning.yaml"
    data = _load(path)
    hooks = data["brands"][brand]["hook_types"]
    if hook in hooks:
        return f"[skip] {brand}.hook_types に既に '{hook}' がある"
    hooks.append(hook)
    _dump(path, data)
    return f"[applied] {brand}.hook_types に '{hook}' を追加"


_MIN_CONCEPT_TAGS = 3
_MIN_HOOK_TYPES = 2


def apply_retire_concept_tag(brand: str, tag: str) -> str:
    brand = _require_brand(brand)
    path = CONFIG_DIR / "planning.yaml"
    data = _load(path)
    tags = data["brands"][brand]["concept_tags"]
    if tag not in tags:
        return f"[skip] {brand}.concept_tags に '{tag}' は無い"
    if len(tags) <= _MIN_CONCEPT_TAGS:
        raise ApplyError(f"{brand}.concept_tags が最低数({_MIN_CONCEPT_TAGS})なので削除しない")
    tags.remove(tag)
    _dump(path, data)
    return f"[applied] {brand}.concept_tags から '{tag}' を引退"


def apply_retire_hook_type(brand: str, hook: str) -> str:
    brand = _require_brand(brand)
    path = CONFIG_DIR / "planning.yaml"
    data = _load(path)
    hooks = data["brands"][brand]["hook_types"]
    if hook not in hooks:
        return f"[skip] {brand}.hook_types に '{hook}' は無い"
    if len(hooks) <= _MIN_HOOK_TYPES:
        raise ApplyError(f"{brand}.hook_types が最低数({_MIN_HOOK_TYPES})なので削除しない")
    hooks.remove(hook)
    _dump(path, data)
    return f"[applied] {brand}.hook_types から '{hook}' を引退"


def apply_set_level_range(brand: str, dimension: str, lo, hi) -> str:
    brand = _require_brand(brand)
    key = {"reality": "reality_level", "oddity": "oddity_level"}.get(dimension)
    if key is None:
        raise ApplyError(f"未知のdimension: {dimension!r}（reality / oddity のみ）")
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError) as e:
        raise ApplyError("min/max が整数でない") from e
    if not (1 <= lo <= hi <= 5):
        raise ApplyError(f"範囲が不正: [{lo},{hi}]（1<=min<=max<=5）")
    path = CONFIG_DIR / "planning.yaml"
    data = _load(path)
    data["brands"][brand][key] = [lo, hi]
    _dump(path, data)
    return f"[applied] {brand}.{key} を [{lo}, {hi}] に"


def apply_set_allocation_ratio(exploit, explore) -> str:
    try:
        exploit, explore = float(exploit), float(explore)
    except (TypeError, ValueError) as e:
        raise ApplyError("exploit/explore が数値でない") from e
    if abs(exploit + explore - 1.0) > 0.001:
        raise ApplyError(f"exploit+explore が1にならない（{exploit}+{explore}）")
    if not (0.3 <= exploit <= 0.9):
        raise ApplyError(f"exploit_ratio は 0.3〜0.9（指定: {exploit}）")
    path = CONFIG_DIR / "scoring.yaml"
    data = _load(path)
    data["allocation"]["exploit_ratio"] = round(exploit, 2)
    data["allocation"]["explore_ratio"] = round(explore, 2)
    _dump(path, data)
    return f"[applied] allocation を 活用{exploit:.0%}/探索{explore:.0%} に"


def apply_set_hashtags(brand: str, platform: str, tags: list[str]) -> str:
    brand = _require_brand(brand)
    if platform not in _VALID_PLATFORMS:
        raise ApplyError(f"未知のplatform: {platform!r}")
    if not tags or not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ApplyError("tags が空、またはリストでない")
    bad = [t for t in tags if not t.startswith("#")]
    if bad:
        raise ApplyError(f"'#'で始まらないタグがある: {bad}")
    path = CONFIG_DIR / "hashtags.yaml"
    data = _load(path)
    entry = data[brand][platform]
    entry["pool"] = list(tags)  # always / per_video は触らない
    _dump(path, data)
    return f"[applied] {brand}/{platform} のハッシュタグpoolを{len(tags)}件に更新"


_HANDLERS = {
    "add_concept_tag": lambda item: apply_add_concept_tag(item.get("brand"), item.get("tag")),
    "add_hook_type": lambda item: apply_add_hook_type(item.get("brand"), item.get("hook")),
    "retire_concept_tag": lambda item: apply_retire_concept_tag(
        item.get("brand"), item.get("tag"),
    ),
    "retire_hook_type": lambda item: apply_retire_hook_type(
        item.get("brand"), item.get("hook"),
    ),
    "set_hashtags": lambda item: apply_set_hashtags(
        item.get("brand"), item.get("platform"), item.get("tags"),
    ),
    "set_level_range": lambda item: apply_set_level_range(
        item.get("brand"), item.get("dimension"), item.get("min"), item.get("max"),
    ),
    "set_allocation_ratio": lambda item: apply_set_allocation_ratio(
        item.get("exploit"), item.get("explore"),
    ),
}


def apply_all(auto_apply: list[dict]) -> list[str]:
    """coordinatorのauto_applyリストを順に適用する。1件の失敗で全体を止めない。"""
    results = []
    for item in auto_apply:
        kind = item.get("kind")
        handler = _HANDLERS.get(kind)
        if handler is None:
            results.append(f"[rejected] 未知のkind: {kind!r}（許可された操作以外は無視）")
            continue
        try:
            results.append(handler(item))
        except ApplyError as e:
            results.append(f"[rejected] {kind}: {e}")
    return results
