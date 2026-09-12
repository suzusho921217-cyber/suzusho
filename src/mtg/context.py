"""agent-mtg: 各役職に渡す共有コンテキストを config/ と 管理DB(Sheets/local) から集める。

対話版の .claude/agents/*.md が「最初に読むもの」として挙げているファイル群と
できるだけ揃える（ヘッドレス実行でも同じ材料を見て判断できるようにするため）。

パフォーマンス実績は `.state/performance.json` ではなく管理DB（本番は Sheets）から
直接組み立てる。CI は毎回まっさらなチェックアウトで .state/ が空のため。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.config import load
from src.common.models import PostStatus
from src.sheets.client import decision_to_row, get_store

STATE_DIR = Path(__file__).resolve().parents[2] / ".state"


def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _latest_state(glob: str) -> Path | None:
    matches = sorted(STATE_DIR.glob(glob))
    return matches[-1] if matches else None


def _performance_summary(store) -> str:
    posts = [
        p for p in store.list_posts()
        if p.status is PostStatus.PUBLISHED and p.platform_post_id
    ]
    if not posts:
        return "まだ公開済みの投稿が無い"

    records = []
    for post in posts:
        snaps = store.list_snapshots(post_key=post.post_key)
        latest = next((s for s in snaps if s.snapshot == "latest"), None)
        records.append({
            "post_key": post.post_key, "brand": post.brand.value,
            "platform": post.platform.value, "concept_tag": post.concept_tag,
            "hook_type": post.hook_type,
            "published_at": post.published_at.isoformat() if post.published_at else None,
            "generation_cost_jpy": post.generation_cost_jpy,
            "latest_snapshot": asdict(latest) if latest else None,
        })
    return json.dumps(records, ensure_ascii=False, indent=2, default=str)[:8000]


_KNOWN_METRIC_GAPS = """\
## 既知のAPI仕様上の欠損（バグではない・毎回の異常報告に含めない）

これらは媒体API側の仕様上の制約で、うちのコードを直しても解消しない。
analystは「データの健全性」チェックでこれらを欠損として指摘しないこと
（指摘してもユーザーは対応できず、毎回同じ話が繰り返されるだけになるため）。

- YouTube completion_rate（完視聴率）: YouTube Analytics APIが
  averageViewDuration/engagedViewsを全Shorts投稿で常に0返す（2026-09-11実データ確認済）。
  YouTube Shorts特有の既知の制約。学習側は既にこの指標を除外する重み設定で対応済み。
- YouTube revenue_jpy（収益）: チャンネルが未収益化（登録者1,000人+90日で
  ショート1,000万再生などYouTubeパートナープログラムの条件未達）の間は
  そもそも収益データが存在しない。これは正常な状態で、収益化条件を満たすまで解消しない。
- YouTube impressions（表示回数）: YouTube Analytics APIにこの指標の識別子自体が
  存在しない（権限の問題ではない）。公開APIでは取得不可。
- Instagram impressions（Reels）: Instagram Media Insights APIがReelsでは
  この指標を非対応（他のメディア種別では取れるらしいがReelsは明示的に除外されている）。
- Instagram eng視聴数（engaged views相当）: Instagramにはそもそもこの概念が存在しない
  （YouTubeのengagedViewsに相当するものがない）。「-」表示が正常。
- Flwr数(前日比) / 再生数(前日比)がnull: 投稿・計測を始めたばかりで「前日の基準値」が
  まだ無いだけ（正常）。増減が0という意味ではなく「まだ比較できない」という意味。
- 2026-09-12にYouTubeの犬(Dog Moments)チャンネルのOAuth認証が一時的に失効し、
  この日の一部投稿で全指標が欠損していた（同日中に再認証して解消済み）。
  「特定の日だけ特定ブランドの数値が全滅している」ように見えるのはこれが原因の
  可能性が高く、現在は解消済みなので継続対応は不要。
"""

_ALLOCATION_NOTES = """\
## ブランド配分の仕組み（毎回確認してから「配分を調整する」と決めないこと）

- 猫/犬の投稿本数配分は、winning_tagsのスコアに比例配分するアルゴリズムが
  「毎日の新規生成」に対して自動で決めている。coordinatorが直接指定する手段は
  存在しない（auto_applyのkindに配分系は set_allocation_ratio しか無く、これは
  「活用(exploit) vs 探索(explore)」の全体比率だけで、ブランド間の配分比率とは別物）。
  「dog配分を増やす/調整する」という決定を出しても、それを実行するコードが無いため
  plan-json には反映されない。ブランド間配分を変えたいなら、まず「そのための
  auto_applyを追加してほしい」とneeds_user_approvalで報告すること。
- brand_max_ratio（config/scoring.yaml、既定60%）は「その日新しく生成する分」だけの
  上限で、投稿全体の累積比率の上限ではない。現在の1日の生成本数（2本）だと
  猫/犬それぞれ最大1本=機械的に必ず50:50になる。「累積で見ると猫が偏っている」のは
  本数を絞る前（〜2026-09-10）の投稿が母数に残っているだけで、直近の生成は
  ちゃんと50:50なので、これ自体は対応不要な過去の名残り。
