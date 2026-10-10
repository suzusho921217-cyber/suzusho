"""テロップ（画面に焼き込む文字）を、出来上がった動画を見ながら無料枠の Gemini に考えさせる。

伸びている競合ショートの多くは「状況を説明する文字」で視聴を止めている（型カードで確認）。
一方で Veo に文字を描かせると崩れるので、Veo には文字なしで作らせ、完成後にこちらで重ねる。

- 動画を実際に見せてから書かせる（企画時点の想定とズレたテロップを付けないため）。
- 文体のお手本として、型カード（`.state/pattern_cards.json`）の実際のテロップを渡す。
- **無料枠専用の `GEMINI_ANALYSIS_API_KEY` だけ**を使う。無ければテロップ無しで進める（有料キーは使わない）。
- 結果は `.state/captions-<date>.json` に残し、同じ動画で二度呼ばない。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.common.config import env

STATE_DIR = Path(__file__).resolve().parents[2] / ".state"
MAX_LINES = 3
MAX_CHARS = 20          # 1枚のテロップの最大文字数（長いと読み切れない）
MAX_LINE = 11           # 1行の最大文字数（画面幅に収まる長さ）
# 2026-10-10 ユーザー判断「字幕いらない」で停止。False の間は Gemini も呼ばずテロップ無しで書き出す。
ENABLED = False

_PROMPT = """\
あなたはペット系ショート動画の編集者です。この{duration}秒の動画を見て、画面に焼き込むテロップを
2〜3枚考えてください。企画の狙い: 「{concept}」（冒頭の掴み: {hook}）。

ルール:
- 実際に映っている出来事だけを書く（映っていないこと・誇張した数字・続きのにおわせは禁止）
- 1枚 {max_chars} 文字以内。意味の切れ目で1〜2行に分け、1行 {max_line} 文字以内にする
- 最初の1枚は0秒から出し、「この先どうなる？」と思わせて見る人の手を止める一言にする
- 動物の気持ちを代弁する口語（「〜にゃ」「〜だワン」など）や、ツッコミ・実況が効果的
- 各テロップは1.5〜3秒表示。最後の1枚はオチに合わせる
- 伸びている競合動画の実際のテロップ（文体のお手本。内容は真似しない）:
{examples}

次の JSON だけを返す: [{{"start": 秒, "end": 秒, "lines": ["1行目", "2行目(無ければ省略)"]}}]
"""


def _examples(limit: int = 8) -> str:
    try:
        cards = json.loads((STATE_DIR / "pattern_cards.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "（お手本なし）"
    lines = []
    for c in cards.values():
        for t in c.get("on_screen_text") or []:
            text = str(t.get("text") or "").strip()
            if text and len(lines) < limit:
                lines.append(f"- 「{text}」（{c.get('title', '')[:20]}）")
    return "\n".join(lines) or "（お手本なし）"


def clean(raw, duration: float) -> list[dict]:
    """モデル出力を安全な形に整える（枚数・文字数・時間の範囲）。"""
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            start = max(0.0, float(item["start"]))
            end = min(float(duration), float(item["end"]))
            raw_lines = item.get("lines") or [item["text"]]
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        lines = []
        for ln in raw_lines if isinstance(raw_lines, list) else [raw_lines]:
            ln = " ".join(str(ln).split())
            # 長すぎる行は機械的に折る（モデルが守らなかったときの保険）
            lines += [ln[i:i + MAX_LINE] for i in range(0, len(ln), MAX_LINE)]
        lines = [ln for ln in lines if ln][:2]
        text = "".join(lines)[:MAX_CHARS]
        if text and end - start >= 0.8:
            out.append({"start": round(start, 2), "end": round(end, 2), "text": text, "lines": lines})
    out.sort(key=lambda c: c["start"])
    return out[:MAX_LINES]


def suggest(video_path: str, *, concept: str, hook: str, duration: float, client=None,
            sleep=time.sleep) -> list[dict] | None:
    """動画を Gemini に見せてテロップ案を返す。キーが無い・失敗したら None（テロップ無し）。"""
    if client is None:
        key = env("GEMINI_ANALYSIS_API_KEY")
        if not key:
            print("[captions] GEMINI_ANALYSIS_API_KEY 未設定のためテロップ無し（有料キーは使わない）")
            return None
        from src.mtg.pattern_cards import free_client

        client = free_client(key)
    from google.genai import types

    from src.mtg.pattern_cards import _FALLBACK_MODELS, _MODEL_DEFAULT, _is_busy

    uploaded = None
    try:
        uploaded = client.files.upload(file=video_path)
        for _ in range(30):  # アップロード後の処理待ち（通常は数秒）
            state = getattr(getattr(uploaded, "state", None), "name", None) or str(getattr(uploaded, "state", ""))
            if "PROCESSING" not in state:
                break
            sleep(2)
            uploaded = client.files.get(name=uploaded.name)
        prompt = _PROMPT.format(duration=int(duration), concept=concept, hook=hook,
                                max_chars=MAX_CHARS, max_line=MAX_LINE, examples=_examples())
        models = (env("GEMINI_ANALYSIS_MODEL", _MODEL_DEFAULT), *_FALLBACK_MODELS)
        last = None
        for model in models:
            for attempt in range(2):
                try:
                    resp = client.models.generate_content(
                        model=model, contents=[uploaded, prompt],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json", temperature=0.6),
                    )
                    return clean(json.loads(resp.text), duration)
                except Exception as e:
                    if not _is_busy(e):
                        raise
                    last = e
                    if attempt == 0:
                        sleep(15)
        raise last  # type: ignore[misc]
    except Exception as e:  # noqa: BLE001 - テロップが作れなくても投稿は止めない
        print(f"[captions] テロップ生成に失敗（テロップ無しで進める）: {type(e).__name__}: {str(e)[:160]}")
        return None
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception as e:  # noqa: BLE001 - 削除失敗は無料枠の一時ファイル（48時間で自動削除）なので無視
                print(f"[captions] アップロードした動画の削除に失敗（48時間で自動削除）: {e}")


def for_plan(date: str, plan_id: str, video_path: str, *, concept: str, hook: str,
             duration: float) -> list[dict]:
    """キャッシュがあればそれを返し、無ければ作って保存する。"""
    if not ENABLED:
        return []
    path = STATE_DIR / f"captions-{date}.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if plan_id in cache:
        return cache[plan_id]
    caps = suggest(video_path, concept=concept, hook=hook, duration=duration)
    if caps is None:  # キー無し・失敗はキャッシュしない（次の media 実行でやり直せるように）
        return []
    cache[plan_id] = caps
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return caps
