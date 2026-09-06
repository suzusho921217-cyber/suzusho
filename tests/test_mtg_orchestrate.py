"""agent-mtg orchestrate.py: 5役職の呼び出し順序・JSON抽出・自動反映の配線を確認する。

実際のClaude API呼び出しはしない（call_roleをモック）。
"""

import json

import pytest

from src.mtg import orchestrate


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(orchestrate, "gather_context", lambda: "CONTEXT")
    monkeypatch.setattr(orchestrate, "send_alert_email", lambda subject, body, **kw: True)


def _coordinator_text(auto_apply=None, needs_approval=None):
    payload = {
        "headline": "テスト結論",
        "auto_apply": auto_apply or [],
        "needs_user_approval": needs_approval or [],
    }
    return f"統括の報告文\n\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"


def test_extract_json_parses_fenced_block():
    text = "前置き\n```json\n{\"a\": 1}\n```\n後書き"
    assert orchestrate._extract_json(text) == {"a": 1}


def test_extract_json_returns_none_when_absent():
    assert orchestrate._extract_json("JSONブロックが無い普通の文章") is None


def test_run_calls_five_roles_in_order_with_correct_web_search_flag(monkeypatch):
    calls = []

    def fake_call_role(system, user_content, *, with_web_search=False, **kwargs):
        calls.append((system, with_web_search))
        if system is orchestrate.roles.COORDINATOR_SYSTEM:
            return _coordinator_text()
        return f"{system[:10]}の出力"

    monkeypatch.setattr(orchestrate, "call_role", fake_call_role)
    monkeypatch.setattr(orchestrate, "apply_all", lambda items: [])

    result = orchestrate.run()

    systems_called = [c[0] for c in calls]
    assert systems_called == [
        orchestrate.roles.ANALYST_SYSTEM, orchestrate.roles.RESEARCHER_SYSTEM,
        orchestrate.roles.MARKETER_SYSTEM, orchestrate.roles.CRITIC_SYSTEM,
        orchestrate.roles.COORDINATOR_SYSTEM,
    ]
    # web検索を使うのはresearcherだけ
    assert calls[1][1] is True
    assert calls[0][1] is False and calls[2][1] is False and calls[3][1] is False and calls[4][1] is False
    assert result.error is None
    assert result.coordinator_json["headline"] == "テスト結論"


def test_run_passes_coordinator_auto_apply_to_apply_all(monkeypatch):
    auto_apply_items = [{"kind": "add_concept_tag", "brand": "cat", "tag": "新タグ"}]

    def fake_call_role(system, user_content, *, with_web_search=False, **kwargs):
        if system is orchestrate.roles.COORDINATOR_SYSTEM:
            return _coordinator_text(auto_apply=auto_apply_items)
        return "出力"

    captured = {}

    def fake_apply_all(items):
        captured["items"] = items
        return ["[applied] ダミー"]

    monkeypatch.setattr(orchestrate, "call_role", fake_call_role)
    monkeypatch.setattr(orchestrate, "apply_all", fake_apply_all)

    result = orchestrate.run()

    assert captured["items"] == auto_apply_items
    assert result.apply_results == ["[applied] ダミー"]


def test_run_sets_error_and_skips_apply_when_coordinator_has_no_json(monkeypatch):
    monkeypatch.setattr(orchestrate, "call_role", lambda system, content, **kw: "JSONを出さない出力")
    called = {"n": 0}
    monkeypatch.setattr(orchestrate, "apply_all", lambda items: called.__setitem__("n", called["n"] + 1) or [])

    result = orchestrate.run()

    assert result.error is not None
    assert called["n"] == 0  # JSON無しならapply_allは呼ばれない


def test_run_and_report_writes_log_and_sends_email(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "STATE_DIR", tmp_path)
    monkeypatch.setattr(orchestrate, "call_role",
                         lambda system, content, **kw: (
                             _coordinator_text() if system is orchestrate.roles.COORDINATOR_SYSTEM else "出力"
                         ))
    monkeypatch.setattr(orchestrate, "apply_all", lambda items: [])

    sent = {}
    monkeypatch.setattr(orchestrate, "send_alert_email",
                         lambda subject, body, **kw: sent.update(subject=subject, body=body, kw=kw) or True)

    result = orchestrate.run_and_report()

    log_path = tmp_path / f"mtg-{result.date}.json"
    assert log_path.exists()
    saved = json.loads(log_path.read_text(encoding="utf-8"))
    assert saved["coordinator_json"]["headline"] == "テスト結論"
    assert "テスト結論" in sent["subject"] or "エージェントMTG" in sent["subject"]
    # 本文は要点のみの HTML（結論 headline を含む・全文の議事録は含めない）
    assert sent["kw"].get("html") is True
    assert "<h2" in sent["body"] and "テスト結論" in sent["body"]
