"""agent-mtg: coordinatorの auto_apply を、限定された安全な操作だけで反映する。

費用・ポリシーに触れる変更は絶対にここを通さない（coordinatorのプロンプト側で
auto_apply に入れないよう指示済みだが、ここでも構造的に不可能にする）。
許可するのは3種類のみ：
  - add_concept_tag: config/planning.yaml の <brand>.concept_tags に1件追加
  - add_hook_type:   config/planning.yaml の <brand>.hook_types に1件追加
  - set_hashtags:    config/hashtags.yaml の <brand>.<platform>.pool を置き換え
                      （always・per_video は触らない。ブランドの核タグを守るため）

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
    "set_hashtags": lambda item: apply_set_hashtags(
        item.get("brand"), item.get("platform"), item.get("tags"),
    ),
}


def apply_all(auto_apply: list[dict]) -> list[str]:
    """coordinatorのauto_applyリストを順に適用する。1件の失敗で全体を止めない。"""
    results = []
    for item in auto_apply:
        kind = item.get("kind")
        handler = _HANDLERS.get(kind)
        if handler is None:
            results.append(f"[rejected] 未知のkind: {kind!r}（許可された3種類以外は無視）")
            continue
        try:
            results.append(handler(item))
        except ApplyError as e:
            results.append(f"[rejected] {kind}: {e}")
    return results