"""


def _recent_engineering_changes() -> str:
    """直近のgitコミットログ（エンジニアが手動で行った修正・実装）。

    人間のエンジニア（Claude Code等）がこのリポジトリに加えた変更を、
    エージェントが常に把握した状態で会議できるようにするための材料。
    手動でメモを更新し続ける運用は忘れられるので、gitログから自動生成する。
    """
    import subprocess

    repo_root = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(
            ["git", "log", "--since=14 days ago", "--date=format:%Y-%m-%d",
             "--pretty=format:- %ad %h %s"],
            cwd=repo_root, capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "## 直近のエンジニアリング変更（コミットログ）\n取得できなかった（git不在 or 浅いclone）"
    if not out:
        return "## 直近のエンジニアリング変更（コミットログ）\n直近14日の変更なし"
    return "## 直近のエンジニアリング変更（コミットログ、直近14日）\n" + out[:4000]


def gather_context() -> str:
    """全役職共通の状況説明テキスト（configの現状 + 直近の実績）。"""
    parts: list[str] = [_KNOWN_METRIC_GAPS, _ALLOCATION_NOTES, _recent_engineering_changes()]

    brands = load("brands")
    parts.append("## config/brands.yaml（ブランド定義）\n" + json.dumps(brands, ensure_ascii=False, indent=2))

    planning = load("planning")
    parts.append("## config/planning.yaml（企画タグ・フックのプール）\n" + json.dumps(planning, ensure_ascii=False, indent=2))

    hashtags = load("hashtags")
    parts.append("## config/hashtags.yaml（現行ハッシュタグ）\n" + json.dumps(hashtags, ensure_ascii=False, indent=2))

    store = get_store()
    parts.append("## 投稿実績（管理DBの投稿ごとの最新指標）\n" + _performance_summary(store))

    winning = _read_json(STATE_DIR / "winning_tags.json")
    if winning is None:
        parts.append("## winning_tags.json\nまだ無い、またはこの実行環境では未取得（daily-learningの直近出力）")
    else:
        parts.append("## winning_tags.json（勝ちタグ）\n" + json.dumps(winning, ensure_ascii=False, indent=2)[:4000])

    plan_path = _latest_state("plan-*.json")
    if plan_path is not None:
        plan = _read_json(plan_path)
        parts.append(f"## {plan_path.name}（直近の配分・企画）\n" + json.dumps(plan, ensure_ascii=False, indent=2)[:4000])

    budget = load("budget")
    parts.append("## config/budget.yaml（予算上限。monthly_budget は絶対に超えない）\n" + json.dumps(budget, ensure_ascii=False, indent=2))

    parts.append("## 生成費の消化状況（今月）\n" + _spend_summary(budget))

    parts.append("## 意思決定ログ（過去14日 + 未評価の判断）\n" + _decision_log_summary(store))

    return "\n\n".join(parts)


def _spend_summary(budget: dict) -> str:
    """今月の生成費 / 残額 / 残り日数 / 現在ペースの持ち日数。set_daily_slots のペース判断用。"""
    import calendar

    spend = _read_json(STATE_DIR / "spend.json") or {}
    month = float(spend.get("month", 0.0))
    monthly_budget = float(budget.get("monthly_budget", 5000) or 5000)
    stop_at = monthly_budget * float(budget.get("automatic_stop_ratio", 0.95))
    remaining = max(0.0, stop_at - month)

    gen = load("generation").get("veo", {}) or {}
    per_sec = float(gen.get("price_jpy_per_sec", 8) or 8)
    per_video = per_sec * max(gen.get("allowed_durations") or [8])
    slots = int(load("scoring")["allocation"].get("total_daily_slots", 3))

    today = datetime.now(timezone(timedelta(hours=9))).date()
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
    daily_cost = per_video * slots
    days_covered = int(remaining // daily_cost) if daily_cost else 999
    return json.dumps({
        "今月の生成費": round(month),
        "自動停止ライン(95%)": round(stop_at),
        "残り(停止までに使える額)": round(remaining),
        "今月の残り日数": days_left,
        "1本の単価": round(per_video),
        "現在の1日本数": slots,
        "現ペースの1日コスト": round(daily_cost),
        "現ペースで停止までに持つ日数": days_covered,
        "月末まで持たせるなら1日": max(1, int(remaining / (days_left * per_video))) if days_left else slots,
    }, ensure_ascii=False, indent=2)


def _decision_log_summary(store) -> str:
    """統括が「過去の判断」を踏まえて決めるための材料（ルール3・4・9）。"""
    try:
        decisions = store.list_decisions()
    except Exception:  # noqa: BLE001 - タブ未作成など。会議は止めない
        return "まだ意思決定ログが無い（初回）"
    if not decisions:
        return "まだ意思決定ログが無い（初回）"
    today = datetime.now(timezone(timedelta(hours=9))).date()
    cutoff = (today - timedelta(days=14)).isoformat()
    keep = [
        d for d in decisions
        if d.date >= cutoff or not d.result  # 直近14日 or 未評価
    ]
    keep.sort(key=lambda d: d.date, reverse=True)
    rows = [decision_to_row(d) for d in keep[:30]]
    return json.dumps(rows, ensure_ascii=False, indent=2)[:6000]
