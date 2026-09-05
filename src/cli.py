"""GitHub Actions から呼ぶ単一エントリポイント（§5）。

各サブコマンド = 1 ワークフロー。処理は途中再実行可能なステートマシン形式
にすること（§15）。

実装状況:
    plan-daily      … 実装済み（config → 配分 → 企画6本 → .state/plan-<date>.json）
    policy-sync     … 実装済み（媒体フィード監視 → 変更検知で該当媒体を stale・非0終了）
    daily-learning  … 実装済み（成績 → スコア → 勝ちタグ → .state/winning_tags.json）
    kill-switch     … 実装済み（予算/異常シグナル → brand×platform / 全体の停止判定）
    generate        … 実装済み（plan の企画を予算ゲート込みで生成投入 → .state/jobs-<date>.json）
    poll-generation … 実装済み（生成中ジョブを完了確認 → 品質判定 → NG は再生成）
    media           … 実装済み（マスターを 9:16・音量正規化 → 媒体別派生。ffmpeg 不在ならスキップ）
    publish         … 実装済み（生成完了ジョブを媒体別に投稿判定 → 投稿。既定 PUBLISH_MODE=dryrun）
    metrics         … 実装済み（公開済み投稿の指標を回収 → 管理DB → .state/performance.json）
    その他          … 未実装（"not implemented yet" を表示するだけ）

使い方:
    python -m src.cli plan-daily [--date YYYY-MM-DD] [--winning-tags PATH] [--out PATH]
    python -m src.cli policy-sync
    python -m src.cli daily-learning [--input PATH] [--out PATH]
    python -m src.cli kill-switch [--input PATH] [--out PATH]
    python -m src.cli generate [--date YYYY-MM-DD]
    python -m src.cli poll-generation [--date YYYY-MM-DD]
    ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

from src.common.config import env, load
from src.common.guardrails import (
    BudgetSpend,
    GuardAction,
    GuardVerdict,
    check_budget,
    check_kill_switch,
    combine,
)
from src.common.models import (
    AccountDaily,
    Brand,
    ContentPlan,
    ExperimentFlag,
    GenerationJob,
    GenerationStatus,
    PerformanceSnapshot,
    Platform,
    PolicyDecision,
    PolicyRisk,
    Post,
    PostStatus,
)
from src.common.notify import send_alert_email
from src.generation.base import get_provider
from src.generation.pipeline import advance_jobs, submit_plans, summarize
from src.generation.providers import mock as _mock_provider  # noqa: F401  (@register 副作用)
from src.generation.providers import veo as _veo_provider  # noqa: F401  (@register 副作用)
from src.learning.engine import extract_winning_tags
from src.media.processor import (
    MediaError,
    MediaVariantSpec,
    ffmpeg_available,
    make_variant,
    normalize_master,
)
from src.metrics.collector import collect_snapshot, due_snapshots
from src.planner.planner import build_daily_plan, next_day_allocation, render_prompt
from src.policy.engine import check_prompt
from src.policy.policy_sync import check_feeds
from src.publishers.base import PublishRequest
from src.publishers.dryrun import DryRunPublisher
from src.publishers.hashtags import select_caption_cta, select_hashtags
from src.publishers.pipeline import decide_and_publish
from src.publishers.registry import get_publisher
from src.sheets.client import get_store, snapshot_to_row

STATE_DIR = Path(__file__).resolve().parents[1] / ".state"

COMMANDS = [
    "policy-sync",
    "plan-daily",
    "generate",
    "poll-generation",
    "media",
    "publish",
    "metrics",
    "daily-learning",
    "kill-switch",
]


def _enabled(cfg_name: str, key: str, enum: type) -> list:
    """config/<cfg_name>.yaml の <key> から enabled=true の項目を enum で返す。"""
    cfg = load(cfg_name)
    return [
        enum(name)
        for name, spec in (cfg.get(key) or {}).items()
        if isinstance(spec, dict) and spec.get("enabled")
    ]


def _load_winning_tags(path: str | None) -> list[dict]:
    """learning の出力（勝ちタグ）を読む。無ければ空 = ブートストラップ配分。"""
    p = Path(path) if path else STATE_DIR / "winning_tags.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("winning_tags", [])
    return list(data)


def cmd_plan_daily(args: argparse.Namespace) -> int:
    target_date = args.date or datetime.now(JST).date().isoformat()

    allocation_cfg = load("scoring")["allocation"]
    planning_cfg = load("planning")
    enabled_brands = _enabled("brands", "brands", Brand)
    enabled_platforms = _enabled("platforms", "platforms", Platform)
    winning_tags = _load_winning_tags(args.winning_tags)

    alloc = next_day_allocation(winning_tags, allocation_cfg, enabled_brands)
    plans = build_daily_plan(
        target_date, alloc, winning_tags, planning_cfg, enabled_platforms
    )

    # 各企画に生成プロンプトを付ける（投稿前レビュー用。§19）
    characters = load("characters").get("characters", {})
    brands_cfg = load("brands").get("brands", {})
    for p in plans:
        char = characters.get(p.character_id, {})
        banned = (brands_cfg.get(p.brand.value, {}) or {}).get("banned_expressions", [])
        p.prompt_text = render_prompt(p, char, banned)

    # プロンプト段階のポリシー判定（§7 1段目）。媒体ごとに評価して記録する。
    policy_precheck: dict[str, dict] = {}
    for p in plans:
        per_platform = {}
        for platform in p.target_platforms:
            res = check_prompt(p, platform)
            per_platform[platform.value] = {
                "decision": res.decision.value,
                "policy_version": res.policy_version,
                "reasons": res.reasons,
            }
        policy_precheck[p.plan_id] = per_platform

    blocked = [
        (pid, plat, v["decision"])
        for pid, pp in policy_precheck.items()
        for plat, v in pp.items()
        if v["decision"] != "PASS"
    ]

    out = Path(args.out) if args.out else STATE_DIR / f"plan-{target_date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": target_date,
        "allocation": {
            "mode": alloc.mode,
            "total_slots": alloc.total_slots,
            "brands": [asdict(b) for b in alloc.brands],
            "warnings": alloc.warnings,
        },
        "winning_tags_used": len(winning_tags),
        "plans": [asdict(p) for p in plans],
        "policy_precheck": policy_precheck,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[plan-daily] date={target_date} mode={alloc.mode} slots={len(plans)}")
    print(f"[plan-daily] enabled brands={[b.value for b in enabled_brands]} "
          f"platforms={[p.value for p in enabled_platforms]} "
          f"winning_tags={len(winning_tags)}")
    for b in alloc.brands:
        print(f"  {b.brand.value:6s} total={b.total} exploit={b.exploit} explore={b.explore}")
    for w in alloc.warnings:
        print(f"  ! {w}")
    for p in plans:
        print(
            f"  {p.plan_id}  {p.brand.value:5s} {p.experiment_flag.value:7s} "
            f"{p.concept_tag}/{p.hook_type}  {p.duration_target_sec}s  "
            f"r{p.reality_level}/o{p.oddity_level}  "
            f"-> {[pf.value for pf in p.target_platforms]}"
        )
    if blocked:
        print(f"[plan-daily] policy 要対応 {len(blocked)} 件:")
        for pid, plat, decision in blocked:
            reasons = "; ".join(policy_precheck[pid][plat]["reasons"])
            print(f"  ! {pid} [{plat}] {decision}: {reasons}")
    else:
        print("[plan-daily] policy prompt-check: 全件 PASS")
    print(f"[plan-daily] wrote {out}")
    return 0


def _http_get(url: str) -> str:
    import requests

    resp = requests.get(
        url, timeout=30, headers={"User-Agent": "ai-media-policy-sync/1 (+github-actions)"}
    )
    resp.raise_for_status()
    return resp.text


def cmd_policy_sync(args: argparse.Namespace) -> int:
    """媒体の RSS/Atom フィードを監視し、新着（＝ポリシー/規約変更の可能性）を検知する。

    終了コード: 0=変更なし / 1=フィード取得失敗あり / 2=新着あり（要手動確認）。
    非0は GitHub Actions のジョブ失敗になり、失敗通知メールが飛ぶ。
    """
    cfg = load("policy_sync")
    feeds = cfg.get("feeds") or []

    seen_path = STATE_DIR / "policy_sync_feeds.json"
    seen: dict = json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}

    report = check_feeds(feeds, seen, _http_get)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")

    if report.changed_platforms:
        stale_path = STATE_DIR / "policy_sync.json"
        stale = json.loads(stale_path.read_text(encoding="utf-8")) if stale_path.exists() else {}
        for plat in sorted(report.changed_platforms):
            entry = stale.setdefault(plat, {})
            entry["stale"] = True
            entry["flagged_at"] = report.checked_at
            entry["detail"] = (
                "policy_sync がフィード新着を検知。docs/platform_policies.md と "
                "config/policy_rules/ を確認し、問題なければ version を更新して stale を戻す"
            )
        stale_path.write_text(json.dumps(stale, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[policy-sync] checked_at={report.checked_at} feeds={len(feeds)}")
    for r in report.results:
        if not r.ok:
            print(f"  ✗ {r.platform}/{r.name}: {r.error}")
        elif r.new_entries:
            print(f"  ! {r.platform}/{r.name}: 新着 {len(r.new_entries)} 件")
            for e in r.new_entries:
                print(f"      - {e.title}  {e.link}")
        else:
            print(f"  ok {r.platform}/{r.name}")

    if report.first_run:
        print("[policy-sync] 初回: ベースライン化のみ（次回から差分検知）")
        return 1 if report.has_errors else 0
    if report.has_changes:
        print(f"[policy-sync] 変更の可能性: {sorted(report.changed_platforms)} を stale にした。手動確認が必要")
        return 2
    if report.has_errors:
        print("[policy-sync] 一部フィード取得失敗")
        return 1
    return 0


# --- generate / poll-generation ----------------------------------------

def _latest_state(glob: str) -> Path | None:
    files = sorted(STATE_DIR.glob(glob))
    return files[-1] if files else None


def _provider_name(explicit: str | None = None) -> str:
    """VIDEO_PROVIDER env > config/generation.yaml provider: > "mock"。"""
    return explicit or env("VIDEO_PROVIDER") or str(load("generation").get("provider", "mock"))


def _plan_from_dict(d: dict) -> ContentPlan:
    return ContentPlan(
        plan_id=d["plan_id"], date=d["date"], brand=Brand(d["brand"]),
        concept_tag=d["concept_tag"], hook_type=d["hook_type"],
        character_id=d["character_id"], reality_level=int(d["reality_level"]),
        oddity_level=int(d["oddity_level"]),
        duration_target_sec=int(d["duration_target_sec"]),
        experiment_flag=ExperimentFlag(d["experiment_flag"]),
        policy_risk=PolicyRisk(d["policy_risk"]), prompt_version=d["prompt_version"],
        prompt_text=d.get("prompt_text"),
        target_platforms=[Platform(p) for p in d.get("target_platforms", [])],
        notes=d.get("notes", ""),
    )


def _job_from_dict(d: dict) -> GenerationJob:
    def _dt(v):
        try:
            return datetime.fromisoformat(v) if v else None
        except (TypeError, ValueError):
            return None
    return GenerationJob(
        job_id=d["job_id"], plan_id=d["plan_id"], provider=d["provider"],
        external_job_id=d.get("external_job_id"),
        status=GenerationStatus(d.get("status", "QUEUED")),
        attempt=int(d.get("attempt", 1)), max_attempts=int(d.get("max_attempts", 3)),
        video_url=d.get("video_url"), local_path=d.get("local_path"),
        cost_jpy=float(d.get("cost_jpy", 0.0)), error=d.get("error"),
        submitted_at=_dt(d.get("submitted_at")), completed_at=_dt(d.get("completed_at")),
    )


def _load_plans(date: str | None) -> tuple[Path, list[ContentPlan]]:
    path = STATE_DIR / f"plan-{date}.json" if date else _latest_state("plan-*.json")
    if path is None or not path.exists():
        raise SystemExit("[generate] plan ファイルが無い。先に plan-daily を実行してください")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return path, [_plan_from_dict(p) for p in payload.get("plans", [])]


def _load_spend() -> dict:
    """`.state/spend.json` = 実際に使った生成費の積み上げ。月・日をまたぐと自動リセット。"""
    now = datetime.now(JST)
    month_key, day_key = now.strftime("%Y-%m"), now.date().isoformat()
    p = STATE_DIR / "spend.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if data.get("month_key") != month_key:
        data = {"month_key": month_key, "month": 0.0,
                "total": float(data.get("total", 0.0)),
                "day_key": day_key, "today": 0.0, "by_brand": {}}
    if data.get("day_key") != day_key:
        data["day_key"], data["today"] = day_key, 0.0
    data.setdefault("by_brand", {})
    return data


def _save_spend(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "spend.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _spend_gate(spend: dict, cfg: dict):
    """今月/今日の実績を起点にした予算ゲート（§13）。generate が投入前に毎回通す。"""
    def gate(additional: float):
        return check_budget(
            BudgetSpend(
                today=spend["today"] + additional,
                month=spend["month"] + additional,
                total=spend["total"] + additional,
                by_brand_month=spend["by_brand"],
            ),
            cfg,
        )

    return gate


def cmd_generate(args: argparse.Namespace) -> int:
    """plan-<date>.json の企画を生成投入する（§5 generate: 投入のみ）。

    予算ゲート（§13）: `config/budget.yaml` の上限と `.state/spend.json`（実績）を突き合わせ、
    上限の 95% に達したら以降を投入しない。投入したぶんの概算費を spend.json に加算する。
    出力: `.state/jobs-<date>.json`（poll-generation が続きを進める）。
    """
    plan_path, plans = _load_plans(args.date)
    date = plan_path.stem.replace("plan-", "")
    if getattr(args, "limit", None):
        plans = plans[: int(args.limit)]
    provider = get_provider(_provider_name())
    budget_cfg = load("budget")

    spend = _load_spend()
    jobs, skipped = submit_plans(plans, provider, budget_gate=_spend_gate(spend, budget_cfg))

    plan_by_id = {p.plan_id: p for p in plans}
    charged = 0.0
    for j in jobs:
        spend["by_brand"][plan_by_id[j.plan_id].brand.value] = round(
            spend["by_brand"].get(plan_by_id[j.plan_id].brand.value, 0.0) + j.cost_jpy, 2
        )
        charged += j.cost_jpy
    spend["today"] = round(spend["today"] + charged, 2)
    spend["month"] = round(spend["month"] + charged, 2)
    spend["total"] = round(spend["total"] + charged, 2)
    _save_spend(spend)

    out = STATE_DIR / f"jobs-{date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "date": date, "provider": provider.name,
        "jobs": [asdict(j) for j in jobs],
        "skipped": skipped,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"[generate] plan={plan_path.name} provider={provider.name} "
          f"投入={len(jobs)} スキップ={len(skipped)} 概算 +¥{charged:.0f}")
    print(f"[generate] 今月の生成費 ¥{spend['month']:.0f} / 上限 ¥{budget_cfg.get('monthly_budget', 0):.0f}")
    for s in skipped:
        print(f"  ! {s['plan_id']}: {s['reason']}")
    print(f"[generate] wrote {out}")

    if skipped:
        lines = [f"date={date} 投入={len(jobs)} スキップ={len(skipped)}"]
        lines += [f"  - {s['plan_id']}: {s['reason']}" for s in skipped]
        send_alert_email("[AI動画自動投稿] 予算ゲートで生成をスキップ", "\n".join(lines))

    return 0


def cmd_poll_generation(args: argparse.Namespace) -> int:
    """生成中ジョブを1歩進める（完了確認 → 品質判定 → NG は再生成）。§5 poll_generation。"""
    jobs_path = (
        STATE_DIR / f"jobs-{args.date}.json" if args.date else _latest_state("jobs-*.json")
    )
    if jobs_path is None or not jobs_path.exists():
        print("[poll-generation] jobs ファイルが無い。先に generate を実行してください")
        return 0
    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    date = payload.get("date") or jobs_path.stem.replace("jobs-", "")
    jobs = [_job_from_dict(d) for d in payload.get("jobs", [])]

    plan_path = STATE_DIR / f"plan-{date}.json"
    plans_by_id = {}
    if plan_path.exists():
        pj = json.loads(plan_path.read_text(encoding="utf-8"))
        plans_by_id = {p["plan_id"]: _plan_from_dict(p) for p in pj.get("plans", [])}

    provider = get_provider(_provider_name(payload.get("provider")))
    advance_jobs(jobs, provider, plans_by_id)

    payload["jobs"] = [asdict(j) for j in jobs]
    jobs_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    counts = summarize(jobs)
    print(f"[poll-generation] {jobs_path.name} → {counts}")
    for j in jobs:
        if j.error:
            print(f"  {j.plan_id} [{j.status.value}] {j.error}")
    print(f"[poll-generation] wrote {jobs_path}")
    return 0


def cmd_media(args: argparse.Namespace) -> int:
    """生成完了マスターを 9:16・音量正規化 → 媒体別派生に加工する（§12）。

    出力: `.state/media/<plan_id>/<platform>.mp4` と対応表 `.state/media-<date>.json`。
    ffmpeg が無い／マスターが未取得（mock）なら加工をスキップし、publish は元動画を使う。
    """
    jobs_path = (
        STATE_DIR / f"jobs-{args.date}.json" if args.date else _latest_state("jobs-*.json")
    )
    if jobs_path is None or not jobs_path.exists():
        print("[media] jobs ファイルが無い。先に generate / poll-generation を実行してください")
        return 0
    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    date = payload.get("date") or jobs_path.stem.replace("jobs-", "")

    plan_path = STATE_DIR / f"plan-{date}.json"
    plans = {
        p["plan_id"]: _plan_from_dict(p)
        for p in (json.loads(plan_path.read_text(encoding="utf-8")).get("plans", [])
                  if plan_path.exists() else [])
    }
    manifest_path = STATE_DIR / f"media-{date}.json"
    variants: dict[str, str] = {}

    if not ffmpeg_available():
        print("[media] ffmpeg が無いためスキップ（brew install ffmpeg）。publish は元動画を使う")
        _write_media_manifest(manifest_path, date, variants)
        return 0

    media_root = STATE_DIR / "media"
    made = skipped = 0
    for jd in payload.get("jobs", []):
        if jd.get("status") != GenerationStatus.SUCCEEDED.value:
            continue
        plan = plans.get(jd.get("plan_id"))
        src = (jd.get("local_path") or "").removeprefix("file://")
        if plan is None or not src or not os.path.exists(src):
            skipped += 1
            continue
        d = media_root / plan.plan_id
        d.mkdir(parents=True, exist_ok=True)
        try:
            master = normalize_master(src, str(d / "master.mp4"))
            for platform in plan.target_platforms:
                spec = MediaVariantSpec(platform=platform,
                                        duration_sec=plan.duration_target_sec)
                out = make_variant(master, spec, str(d / f"{platform.value}.mp4"))
                variants[f"{plan.plan_id}|{platform.value}"] = out
                made += 1
        except MediaError as e:
            print(f"  ! {plan.plan_id}: {e}")
            skipped += 1

    _write_media_manifest(manifest_path, date, variants)
    print(f"[media] 派生 {made} 本 / スキップ {skipped} 件 → {manifest_path.name}")
    return 0


def _write_media_manifest(path: Path, date: str, variants: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": date, "variants": variants}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_PUBLISH_STATUS = {
    "PUBLISHED": PostStatus.PUBLISHED,
    "ALREADY_PUBLISHED": PostStatus.PUBLISHED,
    "HOLD_POLICY_STALE": PostStatus.POLICY_HOLD,
    "HOLD_GUARD": PostStatus.POLICY_HOLD,
    "HOLD_POLICY": PostStatus.POLICY_HOLD,
    "SKIP_PLATFORM": PostStatus.SKIPPED,
    "REGENERATE": PostStatus.GENERATING,
    "FAILED": PostStatus.FAILED,
}


def _guard_map() -> dict[tuple[str, str], GuardVerdict]:
    p = STATE_DIR / "guard.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], GuardVerdict] = {}
    for t in data.get("targets", []) or []:
        try:
            action = GuardAction(t["action"])
        except (KeyError, ValueError):
            continue
        out[(t.get("brand"), t.get("platform"))] = GuardVerdict(
            action, " / ".join(t.get("reasons", []) or []), list(t.get("reasons", []) or [])
        )
    return out


def cmd_publish(args: argparse.Namespace) -> int:
    """生成完了ジョブを媒体別に投稿判定 → 投稿する（§8 §15）。既定は PUBLISH_MODE=dryrun。

    投稿レコードは管理DB（`sheets.get_store`、既定 `.state/db/posts.json`）に upsert。
    判定結果は `.state/publish-<date>.json`。冪等キーは DB の既存投稿から復元する。
    """
    jobs_path = (
        STATE_DIR / f"jobs-{args.date}.json" if args.date else _latest_state("jobs-*.json")
    )
    if jobs_path is None or not jobs_path.exists():
        print("[publish] jobs ファイルが無い。先に generate / poll-generation を実行してください")
        return 0
    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    date = payload.get("date") or jobs_path.stem.replace("jobs-", "")

    plan_path = STATE_DIR / f"plan-{date}.json"
    if not plan_path.exists():
        print(f"[publish] {plan_path.name} が無い")
        return 0
    plans = {
        p["plan_id"]: _plan_from_dict(p)
        for p in json.loads(plan_path.read_text(encoding="utf-8")).get("plans", [])
    }

    media_manifest = STATE_DIR / f"media-{date}.json"
    variants: dict[str, str] = (
        json.loads(media_manifest.read_text(encoding="utf-8")).get("variants", {})
        if media_manifest.exists() else {}
    )

    store = get_store(root=STATE_DIR / "db")
    # 冪等台帳（key -> platform_post_id）を DB の既存投稿から復元
    ledger: dict[str, str] = {
        f"{p.master_video_id}|{p.platform.value}|{p.account_id}": p.platform_post_id
        for p in store.list_posts()
        if p.platform_post_id
    }
    guard_map = _guard_map()
    mode = env("PUBLISH_MODE", "dryrun")

    outcomes: list[dict] = []
    for jd in payload.get("jobs", []):
        if jd.get("status") != GenerationStatus.SUCCEEDED.value:
            continue
        plan = plans.get(jd.get("plan_id"))
        if plan is None:
            continue
        # 同じ動画を複数媒体に使い回すため、生成費は媒体数で均等分割して記録する
        # （そのまま複製すると合計が実際の支出より水増しされる）。
        n_platforms = len(plan.target_platforms) or 1
        cost_per_platform = float(jd.get("cost_jpy", 0.0)) / n_platforms
        for platform in plan.target_platforms:
            account_id = f"{plan.brand.value}-{platform.value}"  # TODO: アカウント設定を config 化
            post = Post(
                post_key=f"{plan.plan_id}:{platform.value}",
                master_video_id=plan.plan_id, brand=plan.brand, platform=platform,
                account_id=account_id, concept_tag=plan.concept_tag, hook_type=plan.hook_type,
                character_id=plan.character_id, duration_sec=plan.duration_target_sec,
                oddity_level=plan.oddity_level, reality_level=plan.reality_level,
                prompt_version=plan.prompt_version,
                generation_cost_jpy=cost_per_platform,
                policy_version="", policy_result=PolicyDecision.PASS,
                status=PostStatus.PUBLISHING,
            )
            tags = select_hashtags(plan.brand.value, platform.value, date=date)
            cta = select_caption_cta(plan.concept_tag, date=date)
            species = {"cat": "子猫", "dog": "子犬"}.get(plan.brand.value, "")
            caption = f"{plan.concept_tag}な{species}"
            if cta:
                caption += f"\n\n{cta}"
            caption += f"\n\n{' '.join(tags)}"
            req = PublishRequest(
                post=post,
                video_path=variants.get(f"{plan.plan_id}|{platform.value}")
                or jd.get("local_path") or "",
                title=f"{plan.concept_tag}｜{plan.hook_type}",
                caption=caption.strip(),
                tags=tags,
            )
            publisher = (
                DryRunPublisher(platform, ledger=ledger)
                if mode == "dryrun" else get_publisher(platform, mode=mode, brand=plan.brand)
            )
            guard = guard_map.get((plan.brand.value, platform.value))
            oc = decide_and_publish(plan, platform, req, publisher=publisher, guard=guard)

            post.policy_version = oc.policy_version
            post.status = _PUBLISH_STATUS.get(oc.action, PostStatus.FAILED)
            if oc.published:
                post.platform_post_id = oc.platform_post_id
                existing = store.get_post(post.post_key)
                post.published_at = (
                    existing.published_at if existing and existing.published_at
                    else datetime.now(JST)
                )
                ledger[f"{post.master_video_id}|{platform.value}|{account_id}"] = (
                    oc.platform_post_id
                )
            store.upsert_post(post)
            outcomes.append(asdict(oc))

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"publish-{date}.json").write_text(
        json.dumps({"date": date, "mode": mode, "outcomes": outcomes},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    by_action: dict[str, int] = {}
    for oc in outcomes:
        by_action[oc["action"]] = by_action.get(oc["action"], 0) + 1
    print(f"[publish] date={date} mode={mode} → {by_action}")
    for oc in outcomes:
        if oc["action"] not in ("PUBLISHED", "ALREADY_PUBLISHED"):
            print(f"  ! {oc['plan_id']} [{oc['platform']}] {oc['action']}: "
                  f"{'; '.join(oc['reasons'])}")
    print(f"[publish] wrote {STATE_DIR / f'publish-{date}.json'}")
    return 0


def _latest_snapshots_by_label(store, post_key: str) -> dict[str, object]:
    by_label: dict[str, object] = {}
    for s in store.list_snapshots(post_key=post_key):
        cur = by_label.get(s.snapshot)
        if cur is None or s.collected_at > cur.collected_at:
            by_label[s.snapshot] = s
    return by_label


def _write_performance_json(store) -> int:
    """DB の 投稿×最新snapshot を daily-learning が読める形で `.state/performance.json` に。"""
    records = []
    for p in store.list_posts():
        snaps = _latest_snapshots_by_label(store, p.post_key)
        if not snaps:
            continue
        records.append({
            "post": {
                "brand": p.brand.value, "concept_tag": p.concept_tag, "hook_type": p.hook_type,
                "character_id": p.character_id, "reality_level": p.reality_level,
                "oddity_level": p.oddity_level, "duration_target_sec": p.duration_sec,
                "prompt_version": p.prompt_version, "platform": p.platform.value,
                "generation_cost_jpy": p.generation_cost_jpy,
                "published_at": p.published_at.isoformat() if p.published_at else None,
            },
            "snapshots": {lbl: snapshot_to_row(s) for lbl, s in snaps.items()},
        })
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "performance.json").write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(records)


def cmd_metrics(args: argparse.Namespace) -> int:
    """公開済み投稿の指標を回収して管理DBに保存する（§10.2）。

    経過時間に応じて 24h/72h/7d/latest を回収し、`.state/performance.json` を更新する
    （その後 daily-learning がそれを読む）。既定 PUBLISH_MODE=dryrun は決定的なダミー指標。
    """
    store = get_store(root=STATE_DIR / "db")
    now = datetime.now(JST)
    today = now.date().isoformat()
    posts = [
        p for p in store.list_posts()
        if p.status is PostStatus.PUBLISHED and p.platform_post_id
    ]

    # アカウント単位（brand×platform×account_id）のフォロワー数は投稿ごとに毎回
    # 問い合わせず、このコマンド1回の実行で使い回す。同じ値をアカウント日次DBにも
    # 記録し（既存の当日分の他フィールドは残す）、投稿前フォロワー数の照会に使う。
    followers_cache: dict[tuple[str, str], int | None] = {}
    account_daily_index: dict[str, AccountDaily] = {
        f"{a.date}|{a.account_id}": a for a in store.list_account_daily()
    }

    def _followers_before(post: Post) -> int | None:
        if post.published_at is None:
            return None
        pub_date = post.published_at.date().isoformat()
        candidates = [
            a for a in account_daily_index.values()
            if a.account_id == post.account_id and a.date < pub_date
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.date).followers

    collected = 0
    for post in posts:
        existing = {s.snapshot for s in store.list_snapshots(post_key=post.post_key)}
        due = due_snapshots(post, now, existing_labels=existing)
        if not due:
            continue
        publisher = get_publisher(post.platform, brand=post.brand)
        raw = publisher.fetch_metrics(post.platform_post_id)

        cache_key = (post.platform.value, post.brand.value)
        if cache_key not in followers_cache:
            try:
                followers_cache[cache_key] = publisher.fetch_account_followers()
            except Exception as e:  # noqa: BLE001 - 媒体API由来の未知の例外を握りつぶし継続
                print(f"[metrics] {post.platform.value} フォロワー数取得失敗: {e}")
                followers_cache[cache_key] = None
        current_followers = followers_cache[cache_key]

        followers_before = _followers_before(post)
        for label in due:
            snap = collect_snapshot(post, label, raw, followers_before=followers_before, now=now)
            if current_followers is not None:
                if snap.followers_before is None:
                    snap.followers_before = followers_before
                snap.followers_after = current_followers
            # "latest" は毎回この投稿の現在値を1行で持ちたい（履歴を残さず上書き）。
            # 24h/72h/7d はその時点の記録として一度きり追記する。
            if label == "latest":
                store.upsert_snapshot(snap)
            else:
                store.append_snapshot(snap)
            collected += 1

    # 今回取得できたフォロワー数を当日分としてアカウント日次DBに反映（既存フィールドは維持）
    for (platform_value, brand_value), followers in followers_cache.items():
        if followers is None:
            continue
        posts_for_account = [
            p for p in posts
            if p.platform.value == platform_value and p.brand.value == brand_value
        ]
        if not posts_for_account:
            continue
        account_id = posts_for_account[0].account_id
        key = f"{today}|{account_id}"
        row = account_daily_index.get(key) or AccountDaily(
            date=today, brand=Brand(brand_value), platform=Platform(platform_value),
            account_id=account_id,
        )
        row.followers = followers
        store.upsert_account_daily(row)

    n_records = _write_performance_json(store)
    print(f"[metrics] 公開済み {len(posts)} 件 / snapshot 追加 {collected} 件")
    print(f"[metrics] wrote .state/performance.json（records={n_records}）")
    return 0


_SNAPSHOT_FIELDS = (
    "views", "engaged_views", "likes", "comments", "shares", "impressions",
    "avg_watch_sec", "completion_rate", "followers_before", "followers_after",
    "revenue_jpy",
)


def _parse_snapshot(name: str, raw: dict) -> PerformanceSnapshot:
    collected = raw.get("collected_at")
    try:
        collected_at = (
            datetime.fromisoformat(str(collected).replace("Z", "+00:00"))
            if collected else datetime.now(JST)
        )
    except ValueError:
        collected_at = datetime.now(JST)
    return PerformanceSnapshot(
        post_key=str(raw.get("post_key", "")),
        snapshot=str(raw.get("snapshot", name)),
        collected_at=collected_at,
        **{f: raw.get(f) for f in _SNAPSHOT_FIELDS},
    )


def cmd_daily_learning(args: argparse.Namespace) -> int:
    """成績 → スコア → 媒体別の勝ちタグを抽出し .state/winning_tags.json に書く（§11）。

    入力 JSON: {"records": [{"post": {...企画タグ + generation_cost_jpy + published_at},
                             "snapshots": {"7d": {...PerformanceSnapshot の列}, ...}}]}
    入力が無ければブートストラップ（空の勝ちタグ）を書いて 0 で終わる。
    """
    in_path = Path(args.input) if args.input else STATE_DIR / "performance.json"
    out = Path(args.out) if args.out else STATE_DIR / "winning_tags.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    raw_records = []
    if in_path.exists():
        data = json.loads(in_path.read_text(encoding="utf-8"))
        raw_records = data.get("records", []) if isinstance(data, dict) else list(data)

    records = [
        (
            r.get("post", {}),
            {n: _parse_snapshot(n, s) for n, s in (r.get("snapshots", {}) or {}).items()},
        )
        for r in raw_records
    ]

    winning_tags = extract_winning_tags(records, load("scoring")) if records else []

    payload = {
        "generated_at": datetime.now(JST).isoformat(),
        "records_in": len(records),
        "winning_tags": winning_tags,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[daily-learning] input={in_path} records={len(records)}")
    if not records:
        print("[daily-learning] 成績データなし → ブートストラップ（勝ちタグ空）を書いた")
    else:
        print(f"[daily-learning] 勝ちタグ {len(winning_tags)} 件:")
        for w in winning_tags[:10]:
            print(
                f"  {w['score']:.3f}  {w['brand']:5s} {w['concept_tag']}/{w['hook_type']}"
                f"  r{w['reality_level']}/o{w['oddity_level']} {w['duration_target_sec']}s"
                f"  [{w['platform']}]"
            )
    print(f"[daily-learning] wrote {out}")
    return 0


def cmd_kill_switch(args: argparse.Namespace) -> int:
    """予算消化と異常シグナルから停止判定を出す（§13 §14）。

    入力 JSON:
      {"budget": {"today":.., "month":.., "total":.., "by_brand_month": {"cat":..}},
       "targets": [{"brand":"cat", "platform":"youtube", "signals": {...}}]}
    入力が無ければ全 ALLOW（0 で終了）。1つでも非 ALLOW なら 3 で終了
    （＝GitHub Actions ジョブ失敗＝通知メール）。
    """
    in_path = Path(args.input) if args.input else STATE_DIR / "guard_input.json"
    out = Path(args.out) if args.out else STATE_DIR / "guard.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    data = json.loads(in_path.read_text(encoding="utf-8")) if in_path.exists() else {}
    budget_cfg = load("budget")
    b = data.get("budget", {}) or {}
    spend = BudgetSpend(
        today=float(b.get("today", 0.0)),
        month=float(b.get("month", 0.0)),
        total=float(b.get("total", 0.0)),
        by_brand_month=b.get("by_brand_month", {}) or {},
    )

    budget_verdict = check_budget(spend, budget_cfg)

    target_rows = []
    verdicts = [budget_verdict]
    for t in data.get("targets", []) or []:
        brand, platform = t.get("brand", "?"), t.get("platform", "?")
        signals = dict(t.get("signals", {}) or {})
        if budget_verdict.blocked:
            signals.setdefault("budget_exceeded", True)
        row = combine([
            check_budget(spend, budget_cfg, brand=brand),
            check_kill_switch(brand, platform, signals),
        ])
        target_rows.append({
            "brand": brand, "platform": platform,
            "action": row.action.value,
            "reasons": row.triggers,
        })
        verdicts.append(row)

    overall = combine(verdicts)

    payload = {
        "evaluated_at": datetime.now(JST).isoformat(),
        "overall": overall.action.value,
        "budget": {
            "action": budget_verdict.action.value,
            "reasons": budget_verdict.triggers,
        },
        "targets": target_rows,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[kill-switch] input={in_path} overall={overall.action.value}")
    if budget_verdict.blocked:
        print(f"  予算: {budget_verdict.action.value}")
        for r in budget_verdict.triggers:
            print(f"    ! {r}")
    for row in target_rows:
        marker = "ok " if row["action"] == "ALLOW" else "!  "
        print(f"  {marker}{row['brand']}×{row['platform']}: {row['action']}")
        for r in row["reasons"]:
            print(f"      - {r}")
    print(f"[kill-switch] wrote {out}")

    if overall.action.value != "ALLOW":
        lines = [f"overall={overall.action.value}"]
        if budget_verdict.blocked:
            lines.append("予算: " + budget_verdict.action.value)
            lines += [f"  - {r}" for r in budget_verdict.triggers]
        for row in target_rows:
            if row["action"] != "ALLOW":
                lines.append(f"{row['brand']}×{row['platform']}: {row['action']}")
                lines += [f"  - {r}" for r in row["reasons"]]
        send_alert_email(
            f"[AI動画自動投稿] kill-switch作動: {overall.action.value}",
            "\n".join(lines),
        )

    return 0 if overall.action.value == "ALLOW" else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-media")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        if name == "plan-daily":
            p.add_argument("--date", help="YYYY-MM-DD (default: today)")
            p.add_argument("--winning-tags", help="learning 出力の JSON パス (default: .state/winning_tags.json)")
            p.add_argument("--out", help="出力先 JSON (default: .state/plan-<date>.json)")
        if name == "daily-learning":
            p.add_argument("--input", help="成績 JSON パス (default: .state/performance.json)")
            p.add_argument("--out", help="出力先 JSON (default: .state/winning_tags.json)")
        if name == "kill-switch":
            p.add_argument("--input", help="予算/シグナル JSON パス (default: .state/guard_input.json)")
            p.add_argument("--out", help="出力先 JSON (default: .state/guard.json)")
        if name in ("generate", "poll-generation", "media", "publish"):
            p.add_argument("--date", help="対象日 YYYY-MM-DD (default: .state の最新)")
        if name == "generate":
            p.add_argument("--limit", type=int, help="先頭 N 件だけ投入（テスト・小予算用）")

    args = parser.parse_args(argv)

    if args.command == "plan-daily":
        return cmd_plan_daily(args)
    if args.command == "policy-sync":
        return cmd_policy_sync(args)
    if args.command == "daily-learning":
        return cmd_daily_learning(args)
    if args.command == "kill-switch":
        return cmd_kill_switch(args)
    if args.command == "generate":
        return cmd_generate(args)
    if args.command == "poll-generation":
        return cmd_poll_generation(args)
    if args.command == "media":
        return cmd_media(args)
    if args.command == "publish":
        return cmd_publish(args)
    if args.command == "metrics":
        return cmd_metrics(args)

    print(f"[cli] command={args.command} — not implemented yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
