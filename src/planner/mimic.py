"""模倣企画: 型カード（競合の伸びた動画を実際に見た分析）から、1本ずつ Veo の台本を作る。

1か月レビュー（2026-10-03）で、方針「異常値動画の型を真似る」にもかかわらず企画70本中0本が
特定の競合動画を元にしていなかった（企画はタグ×フックの機械的な組み合わせのみ）。そこで
plan-daily の最後に、各枠の中身を「元ネタ動画の型」に置き換える。

- 型の選び方: 真似できる（mimicable）カードのうち、ブランドごとに「同じ型を3本」試し切る。
  途中の型を優先し、次に登録者比の高い型。使った回数は `.state/mimic_log.json`。
- 主役（うちのキャラ）と【禁止】事項は元の企画のものをそのまま残す（ブランドと規約を守る）。
- 台本は無料枠の Gemini（`GEMINI_ANALYSIS_API_KEY`）で書く。キーが無い・型カードが無い・
  失敗したら元の企画のまま（従来どおり投稿は止めない）。有料キーは使わない。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from src.common.config import env
from src.common.models import ContentPlan

STATE_DIR = Path(__file__).resolve().parents[2] / ".state"
USES_PER_CARD = 3

_PROMPT = """\
あなたはショート動画の構成作家です。下の「元ネタ」は登録者の数千倍再生された競合ショートを
実際に見て分析したものです。この型（掴み・展開・オチ）を、うちの主役で再現する
動画生成AI（Veo）向けの台本を書いてください。

## うちの主役の見た目と舞台（この描写は変えずにそのまま台本の冒頭に使う）
{character}

## 元ネタの型
{card}

## 制約（必ず守る）
- 1カット約8秒・縦型9:16・実写と見分けがつかない映像。0〜1秒で元ネタと同じ種類の「手が止まる掴み」、
  7秒前後でオチ
- **掴み・展開・オチの構造は元ネタのまま保つ**。転ぶ・コケる・眠くなる等のありがちなオチに
  置き換えない（元ネタのオチが転倒の場合だけ転倒にする）
- 主役はうちの動物。元ネタの人間や別の動物の役割は、主役の動物自身・小道具・画面外の気配
  （手だけ・声だけ）に置き換える。人間の顔ははっきり映さない。子ども・乳幼児は出さない
- 画面に文字・字幕・テロップを描かない（テロップは後で別に付ける）
- 痛がる・怯える・危険に見える描写はしない
- 元ネタの映像をそのまま写さない。型（構成と笑いどころ）だけを借りる

次の JSON だけを返す:
{{"title": "企画名（15字以内）", "hook": "冒頭の掴み（15字以内）",
  "veo_prompt": "日本語の台本。主役と舞台の描写 → 0秒〜/3秒〜/6秒〜の展開 → カメラと音（BGMなし、生活音と鳴き声）"}}
