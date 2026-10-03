"""経理チェック（毎朝・無料）。今月の使用額・前払い残高の見込み・異常を点検する。

AI は使わない（数えて上限と比べるだけ）。メールは2種類だけ:
  - 5日に1回の定期報告（`report_every_days`）
  - 異常を見つけたときの【要確認】（同じ内容は1日1回まで）

Google Cloud の実請求額は権限の都合で読めないので、ここでは「量」から見積もる。
実額のずれは Cloud Billing の予算アラート「後払い監視」（¥300/月）で補う。
"""

from __future__ import annotations

import glob
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from src.accounting.usage import JST, STATE_DIR
from src.common import config as cfg_mod
from src.common.config import env


@dataclass
class Result:
    today: str
    month: dict = field(default_factory=dict)        # {veo, claude, gcs, total} 円
    today_counts: dict = field(default_factory=dict)  # {veo_jobs, claude_calls, claude_jpy}
    prepaid: dict = field(default_factory=dict)       # {veo: {...}, anthropic: {...}}
    sizes: dict = field(default_factory=dict)         # {state_mb, gcs_bucket_mb}
    anomalies: list[tuple[str, str]] = field(default_factory=list)  # (key, 説明)


def _read(path: str):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _claude_jpy(row: dict, acc: dict) -> float:
    prices = acc["claude_usd_per_mtok"]
    p = next((v for k, v in prices.items() if row["model"].startswith(k)), None)
    if p is None:  # 知らないモデルは高い方で見積もる（安く見積もって見逃さない）
        p = max(prices.values(), key=lambda v: v["out"])
    usd = (
        (row["input"] + row.get("cache_write", 0)) * p["in"]
        + row.get("cache_read", 0) * p["in"] * 0.1
        + row["output"] * p["out"]
    ) / 1_000_000 + row.get("web_search", 0) * acc["claude_web_search_usd"]
    return usd * acc["usd_jpy"]


def _veo_jobs(state: Path) -> list[dict]:
    """jobs-<date>.json の全ジョブ（企画日つき）。"""
    out = []
    for f in sorted(glob.glob(str(state / "jobs-*.json"))):
        d = _read(f) or {}
        for j in d.get("jobs", []):
            out.append({**j, "_date": d.get("date", Path(f).stem[5:])})
    return out


def _llm_rows(state: Path) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(str(state / "llm_usage-*.json"))):
        rows.extend(_read(f) or [])
    return rows


def _instagram_posts(state: Path, month: str) -> int:
    n = 0
    for f in glob.glob(str(state / f"publish-{month}-*.json")):
        for o in (_read(f) or {}).get("outcomes", []):
            if o.get("platform") == "instagram" and o.get("action") == "PUBLISHED":
                n += 1
    return n


def _dir_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1e6


