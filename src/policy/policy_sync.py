"""policy_sync: 媒体ポリシー/規約の更新監視（§5 §8）。

RSS/Atom フィードを取得し、前回から新着エントリがあれば「ポリシー変更の可能性」として
該当媒体を stale にする（is_policy_stale が効いて publish が止まる）。

コンテンツポリシー本体（性的表現・AI開示など）は機械可読フィードが無いため、
config/policy_sync.yaml の manual_review_urls を人手で定期確認する（§20）。

このモジュールは純粋: ネットワーク取得は呼び出し側（cli）が fetch 関数として注入する。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

_ATOM = "{http://www.w3.org/2005/Atom}"
# 覚えておく entry_id の上限。changelog は多くても数百件なので実質「全部」。
# フィードのページ落ち（古いエントリが末尾から消える）で再フラグしないよう、
# 現在のエントリと過去の記録をマージして保持する。
_MAX_REMEMBERED = 5000


@dataclass(frozen=True)
class FeedEntry:
    entry_id: str        # guid / Atom id（無ければ link, title）。差分判定の安定キー
    title: str
    link: str
    updated: str         # 生の日付文字列（表示用。比較には使わない）


def parse_feed(xml_text: str) -> list[FeedEntry]:
    """Atom（<feed>）と RSS 2.0（<rss>）の両方に対応。"""
    root = ET.fromstring(xml_text.strip())
    tag = root.tag.lower()

    if tag.endswith("feed"):
        return [_atom_entry(e) for e in root.findall(f"{_ATOM}entry")]
    if tag.endswith("rss"):
        return [_rss_item(i) for i in root.findall("./channel/item")]
    raise ValueError(f"未知のフィード形式: <{root.tag}>")


def _atom_entry(e: ET.Element) -> FeedEntry:
    link = ""
    for ln in e.findall(f"{_ATOM}link"):
        if ln.get("rel", "alternate") == "alternate":
            link = ln.get("href", "")
            break
    title = (e.findtext(f"{_ATOM}title") or "").strip()
    eid = (e.findtext(f"{_ATOM}id") or "").strip()
    updated = (e.findtext(f"{_ATOM}updated") or "").strip()
    return FeedEntry(eid or link or title, title, link, updated)


def _rss_item(i: ET.Element) -> FeedEntry:
    title = (i.findtext("title") or "").strip()
    link = (i.findtext("link") or "").strip()
    eid = (i.findtext("guid") or "").strip()
    updated = (i.findtext("pubDate") or "").strip()
    return FeedEntry(eid or link or title, title, link, updated)


@dataclass
class FeedResult:
    platform: str
    name: str
    url: str
    ok: bool
    new_entries: list[FeedEntry] = field(default_factory=list)
    error: str | None = None


@dataclass
class SyncReport:
    checked_at: str
    first_run: bool
    results: list[FeedResult]

    @property
    def changed_platforms(self) -> set[str]:
        return {r.platform for r in self.results if r.new_entries}

    @property
    def has_changes(self) -> bool:
        return any(r.new_entries for r in self.results)

    @property
    def has_errors(self) -> bool:
        return any(not r.ok for r in self.results)


def check_feeds(
    feeds: Iterable[dict],
    seen: dict[str, list[str]],
    fetch: Callable[[str], str],
) -> SyncReport:
    """各フィードを取得し新着を検出する。``seen`` を最新エントリIDで in-place 更新する。

    Args:
        feeds: [{platform, name, url}, ...]
        seen: {url: [entry_id, ...]} 前回の状態。初回は空 → ベースライン化のみ
        fetch: URL を渡すと本文文字列を返す（失敗時は例外）
    """
    feeds = list(feeds)
    first_run = not seen
    results: list[FeedResult] = []

    for f in feeds:
        url = f["url"]
        prev = set(seen.get(url) or [])
        try:
            entries = parse_feed(fetch(url))
        except Exception as exc:  # noqa: BLE001 - 監視ツールなので全例外を結果に畳む
            results.append(
                FeedResult(f["platform"], f["name"], url, ok=False, error=f"{type(exc).__name__}: {exc}")
            )
            continue

        current_ids = [e.entry_id for e in entries]
        new = [e for e in entries if e.entry_id not in prev] if prev else []
        results.append(
            FeedResult(f["platform"], f["name"], url, ok=True, new_entries=new)
        )
        current_set = set(current_ids)
        merged = current_ids + [i for i in seen.get(url, []) if i not in current_set]
        seen[url] = merged[:_MAX_REMEMBERED]

    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return SyncReport(checked_at, first_run, results)
