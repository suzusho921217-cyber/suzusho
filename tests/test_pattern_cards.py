"""型カード: 未分析の動画だけ分析・無料枠キーが無ければ何もしない・失敗しても止まらない。"""

import json
from types import SimpleNamespace

from src.mtg import pattern_cards as pc

OUT = [
    {"url": "https://www.youtube.com/shorts/AAAAAAAA", "title": "踊る猫", "channel": "x", "subs": 1000,
     "views": 5_000_000, "ratio": 5000.0},
    {"url": "https://www.youtube.com/shorts/BBBBBBBB", "title": "犬", "channel": "y", "subs": 2000,
     "views": 3_000_000, "ratio": 1500.0},
]


class _Client:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = fail
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, *, model, contents, config):
        uri = contents.parts[0].file_data.file_uri
        self.calls.append(uri)
        if any(f in uri for f in self.fail):
            raise RuntimeError("boom")
        return SimpleNamespace(text=json.dumps({
            "duration_sec": 9, "hook_0_2s": "猫のドアップ", "mimicable": True,
            "on_screen_text": [{"t": 0, "text": "踊る猫を見た瞬間…"}], "how_to_mimic": "テロップ＋8秒",
        }, ensure_ascii=False))


def test_analyzes_only_new_videos_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "STATE", tmp_path / "pattern_cards.json")
    c = _Client()
    cards, _ = pc.update_cards(OUT, client=c)
    assert set(cards) == {"AAAAAAAA", "BBBBBBBB"}
    assert c.calls[0] == "https://www.youtube.com/watch?v=AAAAAAAA"
    c2 = _Client()
    pc.update_cards(OUT, client=c2)
    assert c2.calls == []  # 二度目は分析しない（無料枠を浪費しない）
    text = pc.format_cards(cards)
    assert "踊る猫を見た瞬間…" in text and "真似し方" in text


def test_no_key_means_no_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "STATE", tmp_path / "pattern_cards.json")
    monkeypatch.delenv("GEMINI_ANALYSIS_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "paid-key-must-not-be-used")
    cards, log = pc.update_cards(OUT)
    assert cards == {} and "未設定" in log[0]


def test_one_failure_does_not_stop_others(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "STATE", tmp_path / "pattern_cards.json")
    cards, log = pc.update_cards(OUT, client=_Client(fail=("AAAAAAAA",)))
    assert set(cards) == {"BBBBBBBB"}
    assert any("分析失敗" in x for x in log)
