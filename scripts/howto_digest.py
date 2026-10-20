"""「再生数を伸ばす方法」系の解説動画20本を無料枠 Gemini に見せて、主張を箇条書きにする（使い捨て）。

2026-10-20 ユーザー依頼: インフルエンサーの伸ばし方解説を10名分ずつ見て共通項を探す。
キーは無料枠専用（GEMINI_ANALYSIS_API_KEY）のみ。有料キーは使わない。結果は howto/<id>.json。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import env
from src.mtg.pattern_cards import (
    _FALLBACK_MODELS,
    _MODEL_DEFAULT,
    _is_busy,
    _parse,
    free_client,
)

OUT = Path(__file__).resolve().parents[1] / "howto"
GAP_SEC = 70  # 無料枠の1分あたりトークン上限に当たらないよう1本ごとに空ける

VIDEOS = {
    "youtube": [
        "VUT6HhqKkBA", "N-q-XLQymnU", "Zc3ac7EWdbE", "QXfugR3ZIAs", "Xir0QtWTq3k",
        "u_oNzUfC3lg", "bStza7wEbWY", "N5xV9KTYtok", "wZ9nJMSEG1Q", "K_z_3Q1Itbk",
    ],
    "instagram": [
        "s8602PzsUtE", "dzYQ2bTku2U", "kTfZyQ2zomA", "zBRbnwXk19M", "RAvC1gN-5RY",
        "Vf-wV9I4fjY", "9C-EHy-c_G8", "QxPnUD799N0", "n2-ogpTY_w4", "mBvQeK3_txM",
    ],
}

_PROMPT = """\
この動画は「{platform} で再生数を伸ばす方法」の解説です。最初から最後まで見て、話し手が
勧めている具体的なやり方を、漏れなく1つずつ取り出してください。言い換えや水増しはしない。
動画で言っていないことは書かない。

各項目:
- claim: 主張を1文（日本語）
- how: 具体的なやり方（秒数・数字・例があればそのまま）
- category: 作り方（冒頭/構成/尺/映像/音/文字）/ タイトル・説明・ハッシュタグ / 設定 / 投稿運用（頻度・時間・再投稿）/ 分析 / その他
- evidence: 根拠の種類。数字や実験を示した / 本人の体験談 / 公式発表を引用 / 根拠なし
- evidence_detail: 示した数字や出典があれば1文

次の JSON だけを返す:
{{"speaker": "話し手・チャンネル名", "summary": "動画全体の要点を2文",
  "claims": [{{"claim": "", "how": "", "category": "", "evidence": "", "evidence_detail": ""}}]}}
"""


def digest(client, platform: str, vid: str) -> dict:
    from google.genai import types

    last = None
    for model in (env("GEMINI_ANALYSIS_MODEL", _MODEL_DEFAULT), *_FALLBACK_MODELS):
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=types.Content(parts=[
                        types.Part(file_data=types.FileData(file_uri=f"https://www.youtube.com/watch?v={vid}")),
                        types.Part(text=_PROMPT.format(platform=platform)),
                    ]),
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json", temperature=0.2,
                        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
                    ),
                )
                out = _parse(resp.text) or {"raw": resp.text}
                out["model"] = model
                return out
            except Exception as e:
                if not _is_busy(e):
                    raise
                last = e
                time.sleep(90)
    raise last  # type: ignore[misc]


def main() -> int:
    key = env("GEMINI_ANALYSIS_API_KEY")
    if not key:
        print("GEMINI_ANALYSIS_API_KEY 未設定（有料キーは使わない）")
        return 1
    client = free_client(key)
    OUT.mkdir(exist_ok=True)
    failed = 0
    for platform, vids in VIDEOS.items():
        for vid in vids:
            path = OUT / f"{platform}_{vid}.json"
            try:
                out = digest(client, platform, vid)
                out.update(platform=platform, video_id=vid)
                path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"OK {platform} {vid}: {len(out.get('claims') or [])}件")
            except Exception as e:  # noqa: BLE001 - 1本の失敗で残りを止めない
                failed += 1
                print(f"NG {platform} {vid}: {e}")
            time.sleep(GAP_SEC)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
