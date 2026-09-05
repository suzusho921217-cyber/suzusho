"""agent-mtg: 各役職に渡す共有コンテキストを config/ と 管理DB(Sheets/local) から集める。

対話版の .claude/agents/*.md が「最初に読むもの」として挙げているファイル群と
できるだけ揃える（ヘッドレス実行でも同じ材料を見て判断できるようにするため）。

パフォーマンス実績は `.state/performance.json` ではなく管理DB（本番は Sheets）から
直接組み立てる。CI は毎回まっさらなチェックアウトで .state/ が空のため。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from src.common.config import load
from src.common.models import PostStatus
from src.sheets.client import get_store

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


def gather_context() -> str:
    """全役職共通の状況説明テキスト（configの現状 + 直近の実績）。"""
    parts: list[str] = []

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
    parts.append("## config/budget.yaml（予算上限。この上限を超える提案はしない）\n" + json.dumps(budget, ensure_ascii=False, indent=2))

    return "\n\n".join(parts)
