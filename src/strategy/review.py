"""strategy-review: 週次の経営企画レビュー。

日次の agent-mtg（src/mtg/）が「日々の運用最適化」なのに対し、これは四半期〜年の
視点で「事業として収益化に向かっているか / どこに寄せるか / 続けるか」を見る。

経営企画役（roles.STRATEGIST_SYSTEM）を1回だけ呼び（Web検索ON）、結果を
- docs/monetization_roadmap.md の「週次所見」欄に反映
- .state/strategy-<date>.json に保存
- メールで報告
する。config・コード・予算・投稿内容は一切変えない（読み取り＋所見のみ）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.config import load
from src.common.models import PostStatus
from src.common.notify import send_alert_email
from src.mtg import roles
from src.mtg.client import call_role
from src.mtg.context import STATE_DIR, _read_json
from src.mtg.orchestrate import _extract_json
from src.sheets.client import get_store

JST = timezone(timedelta(hours=9))

_ROOT = Path(__file__).resolve().parents[2]
ROADMAP_PATH = _ROOT / "docs" / "monetization_roadmap.md"
_WEEKLY_RE = re.compile(
    r"(<!-- WEEKLY:START -->).*?(<!-- WEEKLY:END -->)", re.DOTALL
)


@dataclass
class StrategyResult:
    date: str
    transcript: str = ""
    parsed: dict | None = None
    roadmap_updated: bool = False
    state_path: str | None = None
    error: str | None = None


# ---------------------------------------------------------------- 現在地の集計

def _standing_by_platform(store) -> dict:
    """媒体ごとの「現在地」。フォロワー・累計再生・投稿本数・運用開始からの日数。

    strategist が収益化ラインまでの距離を測るための素材（数字はコードが出す）。
    """
    posts = [
        p for p in store.list_posts()
        if p.status is PostStatus.PUBLISHED and p.platform_post_id
    ]
    today = datetime.now(JST).date()

    out: dict[str, dict] = {}
    for post in posts:
        plat = post.platform.value
        snaps = store.list_snapshots(post_key=post.post_key)
        latest = next((s for s in snaps if s.snapshot == "latest"), None)
        views = (latest.views if latest and latest.views is not None else 0)

        row = out.setdefault(plat, {
            "posts": 0, "cumulative_views": 0, "brands": set(),
            "first_published": None, "days_running": None,
        })
        row["posts"] += 1
        row["cumulative_views"] += views
        row["brands"].add(post.brand.value)
        if post.published_at is not None:
            d = post.published_at.date()
            if row["first_published"] is None or d < row["first_published"]:
                row["first_published"] = d

    # フォロワー: アカウント日次DBの (brand, platform) ごとの最新値
    followers: dict[str, dict[str, int]] = {}
    try:
        rows = store.list_account_daily()
    except Exception:  # noqa: BLE001 - タブ未作成など。集計は止めない
        rows = []
    for a in rows:
        if a.followers is None:
            continue
        cur = followers.setdefault(a.platform.value, {})
        prev = cur.get(a.brand.value)
        # date は "YYYY-MM-DD" 文字列。新しい日付を採用
        if prev is None or a.date >= prev[1]:
            cur[a.brand.value] = (a.followers, a.date)

    for plat, row in out.items():
        row["brands"] = sorted(row["brands"])
        if row["first_published"] is not None:
            row["days_running"] = (today - row["first_published"]).days
            row["first_published"] = row["first_published"].isoformat()
        fol = followers.get(plat, {})
        row["followers"] = {b: v[0] for b, v in fol.items()} or "未取得"

    # 一度も投稿していない媒体も明示
    for plat in ("youtube", "instagram", "tiktok"):
        out.setdefault(plat, {"posts": 0, "note": "未開設 or 未投稿"})
    return out


def _spend_summary() -> dict:
    spend = _read_json(STATE_DIR / "spend.json") or {}
    budget = load("budget")
    return {
        "月上限": budget.get("monthly_budget"),
        "今月の生成費": round(float(spend.get("month", 0.0))),
        "1日の本数": load("scoring").get("allocation", {}).get("total_daily_slots"),
    }


def _recent_decisions(store, days: int = 21) -> str:
    try:
        decisions = store.list_decisions()
    except Exception:  # noqa: BLE001
        return "まだ意思決定ログが無い"
    if not decisions:
        return "まだ意思決定ログが無い"
    cutoff = (datetime.now(JST).date() - timedelta(days=days)).isoformat()
    keep = sorted(
        (d for d in decisions if d.date >= cutoff or not d.result),
        key=lambda d: d.date, reverse=True,
    )[:20]
    return json.dumps(
        [
            {
                "date": d.date, "account": d.account, "decision": d.decision,
                "expected_kpi": d.expected_kpi, "result": d.result or "未評価",
            }
            for d in keep
        ],
        ensure_ascii=False, indent=2,
    )


def _read_text(path: Path, limit: int = 6000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return "(読めなかった)"


def gather_strategy_context() -> str:
    store = get_store()
    parts = [
        "## config/monetization.yaml（収益化条件・動画以外の収益経路・現在の役割）\n"
        + json.dumps(load("monetization"), ensure_ascii=False, indent=2),
        "## 各媒体の現在地（コード集計。フォロワー・累計再生・投稿本数・運用日数）\n"
        + json.dumps(_standing_by_platform(store), ensure_ascii=False, indent=2, default=str),
        "## 予算・生成費の消化\n" + json.dumps(_spend_summary(), ensure_ascii=False, indent=2),
        "## docs/economics.md（収支モデルの試算）\n" + _read_text(_ROOT / "docs" / "economics.md"),
        "## docs/monetization_roadmap.md（現行版。週次所見欄を更新する）\n"
        + _read_text(ROADMAP_PATH),
        "## 直近3週間の意思決定ログ\n" + _recent_decisions(store),
        f"## 今日の日付\n{datetime.now(JST).date().isoformat()}",
    ]
    return "\n\n".join(parts)


# ---------------------------------------------------------------- ロードマップ更新

def _update_roadmap(weekly_note: str, date: str) -> bool:
    """docs/monetization_roadmap.md の WEEKLY マーカー間を週次所見で置き換える。

    マーカーが無ければ何もしない（False）。内容が同じなら書かない（False）。
    """
    if not weekly_note.strip():
        return False
    try:
        text = ROADMAP_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    m = _WEEKLY_RE.search(text)
    if not m:
        return False

    replacement = (
        f"{m.group(1)}\n## 週次所見（{date} 更新 by strategy-review）\n\n"
        f"{weekly_note.strip()}\n\n{m.group(2)}"
    )
    new_text = text[: m.start()] + replacement + text[m.end() :]
    if new_text == text:
        return False
    ROADMAP_PATH.write_text(new_text, encoding="utf-8")
    return True


# ---------------------------------------------------------------- メール

def _email_body(result: StrategyResult) -> str:
    import html as _html

    esc = _html.escape
    cj = result.parsed or {}
    p = [
        "<div style='font-size:14px;line-height:1.7'>",
        f"<h2 style='margin:0 0 10px'>📈 経営企画レビュー {esc(result.date)}</h2>",
    ]
    if result.error:
        p.append(f"<p>⚠️ {esc(result.error)}</p>")

    if cj.get("headline"):
        p.append(
            "<p style='background:#f4f6f8;padding:10px 12px;border-radius:6px'>"
            f"<b>📋 結論</b><br>{esc(str(cj['headline']))}"
            + (f"<br><span style='color:#888'>フェーズ: {esc(str(cj['phase']))}</span>" if cj.get("phase") else "")
            + "</p>"
        )

    plats = cj.get("platforms") or {}
    if plats:
        rows = ""
        for name in ("youtube", "instagram", "tiktok"):
            d = plats.get(name) or {}
            if not d:
                continue
            rows += (
                f"<tr><td style='padding:4px 8px;border:1px solid #ddd'><b>{esc(name)}</b><br>"
                f"<span style='color:#888'>{esc(str(d.get('role', '')))}</span></td>"
                f"<td style='padding:4px 8px;border:1px solid #ddd'>{esc(str(d.get('standing', '')))}</td>"
                f"<td style='padding:4px 8px;border:1px solid #ddd'>{esc(str(d.get('gap', '')))}"
                f"<br><span style='color:#888'>見込み: {esc(str(d.get('eta', '')))}</span></td>"
                f"<td style='padding:4px 8px;border:1px solid #ddd'>{esc(str(d.get('next_lever', '')))}</td></tr>"
            )
        if rows:
            p.append(
                "<h3 style='margin:16px 0 4px'>媒体別の現在地</h3>"
                "<table style='border-collapse:collapse;font-size:13px'>"
                "<tr><th style='padding:4px 8px;border:1px solid #ddd'>媒体</th>"
                "<th style='padding:4px 8px;border:1px solid #ddd'>現在地</th>"
                "<th style='padding:4px 8px;border:1px solid #ddd'>ライン差 / 見込み</th>"
                "<th style='padding:4px 8px;border:1px solid #ddd'>次の一手</th></tr>"
                f"{rows}</table>"
            )

    for key, label in (
        ("deadline_events", "⏰ 期限イベント"),
        ("off_platform_revenue", "💼 動画以外の収益経路"),
        ("resource_allocation", "🎯 リソース配分"),
        ("continue_or_withdraw", "🚦 継続 / 縮小 / 撤退"),
        ("economics_update", "📊 収支モデルの更新点"),
    ):
        v = cj.get(key)
        if not v:
            continue
        if isinstance(v, list):
            body = "<ul style='margin:0;padding-left:20px'>" + "".join(
                f"<li>{esc(str(x))}</li>" for x in v
            ) + "</ul>"
        else:
            body = f"<p style='margin:0'>{esc(str(v))}</p>"
        p.append(f"<h3 style='margin:16px 0 4px'>{label}</h3>{body}")

    needs = cj.get("needs_user_approval") or []
    if needs:
        li = ""
        for it in needs:
            cost = it.get("estimated_cost_jpy_per_month", 0) or 0
            li += (
                f"<li style='margin-bottom:8px'>{esc(str(it.get('description', '')))}"
                f"<br><span style='color:#888'>（月額目安 ¥{cost:,.0f}）</span></li>"
            )
        p.append(
            "<h3 style='margin:16px 0 4px'>⚠️ 要ユーザー判断（未実行）</h3>"
            f"<ul style='margin:0;padding-left:20px'>{li}</ul>"
        )

    note = "更新済み" if result.roadmap_updated else "変更なし"
    p.append(
        "<p style='color:#999;font-size:12px;margin-top:18px'>"
        f"📄 docs/monetization_roadmap.md（週次所見: {note}） / "
        f".state/strategy-{esc(result.date)}.json</p></div>"
    )
    return "".join(p)


# ---------------------------------------------------------------- 実行

def run() -> StrategyResult:
    date = datetime.now(JST).date().isoformat()
    result = StrategyResult(date=date)

    context = gather_strategy_context()
    out = call_role(
        roles.STRATEGIST_SYSTEM, context, with_web_search=True, max_tokens=8000,
    )
    result.transcript = out

    parsed = _extract_json(out)
    result.parsed = parsed
    if parsed is None:
        result.error = "strategist の出力から JSON を抽出できなかった（所見は未更新）"
        return result

    try:
        result.roadmap_updated = _update_roadmap(parsed.get("weekly_note", ""), date)
    except Exception as e:  # noqa: BLE001 - 所見更新の失敗でレビュー結果を落とさない
        result.error = f"週次所見の更新に失敗: {e}"
    return result


def _write_log(result: StrategyResult) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"strategy-{result.date}.json"
    path.write_text(
        json.dumps(
            {
                "date": result.date,
                "parsed": result.parsed,
                "transcript": result.transcript,
                "roadmap_updated": result.roadmap_updated,
                "error": result.error,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return path


def run_and_report() -> StrategyResult:
    result = run()
    log_path = _write_log(result)
    result.state_path = str(log_path)
    print(f"[strategy-review] wrote {log_path}")

    subject = f"[AI動画自動投稿] 経営企画レビュー {result.date}"
    if result.parsed:
        subject += f" - {str(result.parsed.get('headline', ''))[:40]}"
    sent = send_alert_email(subject, _email_body(result), html=True)
    print(f"[strategy-review] メール送信: {'成功' if sent else '失敗/未設定'}")
    print(f"[strategy-review] 週次所見: {'更新済み' if result.roadmap_updated else '変更なし'}")
    return result
