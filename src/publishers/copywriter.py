"""投稿タイトル/本文をAIで作る（競合の異常値動画のタイトル・文体を参考にする）。

従来は「違和感｜0.5秒異常」のような内部用語がそのままタイトルになっていた。
視聴者に向けた言葉に直し、.state/outliers.json（MTGが毎日更新する異常値動画の実測）の
タイトルの型（冒頭の掴み・問いかけ・感情語・長さ）を真似る。
失敗・キー未設定のときは None を返し、呼び出し側が従来のテンプレートに戻す。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.common.config import env
from src.common.models import ContentPlan

STATE_DIR = Path(__file__).resolve().parents[2] / ".state"

_SYSTEM = """\
あなたはペット系ショート動画の投稿文ライターです。動画の企画情報から、
視聴者が思わず見たくなる「タイトル」と「本文」を日本語で1つ作ります。

ルール:
- タイトルは30字以内。冒頭に一番の掴み（驚き・問いかけ・「え？」となる一言）を置く。
  参考タイトル（登録者比で桁違いに再生された競合動画）の型を真似る。ただし文言の
  コピーはしない。内部用語（「違和感」「0.5秒異常」「二段オチ」等の企画タグ名）は使わない。
- 本文は1〜2文＋最後に視聴者がコメントしたくなる短い問いかけを1つ。100字以内。
- ハッシュタグは書かない（別で付く）。絵文字は1〜2個まで。
- 動画に映らないことを断言しない（嘘・誇大表現は禁止）。医療・効果効能の主張もしない。
- 動画の内容に書かれていない数字（「1秒で」「3つの表情」等）・展開のにおわせ（「その直後は…？」等、
  実際には無い続きを匂わせる書き方）は禁止。動画の内容欄にある事実だけを書く。
- 「◯◯」「〜が起こります」のように中身をぼかさない。動画で実際に起きることを具体的に一言で書く。
- 「0.5秒」など企画の内部用語・フック名を本文に出さない。
- 出力はJSONのみ: {"title": "...", "body": "..."}
"""


def _outlier_titles(limit: int = 8) -> list[str]:
    p = STATE_DIR / "outliers.json"
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
        return [str(x["title"]) for x in items[:limit] if x.get("title")]
    except Exception:  # noqa: BLE001 - 無ければ参考なしで作る
        return []


def write_copy(plan: ContentPlan, platform: str) -> tuple[str, str] | None:
    """(title, body) を返す。作れなければ None。"""
    if not env("ANTHROPIC_API_KEY"):
        return None
    try:
        from src.mtg.client import call_role

        species = {"cat": "子猫", "dog": "子犬"}.get(plan.brand.value, "ペット")
        refs = "\n".join(f"- {t}" for t in _outlier_titles()) or "（なし）"
        user = (
            f"媒体: {platform}\n被写体: {species}\n企画の方向: {plan.concept_tag} / {plan.hook_type}\n"
            f"動画の内容: {(plan.prompt_text or '')[:400]}\n\n参考タイトル:\n{refs}"
        )
        out = call_role(_SYSTEM, user, max_tokens=400)
        s, e = out.find("{"), out.rfind("}")
        d = json.loads(out[s:e + 1])
        title, body = str(d["title"]).strip()[:100], str(d["body"]).strip()
        return (title, body) if title and body else None
    except Exception:  # noqa: BLE001 - 投稿は止めず従来テンプレートへ
        return None