"""


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _mimic_cfg() -> dict:
    try:
        from src.common.config import load

        return load("mimic") or {}
    except Exception:  # noqa: BLE001
        return {}


def pick_card(cards: dict, log: dict, brand: str, taken: set[str],
              *, pinned: list[str] | None = None, retired: list[str] | None = None) -> tuple[str, dict] | None:
    """ブランドごとに、途中の型を優先して3本まで。次に会議が指名した型（pinned）、次に登録者比の高い型。

    retired（会議が「もう真似ない」とした型）は使わない。
    """
    pinned = list(pinned or [])
    retired = set(retired or [])
    usable = [(vid, c) for vid, c in cards.items()
              if c.get("mimicable") and vid not in taken and vid not in retired
              and len(log.get(f"{vid}|{brand}", [])) < USES_PER_CARD]
    if not usable:
        return None

    def rank(x):
        vid, c = x
        pin = pinned.index(vid) if vid in pinned else len(pinned)
        return (-len(log.get(f"{vid}|{brand}", [])), pin, -(c.get("ratio") or 0))

    usable.sort(key=rank)
    return usable[0]


def _split_prompt(prompt: str) -> tuple[str, str]:
    """元の台本を「主役と舞台」と「【禁止】以降」に分ける。"""
    head, sep, tail = (prompt or "").partition("【禁止】")
    character = head.split("内容（")[0].strip()
    # 性格・お決まりの動き（「コケる」「眠くなる」等）は渡さない。台本がいつものオチに引っ張られるため
    character = re.sub(r"性格は.*?(?=舞台は|$)", "", character, flags=re.DOTALL).strip()
    banned = (sep + tail).split("【備考】")[0].strip()
    return character, banned


# 台本の機械チェック（AI は禁止事項を読んでも破ることがある）。人は「手」「声」「足元」だけ可。
_HUMAN_WORDS = ("女性", "男性", "女の子", "男の子", "子ども", "子供", "乳幼児", "人間の赤ちゃん", "少女", "少年",
                "人物", "人間が", "人が", "飼い主が", "顔を出", "目を見開", "表情の人")
_CUT_WORDS = ("シーンが切り替", "場面が切り替", "場面転換", "カットが変わ", "別のシーン")


def violations(text: str) -> list[str]:
    out = []
    hits = [w for w in _HUMAN_WORDS if w in text]
    if hits:
        out.append(f"人間が映っている（{'・'.join(hits)}）。人は手・足元・画面外の声だけにする")
    if any(w in text for w in _CUT_WORDS):
        out.append("場面が切り替わっている。1つの連続した場面にする")
    return out


def _card_text(c: dict) -> str:
    keys = ("subject", "hook_0_2s", "beats", "punchline", "editing", "why_it_works", "how_to_mimic")
    return json.dumps({k: c.get(k) for k in keys}, ensure_ascii=False, indent=1)


def _write_script(client, character: str, card: dict, *, sleep=time.sleep) -> dict | None:
    """台本を書かせ、機械チェックに引っかかったら理由を伝えて1回だけ書き直させる。"""
    prompt = _PROMPT.format(character=character, card=_card_text(card))
    script = _call(client, prompt, sleep=sleep)
    if script is None:
        return None
    problems = violations(script["veo_prompt"])
    if problems:
        retry = prompt + "\n\n## 前回の台本の問題（必ず直す）\n" + "\n".join(f"- {x}" for x in problems)
        script = _call(client, retry, sleep=sleep)
        if script is None or violations(script["veo_prompt"]):
            return None
    return script


def _call(client, prompt: str, *, sleep=time.sleep) -> dict | None:
    from google.genai import types

    from src.mtg.pattern_cards import _FALLBACK_MODELS, _MODEL_DEFAULT, _is_busy, _parse

    last = None
    for model in (env("GEMINI_ANALYSIS_MODEL", _MODEL_DEFAULT), *_FALLBACK_MODELS):
        for attempt in range(2):
            try:
                resp = client.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json",
                                                       temperature=0.7),
                )
                out = _parse(resp.text)
                if out and out.get("veo_prompt"):
                    return out
                return None
            except Exception as e:
                if not _is_busy(e):
                    raise
                last = e
                if attempt == 0:
                    sleep(15)
    raise last  # type: ignore[misc]


def apply(plans: list[ContentPlan], *, client=None, sleep=time.sleep) -> list[str]:
    """各企画を型カードの模倣企画に置き換える（できたものだけ）。ログ行を返す。"""
    cards = _read(STATE_DIR / "pattern_cards.json", {})
    if not any(c.get("mimicable") for c in cards.values()):
        return ["型カードが無い（まだ真似できる型が無い）ため通常企画のまま"]
    if client is None:
        key = env("GEMINI_ANALYSIS_API_KEY")
        if not key:
            return ["GEMINI_ANALYSIS_API_KEY 未設定のため通常企画のまま（有料キーは使わない）"]
        from google import genai

        client = genai.Client(api_key=key)

    log_path = STATE_DIR / "mimic_log.json"
    log = _read(log_path, {})
    lines: list[str] = []
    taken: set[str] = set()  # 同じ日に同じ型を2ブランドで使わない（型の検証を分散）
    cfg = _mimic_cfg()
    for p in plans:
        picked = pick_card(cards, log, p.brand.value, taken,
                           pinned=cfg.get("pinned"), retired=cfg.get("retired"))
        if picked is None:
            lines.append(f"{p.plan_id}: 使える型が残っていないため通常企画のまま")
            continue
        vid, card = picked
        character, banned = _split_prompt(p.prompt_text or "")
        try:
            script = _write_script(client, character, card, sleep=sleep)
        except Exception as e:  # noqa: BLE001 - 1本の失敗で他の企画と投稿を止めない
            lines.append(f"{p.plan_id}: 台本作成に失敗（通常企画のまま）: {type(e).__name__}: {str(e)[:100]}")
            continue
        if not script:
            lines.append(f"{p.plan_id}: 台本が読めない／禁止事項を守れなかったため通常企画のまま")
            continue
        n = len(log.get(f"{vid}|{p.brand.value}", [])) + 1
        src = f"{card.get('url')}（{card.get('ratio')}倍・{card.get('channel')}）"
        p.concept_tag = "模倣"
        p.hook_type = str(script.get("hook") or "")[:20] or p.hook_type
        p.notes = f"mimic {n}/{USES_PER_CARD}: {script.get('title', '')} ← {src}"
        p.prompt_text = f"{script['veo_prompt'].strip()}\n\n{banned}\n【備考】{p.notes}".strip()
        log.setdefault(f"{vid}|{p.brand.value}", []).append(p.plan_id)
        taken.add(vid)
        lines.append(f"{p.plan_id}: {p.notes}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return lines
