"""模倣企画: 型の選び方（同じ型を3本・途中優先）・置き換え内容・キー無しなら何もしない。"""

import json
from types import SimpleNamespace

from src.common.models import Brand, ContentPlan, ExperimentFlag, Platform, PolicyRisk
from src.planner import mimic

PROMPT = ("主役は「柴犬の子犬」。舞台は明るい庭。 内容（あるある）: フリ: ...\n\n"
          "【禁止】人間の顔をはっきり映す、画面内の文字・字幕・テロップ\n【備考】explore")


def _plan(pid, brand=Brand.DOG):
    return ContentPlan(plan_id=pid, date="2026-10-04", brand=brand, concept_tag="あるある",
                       hook_type="いきなりドアップ", character_id="DOG_001", reality_level=4,
                       oddity_level=1, duration_target_sec=8, experiment_flag=ExperimentFlag.EXPLORE,
                       policy_risk=PolicyRisk.LOW, prompt_version="v3", prompt_text=PROMPT,
                       target_platforms=[Platform.YOUTUBE])


CARDS = {
    "AAA": {"mimicable": True, "ratio": 3559, "url": "https://youtube.com/shorts/AAA", "channel": "NightVeil",
            "hook_0_2s": "ミニカート", "beats": ["押す", "咥える"], "punchline": "1個だけ"},
    "BBB": {"mimicable": True, "ratio": 9999, "url": "u2", "channel": "x"},
    "CCC": {"mimicable": False, "ratio": 99999, "url": "u3"},
}


def test_pick_card_prefers_in_progress_then_ratio():
    assert mimic.pick_card(CARDS, {}, "dog", set())[0] == "BBB"           # 未使用なら比率が高い方
    log = {"AAA|dog": ["p1"]}
    assert mimic.pick_card(CARDS, log, "dog", set())[0] == "AAA"          # 途中の型を3本まで優先
    log = {"AAA|dog": ["p1", "p2", "p3"], "BBB|dog": ["p4", "p5", "p6"]}
    assert mimic.pick_card(CARDS, log, "dog", set()) is None              # 真似できない型は使わない
    assert mimic.pick_card(CARDS, {}, "dog", {"BBB"})[0] == "AAA"         # 同じ日に同じ型を重ねない


def test_apply_rewrites_plan_and_keeps_banned(tmp_path, monkeypatch):
    monkeypatch.setattr(mimic, "STATE_DIR", tmp_path)
    (tmp_path / "pattern_cards.json").write_text(json.dumps({"AAA": CARDS["AAA"]}))
    seen = {}

    def gen(*, model, contents, config):
        seen["prompt"] = contents
        return SimpleNamespace(text=json.dumps({"title": "お買い物ごっこ", "hook": "ミニカート押し",
                                                "veo_prompt": "主役は柴犬の子犬。0秒〜鼻でミニカートを押す…"},
                                               ensure_ascii=False))

    client = SimpleNamespace(models=SimpleNamespace(generate_content=gen))
    p = _plan("2026-10-04-dog-01")
    lines = mimic.apply([p], client=client)
    assert "主役は「柴犬の子犬」" in seen["prompt"] and "ミニカート" in seen["prompt"]
    assert p.concept_tag == "模倣" and p.hook_type == "ミニカート押し"
    assert p.prompt_text.startswith("主役は柴犬の子犬。0秒〜")
    assert "【禁止】人間の顔をはっきり映す" in p.prompt_text      # 規約の禁止事項は残す
    assert "mimic 1/3" in p.notes and "3559倍" in p.notes
    assert json.loads((tmp_path / "mimic_log.json").read_text()) == {"AAA|dog": ["2026-10-04-dog-01"]}
    assert lines and "mimic 1/3" in lines[0]


def test_no_key_or_no_cards_keeps_plans(tmp_path, monkeypatch):
    monkeypatch.setattr(mimic, "STATE_DIR", tmp_path)
    p = _plan("x")
    assert "型カードが無い" in mimic.apply([p])[0]
    (tmp_path / "pattern_cards.json").write_text(json.dumps(CARDS))
    monkeypatch.delenv("GEMINI_ANALYSIS_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "paid")
    assert "未設定" in mimic.apply([p])[0]
    assert p.prompt_text == PROMPT and p.concept_tag == "あるある"


def test_script_with_humans_is_rewritten_or_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(mimic, "STATE_DIR", tmp_path)
    (tmp_path / "pattern_cards.json").write_text(json.dumps({"AAA": CARDS["AAA"]}))
    outputs = iter([
        "0秒〜女性2人が驚いて目を見開く。シーンが切り替わり子犬が登場",
        "0秒〜子犬が玄関から飛び出し、画面外の声が驚く",
    ])
    prompts = []

    def gen(*, model, contents, config):
        prompts.append(contents)
        return SimpleNamespace(text=json.dumps({"title": "t", "hook": "h", "veo_prompt": next(outputs)},
                                               ensure_ascii=False))

    p = _plan("2026-10-04-dog-01")
    mimic.apply([p], client=SimpleNamespace(models=SimpleNamespace(generate_content=gen)))
    assert len(prompts) == 2 and "人間が映っている" in prompts[1] and "場面が切り替わっている" in prompts[1]
    assert p.prompt_text.startswith("0秒〜子犬が玄関から飛び出し")

    outputs2 = iter(["男性が顔を出す"] * 2)
    gen2 = lambda **kw: SimpleNamespace(text=json.dumps({"title": "t", "hook": "h", "veo_prompt": next(outputs2)},
                                                        ensure_ascii=False))
    q = _plan("2026-10-04-dog-02")
    mimic.apply([q], client=SimpleNamespace(models=SimpleNamespace(generate_content=gen2)))
    assert q.prompt_text == PROMPT and q.concept_tag == "あるある"   # 守れなければ通常企画のまま
