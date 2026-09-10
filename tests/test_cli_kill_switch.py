"""cli kill-switch の配線テスト（予算/シグナル JSON → 停止判定 JSON）。"""

import json
from datetime import datetime as _real_datetime

from src import cli
from src.cli import main


class _FixedDatetime(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 9, 10, 0, tzinfo=tz)


def test_kill_switch_no_input_allows(tmp_path, capsys):
    out = tmp_path / "guard.json"
    rc = main(["kill-switch", "--input", str(tmp_path / "missing.json"), "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "ALLOW"
    assert "overall=ALLOW" in capsys.readouterr().out


def test_kill_switch_budget_breach_exits_nonzero(tmp_path):
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"budget": {"month": 29000}}), encoding="utf-8")  # >95% of 30000
    out = tmp_path / "guard.json"
    rc = main(["kill-switch", "--input", str(inp), "--out", str(out)])
    assert rc == 3
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "STOP_NEW_GENERATION"
    assert data["budget"]["action"] == "STOP_NEW_GENERATION"


def test_kill_switch_target_signal_holds(tmp_path):
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({
        "budget": {"month": 500},   # 予算は余裕あり（シグナル側の HOLD を見たい）
        "targets": [
            {"brand": "cat", "platform": "youtube", "signals": {"platform_warnings": 3}},
            {"brand": "dog", "platform": "youtube", "signals": {}},
        ],
    }), encoding="utf-8")
    out = tmp_path / "guard.json"
    rc = main(["kill-switch", "--input", str(inp), "--out", str(out)])
    assert rc == 3
    data = json.loads(out.read_text(encoding="utf-8"))
    rows = {r["brand"]: r for r in data["targets"]}
    assert rows["cat"]["action"] == "HOLD"
    assert rows["dog"]["action"] == "ALLOW"
    assert data["overall"] == "HOLD"


def test_kill_switch_sheets_failure_stops_all(tmp_path):
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({
        "targets": [{"brand": "cat", "platform": "youtube",
                     "signals": {"cannot_record_post_id": True}}],
    }), encoding="utf-8")
    out = tmp_path / "guard.json"
    rc = main(["kill-switch", "--input", str(inp), "--out", str(out)])
    assert rc == 3
    assert json.loads(out.read_text(encoding="utf-8"))["overall"] == "STOP"


# --- 見守り: 今日のプランが無ければ通知（plan_daily がキューでキャンセルされた場合の検知）---

def test_kill_switch_alerts_when_todays_plan_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)  # plan-2026-09-09.json を置かない
    monkeypatch.setattr(cli, "datetime", _FixedDatetime)  # 10:00 JST（>=7時）
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "send_alert_email", lambda subject, body, **kw: sent.append((subject, body)))

    out = tmp_path / "guard.json"
    main(["kill-switch", "--input", str(tmp_path / "missing.json"), "--out", str(out)])

    assert len(sent) == 1
    assert "プラン" in sent[0][0]


def test_kill_switch_does_not_alert_when_todays_plan_exists(tmp_path, monkeypatch):
    (tmp_path / "plan-2026-09-09.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli, "datetime", _FixedDatetime)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "send_alert_email", lambda subject, body, **kw: sent.append((subject, body)))

    out = tmp_path / "guard.json"
    main(["kill-switch", "--input", str(tmp_path / "missing.json"), "--out", str(out)])

    assert sent == []
