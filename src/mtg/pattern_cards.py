"""競合の異常値動画を Gemini に**実際に見せて**「型カード」にする（調査役・企画役の材料）。

以前はタイトル・タグ・再生数しか見ておらず、冒頭で何が起きるか・テロップ・尺・音・編集が
分からないまま「型を真似る」と言っていた（2026-10-03 の1か月レビューで判明）。

- 対象: `outliers.collect_outliers()` が拾った動画のうち、まだカードが無いもの（1日 _MAX_NEW 本まで）。
  一度作ったカードは `.state/pattern_cards.json` に貯めて使い回す（同じ動画を二度分析しない）。
- キー: **無料枠専用の `GEMINI_ANALYSIS_API_KEY` だけ**を使う。無ければ何もしない。
  動画生成（Veo）用の有料キーには絶対にフォールバックしない（前払いを勝手に減らさないため）。
- 失敗しても会議は止めない。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.config import env

STATE = Path(__file__).resolve().parents[2] / ".state" / "pattern_cards.json"
JST = timezone(timedelta(hours=9))
_MODEL_DEFAULT = "gemini-3.6-flash"
# 混雑(503)・レート制限(429)のときに順に試す無料枠モデル。どれも無料枠キーでのみ呼ぶ。
_FALLBACK_MODELS = ("gemini-3.8-flash", "gemini-3.5-flash-lite")
_RETRY_WAIT_SEC = 15
_MAX_NEW = 5          # 1日に新しく分析する本数の上限（無料枠のレート制限にも余裕を持たせる）
_REPORT_N = 10        # 会議に渡すカード数（新しく伸びた順）

_PROMPT = """\
あなたはショート動画の構成作家です。この動画を最初から最後まで見て、「なぜ伸びたか」と
「AI生成の8〜15秒の猫・犬ショートで同じ型を再現するにはどうするか」を分析してください。
推測ではなく、実際に画面と音で確認できたことだけを書くこと。日本語で、次の JSON だけを返す:

{
  "duration_sec": 動画の長さ(秒),
  "subject": "主役（動物の種類・実写かAIか・人間の有無）",
  "hook_0_2s": "最初の0〜2秒で画面に何が映り、何が起きるか（視聴者が止まる理由）",
  "on_screen_text": [{"t": 秒, "text": "画面に出る文字（そのまま）"}],
  "beats": ["展開を時系列で（例: 0s 〜 / 3s 〜 / 6s オチ）"],
  "punchline": "オチ・山場",
  "editing": "カット数・スロー/早送り・ズーム・ループの有無など",
  "audio": "BGM・効果音・声・鳴き声（流行りの音源ならその特徴）",
  "why_it_works": "伸びた理由（1〜3点）",
  "mimicable": true または false,
  "how_to_mimic": "AI生成（Veo・1カット8秒または2カット15秒）＋テロップ焼き込み＋BGM で再現する具体案。テロップの文言案も書く",
  "not_mimicable_reason": "真似できない/すべきでない場合の理由（実写まとめ・リンク誘導・他人の素材・規約リスクなど）。無ければ空文字"
}
"""


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cards: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")


def _video_id(url: str) -> str:
    m = re.search(r"(?:shorts/|v=)([\w-]{6,})", url)
    return m.group(1) if m else url


def _parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def analyze_video(client, url: str, *, model: str) -> dict | None:
    """1本を Gemini に見せてカードにする。失敗は None。"""
    from google.genai import types

    resp = client.models.generate_content(
        model=model,
        contents=types.Content(parts=[
            types.Part(file_data=types.FileData(file_uri=url.replace("/shorts/", "/watch?v="))),
            types.Part(text=_PROMPT),
        ]),
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
    )
    return _parse(resp.text)


def _is_busy(e: Exception) -> bool:
    text = str(e)
    return any(k in text for k in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "high demand"))


def _analyze_with_fallback(client, url: str, models, *, sleep=time.sleep) -> dict | None:
    """混雑・レート制限なら同じモデルで1回待って再試行し、だめなら次の無料モデルへ。"""
    last: Exception | None = None
    for model in models:
        for attempt in range(2):
            try:
                return analyze_video(client, url, model=model)
            except Exception as e:
                if not _is_busy(e):
                    raise
                last = e
                if attempt == 0:
                    sleep(_RETRY_WAIT_SEC)
    raise last  # type: ignore[misc]


def update_cards(outliers: list[dict], *, client=None, now: datetime | None = None,
                 max_new: int = _MAX_NEW, sleep=None) -> tuple[dict, list[str]]:
    """未分析の異常値動画をカード化して貯める。(全カード, ログ) を返す。"""
    sleep = sleep or time.sleep
    cards = _load()
    log: list[str] = []
    todo = [o for o in outliers if _video_id(o["url"]) not in cards][:max_new]
    if not todo:
        return cards, log
    if client is None:
        key = env("GEMINI_ANALYSIS_API_KEY")
        if not key:
            log.append("GEMINI_ANALYSIS_API_KEY 未設定のため動画の中身は未分析（無料枠キー専用。有料キーは使わない）")
            return cards, log
        from google import genai

        client = genai.Client(api_key=key)
    models = (env("GEMINI_ANALYSIS_MODEL", _MODEL_DEFAULT), *_FALLBACK_MODELS)
    now = now or datetime.now(JST)
    for o in todo:
        try:
            card = _analyze_with_fallback(client, o["url"], models, sleep=sleep)
        except Exception as e:  # noqa: BLE001 - 1本の失敗で残りと会議を止めない
            log.append(f"分析失敗 {o['url']}: {type(e).__name__}: {str(e)[:120]}")
            continue
        if not card:
            log.append(f"分析結果を読めず {o['url']}")
            continue
        cards[_video_id(o["url"])] = {
            **card, "url": o["url"], "title": o.get("title"), "channel": o.get("channel"),
            "subs": o.get("subs"), "views": o.get("views"), "ratio": round(o.get("ratio", 0)),
            "analyzed_at": now.isoformat(timespec="minutes"),
        }
        log.append(f"分析 {o['url']}")
    _save(cards)
    return cards, log


def format_cards(cards: dict, *, n: int = _REPORT_N) -> str:
    if not cards:
        return "（型カードはまだ無い）"
    items = sorted(cards.values(), key=lambda c: c.get("analyzed_at", ""), reverse=True)[:n]
    out = []
    for c in items:
        texts = " / ".join(f"{t.get('t')}s「{t.get('text')}」" for t in (c.get("on_screen_text") or [])[:4])
        out.append(
            f"### 「{c.get('title')}」 {c.get('channel')}（登録者{c.get('subs') or 0:,}・"
            f"再生{c.get('views') or 0:,}・{c.get('ratio')}倍） {c.get('url')}\n"
            f"- 尺 {c.get('duration_sec')}秒 / 主役: {c.get('subject')}\n"
            f"- 冒頭0〜2秒: {c.get('hook_0_2s')}\n"
            f"- テロップ: {texts or 'なし'}\n"
            f"- 展開: {' → '.join(c.get('beats') or [])}\n"
            f"- オチ: {c.get('punchline')} / 編集: {c.get('editing')} / 音: {c.get('audio')}\n"
            f"- 伸びた理由: {c.get('why_it_works')}\n"
            + (f"- 真似し方: {c.get('how_to_mimic')}" if c.get("mimicable")
               else f"- 真似しない: {c.get('not_mimicable_reason')}")
        )
    return "\n".join(out)