def _gcs_bucket_mb() -> float | None:
    """Instagram 用 GCS バケットの中身の合計。読めなければ None（チェックを飛ばす）。"""
    bucket = env("GCS_BUCKET_NAME")
    creds_json = env("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not (bucket and creds_json):
        return None
    try:
        from google.cloud import storage
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_info(json.loads(creds_json))
        client = storage.Client(credentials=creds, project=env("GOOGLE_CLOUD_PROJECT"))
        return sum(b.size or 0 for b in client.list_blobs(bucket)) / 1e6
    except Exception as e:  # noqa: BLE001 - 読めないだけで点検全体は止めない
        print(f"[accounting] GCS バケットの大きさを読めなかった: {e}")
        return None


def _prepaid(purchases: list[dict], unit: str, usage: list[tuple[str, float]], today: date,
             *, planned_per_day: float = 0.0) -> dict:
    """前払いの残り見込み。usage は (日付, 円 or ドル)。

    1日あたりは「直近7日の実績」と「予定ペース（planned_per_day）」の多い方。買った直後は
    実績が少なく、実績だけだと残り日数を長く見積もりすぎるため（安全側に倒す）。
    """
    if not purchases:
        return {}
    start = min(p["date"] for p in purchases)
    bought = sum(p[unit] for p in purchases)
    used = sum(v for d, v in usage if d >= start)
    week_ago = (today - timedelta(days=7)).isoformat()
    recent = [v for d, v in usage if d > week_ago and d >= start]
    days_in_window = min(7, (today - date.fromisoformat(start)).days + 1)
    per_day = max(sum(recent) / days_in_window if recent else 0.0, planned_per_day)
    remaining = bought - used
    days_left = remaining / per_day if per_day > 0 else None
    return {"unit": unit, "bought": bought, "used": round(used, 2),
            "remaining": round(remaining, 2), "per_day": round(per_day, 2),
            "days_left": round(days_left, 1) if days_left is not None else None}


def run_check(*, state: Path = STATE_DIR, now: datetime | None = None,
              gcs_bucket_mb=_gcs_bucket_mb) -> Result:
    acc = cfg_mod.load("accounting")
    now = now or datetime.now(JST)
    today = now.date()
    month = f"{today:%Y-%m}"
    res = Result(today=today.isoformat())

    # --- Veo（企画日ベース。cost_jpy は投入時の見積もり）
    jobs = _veo_jobs(state)
    veo_usage = [(j["_date"], float(j.get("cost_jpy") or 0)) for j in jobs]
    veo_month = sum(v for d, v in veo_usage if d.startswith(month))
    veo_today = sum(1 for j in jobs if j["_date"] == today.isoformat())

    # --- Claude
    rows = _llm_rows(state)
    llm_usage = [(r["at"][:10], _claude_jpy(r, acc)) for r in rows]
    claude_month = sum(v for d, v in llm_usage if d.startswith(month))
    claude_today = [v for d, v in llm_usage if d == today.isoformat()]

    # --- GCS（Instagram が動画を取りに来る転送料）
    ig = _instagram_posts(state, month)
    gcs_month = ig * acc["instagram_video_mb"] / 1024 * acc["gcs_egress_jpy_per_gib"]

    res.month = {"veo": round(veo_month), "claude": round(claude_month),
                 "gcs": round(gcs_month), "instagram_posts": ig}
    res.month["total"] = res.month["veo"] + res.month["claude"] + res.month["gcs"]
    res.today_counts = {"veo_jobs": veo_today, "claude_calls": len(claude_today),
                        "claude_jpy": round(sum(claude_today))}

    usd_jpy = acc["usd_jpy"]
    veo_cfg = cfg_mod.load("generation").get("veo", {}) or {}
    slots = int(cfg_mod.load("scoring")["allocation"].get("total_daily_slots", 0))
    veo_planned = float(veo_cfg.get("price_jpy_per_sec", 0)) * max(veo_cfg.get("allowed_durations") or [8]) * slots
    res.prepaid = {
        "veo": _prepaid(acc["prepaid"]["veo"]["purchases"], "jpy", veo_usage, today,
                        planned_per_day=veo_planned),
        "anthropic": _prepaid(acc["prepaid"]["anthropic"]["purchases"], "usd",
                              [(d, v / usd_jpy) for d, v in llm_usage], today),
    }

    res.sizes = {"state_mb": round(_dir_mb(state), 1)}
    bucket_mb = gcs_bucket_mb()
    if bucket_mb is not None:
        res.sizes["gcs_bucket_mb"] = round(bucket_mb, 1)

    # --- 異常
    a = acc["anomaly"]
    if veo_today > a["veo_jobs_per_day"]:
        res.anomalies.append(("veo_jobs", f"今日の動画生成が {veo_today} 本（上限の目安 {a['veo_jobs_per_day']} 本）"))
    if len(claude_today) > a["claude_calls_per_day"]:
        res.anomalies.append(("claude_calls", f"今日の Claude API 呼び出しが {len(claude_today)} 回（目安 {a['claude_calls_per_day']} 回）"))
    if sum(claude_today) > a["claude_jpy_per_day"]:
        res.anomalies.append(("claude_jpy", f"今日の Claude API 使用額が約 ¥{sum(claude_today):,.0f}（目安 ¥{a['claude_jpy_per_day']}）"))
    if res.sizes["state_mb"] > a["state_mb"]:
        res.anomalies.append(("state_mb", f"工程間で受け渡すデータが {res.sizes['state_mb']:,.0f}MB（目安 {a['state_mb']}MB）。転送量が膨らんでいる恐れ"))
    if bucket_mb is not None and bucket_mb > a["gcs_bucket_mb"]:
        res.anomalies.append(("gcs_bucket_mb", f"GCS バケットに {bucket_mb:,.0f}MB 溜まっている（目安 {a['gcs_bucket_mb']}MB）。転送料事故の兆候"))
    for name, label in (("veo", "Veo（動画生成）"), ("anthropic", "Claude API")):
        p = res.prepaid[name]
        if p and p["days_left"] is not None and p["days_left"] < a["prepaid_days_left"]:
            res.anomalies.append((f"prepaid_{name}", f"{label} の前払いが残り約 {p['days_left']} 日分。尽きると止まる（追加請求は無し）"))
    return res


def _fmt_prepaid(label: str, p: dict) -> str:
    if not p:
        return f"- {label}: 記録なし"
    sym = "¥" if p["unit"] == "jpy" else "$"
    days = f"あと約 {p['days_left']} 日分" if p["days_left"] is not None else "最近の使用なし"
    return (f"- {label}: 残り {sym}{p['remaining']:,.2f}（購入 {sym}{p['bought']:,.2f} ／ "
            f"1日 約 {sym}{p['per_day']:,.2f}）→ {days}")


def render(res: Result) -> str:
    m = res.month
    lines = [
        f"経理チェック {res.today}",
        "",
        "■ 今月の使用見込み（円・税抜の目安）",
        f"- Veo（動画生成・前払いから引かれる）: ¥{m['veo']:,}",
        f"- Claude API（会議・投稿文・前払いから引かれる）: ¥{m['claude']:,}",
        f"- Google Cloud 後払い（Instagram {m['instagram_posts']} 投稿ぶんの転送料）: ¥{m['gcs']:,}",
        f"- 合計: ¥{m['total']:,}",
        "",
        "■ 前払いの残り見込み",
        _fmt_prepaid("Veo", res.prepaid.get("veo", {})),
        _fmt_prepaid("Claude API", res.prepaid.get("anthropic", {})),
        "",
        "■ 監視",
        f"- 工程間データ: {res.sizes.get('state_mb', 0):,.1f}MB",
        f"- GCS バケット: {res.sizes['gcs_bucket_mb']:,.1f}MB" if "gcs_bucket_mb" in res.sizes
        else "- GCS バケット: 読めず（点検スキップ）",
        f"- 今日: 動画生成 {res.today_counts['veo_jobs']} 本 / Claude 呼び出し {res.today_counts['claude_calls']} 回",
        "",
    ]
    if res.anomalies:
        lines += ["■ 要確認"] + [f"- {msg}" for _, msg in res.anomalies] + [""]
    else:
        lines += ["■ 異常なし", ""]
    lines += [
        "※ Google Cloud の実際の請求額はここでは読めません。後払いが想定を超えたら",
        "  Cloud Billing の予算アラート「後払い監視」（¥300/月）のメールが別に届きます。",
        "※ 前払いを買い足したら config/accounting.yaml の purchases に1行足してください。",
    ]
    return "\n".join(lines)


def run_and_notify(*, state: Path = STATE_DIR, now: datetime | None = None,
                   send=None, gcs_bucket_mb=_gcs_bucket_mb) -> Result:
    """点検して、必要なときだけメールする。送った記録は `.state/accounting.json`。"""
    if send is None:
        from src.common.notify import send_alert_email as send

    res = run_check(state=state, now=now, gcs_bucket_mb=gcs_bucket_mb)
    acc = cfg_mod.load("accounting")
    log_path = state / "accounting.json"
    log = _read(str(log_path)) or {}
    body = render(res)

    new = [(k, msg) for k, msg in res.anomalies if log.get("alerted", {}).get(k) != res.today]
    if new:
        subject = f"【要確認】SNS自動投稿の経理チェック: {new[0][1]}"
        if send(subject, body):
            log.setdefault("alerted", {}).update({k: res.today for k, _ in new})

    last = log.get("last_report")
    due = last is None or (date.fromisoformat(res.today) - date.fromisoformat(last)).days >= acc["report_every_days"]
    if due and not new:  # 異常メールを送った日は、それが定期報告を兼ねる
        if send(f"SNS自動投稿の経理報告（{res.today}）今月 ¥{res.month['total']:,}", body):
            log["last_report"] = res.today
    elif due and new:
        log["last_report"] = res.today

    log["latest"] = {"today": res.today, "month": res.month, "prepaid": res.prepaid,
                     "sizes": res.sizes, "anomalies": [k for k, _ in res.anomalies]}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1))
    print(body)
    return res
