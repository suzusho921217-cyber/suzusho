"""publishers.hashtags.select_caption_cta（§2 視聴者参加）。"""

from src.publishers.hashtags import select_caption_cta

CFG = {
    "general": ["うちの子もやる人〜", "10点満点で何点？", "名前どうする？"],
    "by_concept": {
        "二段オチ": ["オチ予想できた？", "着地何点？"],
    },
}


def test_concept_specific_preferred_and_deterministic():
    a = select_caption_cta("二段オチ", date="2026-09-08", config=CFG)
    b = select_caption_cta("二段オチ", date="2026-09-08", config=CFG)
    assert a == b
    # concept 固有 or general のどれか
    assert a in CFG["by_concept"]["二段オチ"] + CFG["general"]


def test_changes_with_date():
    seen = {select_caption_cta("二段オチ", date=f"2026-09-{d:02d}", config=CFG)
            for d in range(1, 15)}
    assert len(seen) > 1  # 日によって変わる


def test_unknown_concept_falls_back_to_general():
    cta = select_caption_cta("知らないタグ", date="2026-09-08", config=CFG)
    assert cta in CFG["general"]


def test_empty_config_returns_empty_string():
    assert select_caption_cta("二段オチ", date="2026-09-08", config={}) == ""
