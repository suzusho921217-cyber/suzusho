"""競合の「超異常値PV」ショート動画をYouTube Data APIで実測して拾う（調査役の材料）。

異常値 = 再生数 ÷ チャンネル登録者数（登録者が少ないのに大きく伸びた＝企画・フックの力で
伸びた動画）。登録者が少なすぎる場合は下限(_MIN_SUBS)で割り、極小チャンネルの過大評価を防ぐ。
YOUTUBE_API_KEY もOAuthも無い/APIが失敗した場合は空の報告を返し、MTG全体は止めない。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

from src.common.config import env

_BASE = "https://www.googleapis.com/youtube/v3"
_QUERIES = [
    "猫 ショート", "犬 ショート", "猫 かわいい 面白い", "犬 かわいい 面白い",
    "cat shorts funny", "dog shorts cute", "AI cat", "AI dog",
]
_LOOKBACK_DAYS = 90
_MIN_VIEWS = 100_000
_MIN_SUBS = 1_000      # 登録者がこれ未満のときはこの値で割る
_MIN_RATIO = 20.0      # 再生数/登録者がこれ未満は異常値とみなさない
_MAX_SECONDS = 61
_TOP_N = 10


def _oauth_token() -> str | None:
    """投稿用のYouTube OAuth（youtube.readonly含む）から読み取り用アクセストークンを得る。"""
    cid, secret = env("YOUTUBE_OAUTH_CLIENT_ID"), env("YOUTUBE_OAUTH_CLIENT_SECRET")
    refresh = env("YOUTUBE_OAUTH_REFRESH_TOKEN_CAT") or env("YOUTUBE_OAUTH_REFRESH_TOKEN")
    if not (cid and secret and refresh):
        return None
    r = requests.post(
        "https://oauth2.googleapis.com/token", timeout=20,
        data={"client_id": cid, "client_secret": secret, "refresh_token": refresh,
              "grant_type": "refresh_token"},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _get(path: str, key: str, **params) -> dict:
    """key が "Bearer <token>" ならOAuth、それ以外はAPIキーとして扱う。"""
    if key.startswith("Bearer "):
        r = requests.get(f"{_BASE}/{path}", params=params, headers={"Authorization": key}, timeout=20)
    else:
        r = requests.get(f"{_BASE}/{path}", params={**params, "key": key}, timeout=20)
    r.raise_for_status()
    return r.json()


def _seconds(iso: str) -> int:
    m = re.fullmatch(r"PT(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0) if m else 10**6


def collect_outliers(key: str, *, now: datetime | None = None) -> list[dict]:
    """異常値の高い順に最大_TOP_N件。"""
    now = now or datetime.now(timezone.utc)
    after = (now - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids: list[str] = []
    for q in _QUERIES:
        data = _get(
            "search", key, part="id", q=q, type="video", videoDuration="short",
            order="viewCount", publishedAfter=after, maxResults=25,
        )
        ids += [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
    ids = list(dict.fromkeys(ids))

    videos: list[dict] = []
    for i in range(0, len(ids), 50):
        data = _get("videos", key, part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50]))
        videos += data.get("items", [])

    channel_ids = list({v["snippet"]["channelId"] for v in videos})
    subs: dict[str, int] = {}
    for i in range(0, len(channel_ids), 50):
        data = _get("channels", key, part="statistics", id=",".join(channel_ids[i:i + 50]))
        for c in data.get("items", []):
            st = c.get("statistics", {})
            subs[c["id"]] = 0 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))

    out = []
    for v in videos:
        views = int(v.get("statistics", {}).get("viewCount", 0))
        if views < _MIN_VIEWS or _seconds(v["contentDetails"]["duration"]) > _MAX_SECONDS:
            continue
        cid = v["snippet"]["channelId"]
        n = subs.get(cid, 0)
        ratio = views / max(n, _MIN_SUBS)
        if ratio < _MIN_RATIO:
            continue
        out.append({
            "title": v["snippet"]["title"], "url": f"https://www.youtube.com/shorts/{v['id']}",
            "channel": v["snippet"]["channelTitle"], "subs": n, "views": views, "ratio": ratio,
            "published": v["snippet"]["publishedAt"][:10],
            "tags": (v["snippet"].get("tags") or [])[:8],
            "description": (v["snippet"].get("description") or "").replace("\n", " ")[:120],
        })
    out.sort(key=lambda x: x["ratio"], reverse=True)
    return out[:_TOP_N]


def format_report(items: list[dict]) -> str:
    if not items:
        return "（異常値動画は取得できなかった、または YOUTUBE_API_KEY 未設定）"
    lines = []
    for i, x in enumerate(items, 1):
        lines.append(
            f"{i}. 「{x['title']}」 {x['channel']}（登録者{x['subs']:,}） "
            f"再生{x['views']:,} = 登録者比{x['ratio']:.0f}倍 / 公開{x['published']}\n"
            f"   {x['url']}\n   タグ: {', '.join(x['tags']) or '-'} / 概要: {x['description']}"
        )
    return "\n".join(lines)


def gather_outlier_report() -> str:
    """MTGに渡す実測レポート。失敗してもMTGは止めず、その旨を文面に残す。"""
    try:
        key = env("YOUTUBE_API_KEY")
        if not key:
            token = _oauth_token()
            key = f"Bearer {token}" if token else None
        if not key:
            return format_report([])
        return format_report(collect_outliers(key))
    except Exception as e:  # noqa: BLE001 - 外部API障害でMTG全体を止めない
        return f"（異常値動画の取得に失敗: {type(e).__name__}: {e}）"
