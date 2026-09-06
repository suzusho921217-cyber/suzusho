"""publishers.hashtags.select_hashtags（§9）。"""

from src.publishers.hashtags import select_hashtags

CFG = {
    "cat": {
        "youtube": {
            "always": ["#Shorts", "#猫", "#cat"],
            "pool": ["#子猫", "#kitten", "#かわいい猫", "#ねこ", "#癒し"],
            "per_video": 3,
        },
        "x": {"always": ["#猫"], "pool": [], "per_video": 2},
    },
}


def test_always_tags_come_first_and_pool_adds_per_video():
    tags = select_hashtags("cat", "youtube", date="2026-09-02", config=CFG)
    assert tags[:3] == ["#Shorts", "#猫", "#cat"]
    assert len(tags) == 6  # always 3 + pool 3


def test_deterministic_per_date():
    a = select_hashtags("cat", "youtube", date="2026-09-02", config=CFG)
    b = select_hashtags("cat", "youtube", date="2026-09-02", config=CFG)
    c = select_hashtags("cat", "youtube", date="2026-09-03", config=CFG)
    assert a == b
    assert a != c  # 日付が変われば pool の並びが変わる


def test_no_duplicates_case_insensitive():
    cfg = {"cat": {"youtube": {"always": ["#猫", "#CAT"], "pool": ["#cat", "#子猫"],
                               "per_video": 2}}}
    tags = select_hashtags("cat", "youtube", date="2026-09-02", config=cfg)
    lowered = [t.lower() for t in tags]
    assert len(lowered) == len(set(lowered))


def test_empty_pool_returns_only_always():
    assert select_hashtags("cat", "x", date="2026-09-02", config=CFG) == ["#猫"]


def test_breed_tag_is_added_from_character_id():
    tags = select_hashtags("dog", "instagram", date="2026-09-02",
                           character_id="dog_shiba", config={"dog": {"instagram": {
                               "always": ["#犬"], "pool": [], "per_video": 0}}})
    assert "#柴犬" in tags and "#shibainu" in tags


def test_unknown_or_missing_character_id_adds_no_breed_tag():
    base = {"dog": {"instagram": {"always": ["#犬"], "pool": [], "per_video": 0}}}
    assert select_hashtags("dog", "instagram", date="d", config=base) == ["#犬"]
    assert select_hashtags("dog", "instagram", date="d", character_id="dog_unknown",
                           config=base) == ["#犬"]


def test_unknown_brand_or_platform_returns_empty():
    assert select_hashtags("bird", "youtube", date="2026-09-02", config=CFG) == []
    assert select_hashtags("cat", "threads", date="2026-09-02", config=CFG) == []
