"""`progress.md` = 1枚見れば今の状況が全部わかるページ（毎朝自動更新・AI なし）。

経理チェック（`cli accounting`）の後に書き換え、ワークフローがコミットする。
中身: 登録者の推移 / 再生 / 今月のお金と前払いの残り / 直近の決定 / 次の予定。
手で書き足しても毎朝上書きされるので、恒久的なメモは README か roadmap へ。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from src.accounting.check import Result
from src.common.models import PostStatus

# docs/ は GitHub Pages（Moments Picks サイト）として公開されるので置かない。リポジトリ直下に置く。
DOC = Path(__file__).resolve().parents[2] / "progress.md"

START = date(2026, 9, 5)  # 自動投稿の運用開始
# 期限つきの予定（固定）。前払いの終了見込みは毎回計算して足す。
MILESTONES = [
    ("2026-12-31", "3か月の継続期間の終わり → 続けるか判断（2026-10-02 決定: 犬猫2本/日で継続）"),
    ("2027-02-01", "YouTube の収益化基準が倍増（2,000万ショート再生/90日 など）"),
]
LABEL = {("youtube", "cat"): "YouTube 猫", ("youtube", "dog"): "YouTube 犬",
         ("instagram", "cat"): "Instagram 猫", ("instagram", "dog"): "Instagram 犬"}


def _followers_table(store, today: date) -> list[str]:
    rows = [r for r in store.list_account_daily() if r.followers is not None]
    by: dict[tuple[str, str], dict[str, int]] = {}
    for r in rows:
        by.setdefault((r.platform.value, r.brand.value), {})[r.date[:10]] = int(r.followers)

    def at_or_before(series: dict[str, int], d: date) -> int | None:
        keys = [k for k in series if k <= d.isoformat()]
        return series[max(keys)] if keys else None

    out = ["| アカウント | 今 | 7日前 | 30日前 |", "|---|---:|---:|---:|"]
    for key, label in LABEL.items():
        s = by.get(key, {})
        vals = [at_or_before(s, today), at_or_before(s, today - timedelta(days=7)),
                at_or_before(s, today - timedelta(days=30))]
        out.append(f"| {label} | " + " | ".join("-" if v is None else f"{v:,}" for v in vals) + " |")
    return out


def _views_table(store) -> list[str]:
    posts = [p for p in store.list_posts() if p.status is PostStatus.PUBLISHED and p.platform_post_id]
    groups: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for p in posts:
        snaps = store.list_snapshots(post_key=p.post_key)
        latest = next((s for s in snaps if s.snapshot == "latest"), None)
        views = int(latest.views or 0) if latest else 0
        when = p.published_at.isoformat()[:10] if p.published_at else ""
        groups.setdefault((p.platform.value, p.brand.value), []).append((when, views))

    out = ["| アカウント | 投稿数 | 最新の投稿 | 総再生 | 直近7本の1本あたり（中央値） | 最高 |",
           "|---|---:|---|---:|---:|---:|"]
    for key, label in LABEL.items():
        rs = sorted(groups.get(key, []), reverse=True)
        if not rs:
            out.append(f"| {label} | 0 | - | - | - | - |")
            continue
        last7 = sorted(v for _, v in rs[:7])
        out.append(f"| {label} | {len(rs)} | {rs[0][0]} | {sum(v for _, v in rs):,} | "
                   f"{last7[len(last7) // 2]:,} | {max(v for _, v in rs):,} |")
    return out


def _decisions(store, today: date, days: int = 7, limit: int = 8) -> list[str]:
    since = (today - timedelta(days=days)).isoformat()
    ds = sorted((d for d in store.list_decisions() if d.date >= since),
                key=lambda d: (d.date, d.decision_id), reverse=True)
    if not ds:
        return [f"- 直近{days}日の記録なし"]
    out = []
    for d in ds[:limit]:
        text = " ".join((d.decision or d.hypothesis or "").split())
        out.append(f"- {d.date}（{d.account}）{text[:160]}{'…' if len(text) > 160 else ''}")
    return out


def build(store, res: Result) -> str:
    today = date.fromisoformat(res.today)
    m, pp = res.month, res.prepaid
    milestones = list(MILESTONES)
    for name, label in (("veo", "Veo（動画生成）"), ("anthropic", "Claude API（会議・投稿文）")):
        p = pp.get(name) or {}
        if p.get("days_left") is not None:
            end = today + timedelta(days=int(p["days_left"]))
            milestones.append((end.isoformat(), f"{label} の前払いが尽きる見込み → 止まる（追加請求なし）"))
    milestones.sort()

    def money(p: dict) -> str:
        if not p:
            return "記録なし"
        sym = "¥" if p["unit"] == "jpy" else "$"
        days = f"あと約 {p['days_left']} 日分" if p.get("days_left") is not None else "最近の使用なし"
        return f"残り {sym}{p['remaining']:,.2f} / 購入 {sym}{p['bought']:,.2f}（{days}）"

    lines = [
        "# 進捗（自動更新）",
        "",
        f"最終更新: {res.today} 朝 ／ 運用 {(today - START).days + 1} 日目（{START.isoformat()} 開始）",
        "",
        "> このページは毎朝の経理チェックが自動で書き換えます。手で書き足しても消えます。",
        "",
        "## 登録者・フォロワー",
        "",
        *_followers_table(store, today),
        "",
        "## 再生",
        "",
        *_views_table(store),
        "",
        "## お金（今月）",
        "",
        "| 項目 | 金額（目安） | 支払い方 |",
        "|---|---:|---|",
        f"| Veo（動画生成） | ¥{m['veo']:,} | 前払い：{money(pp.get('veo', {}))} |",
        f"| Claude API（会議・投稿文） | ¥{m['claude']:,} | 前払い：{money(pp.get('anthropic', {}))} |",
        f"| Google Cloud（Instagram {m['instagram_posts']} 投稿の転送料） | ¥{m['gcs']:,} | 後払い（予算アラート ¥300/月） |",
        f"| **合計** | **¥{m['total']:,}** | |",
        "",
        "異常: " + ("なし" if not res.anomalies else " / ".join(msg for _, msg in res.anomalies)),
        "",
        "## 直近の決定（意思決定ログ・7日分）",
        "",
        *_decisions(store, today),
        "",
        "## 次の予定",
        "",
        *[f"- {d}: {t}" for d, t in milestones if d >= res.today],
        "",
    ]
    return "\n".join(lines)


def write(store, res: Result, path: Path = DOC) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(store, res), encoding="utf-8")
    return path
