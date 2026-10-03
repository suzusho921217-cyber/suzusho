"""経理チェック: 使用額の見積もり・前払い残高・異常検知・メールの出し分け。"""

import json
from datetime import datetime

from src.accounting.check import run_and_notify, run_check
from src.accounting.usage import JST


def _state(tmp_path, *, veo_today=2, claude_calls=3):
    s = tmp_path / ".state"
    s.mkdir()
    (s / "jobs-2026-10-03.json").write_text(json.dumps({
        "date": "2026-10-03",
        "jobs": [{"plan_id": f"p{i}", "cost_jpy": 88.0} for i in range(veo_today)],
    }))
    (s / "jobs-2026-09-29.json").write_text(json.dumps({  # 前月・購入前は数えない
        "date": "2026-09-29", "jobs": [{"plan_id": "old", "cost_jpy": 64.0}],
    }))
    rows = [{"at": "2026-10-03T05:40:00+09:00", "model": "claude-haiku-4-5-20251001",
             "input": 10_000, "output": 2_000, "web_search": 1} for _ in range(claude_calls)]
    (s / "llm_usage-2026-10.json").write_text(json.dumps(rows))
    (s / "publish-2026-10-03.json").write_text(json.dumps({"outcomes": [
        {"platform": "instagram", "action": "PUBLISHED"},
        {"platform": "youtube", "action": "PUBLISHED"},
    ]}))
    return s


NOW = datetime(2026, 10, 3, 8, 0, tzinfo=JST)


def test_month_usage_and_prepaid(tmp_path):
    res = run_check(state=_state(tmp_path), now=NOW, gcs_bucket_mb=lambda: 1.6)
    assert res.month["veo"] == 176
    # haiku: (10k*1 + 2k*5)/1M = $0.02 + 検索 $0.01 = $0.03 × 3回 × ¥160 ≒ ¥14
    assert res.month["claude"] == 14
    assert res.month["instagram_posts"] == 1
    assert res.prepaid["veo"]["remaining"] == 5000 - 176
    assert res.prepaid["anthropic"]["remaining"] == 10 - 0.09
    assert res.anomalies == []


def test_detects_anomalies(tmp_path):
    res = run_check(state=_state(tmp_path, veo_today=6, claude_calls=40), now=NOW,
                    gcs_bucket_mb=lambda: 900.0)
    keys = {k for k, _ in res.anomalies}
    assert {"veo_jobs", "claude_calls", "gcs_bucket_mb"} <= keys


def test_mail_every_5_days_and_alert_once_a_day(tmp_path):
    state = _state(tmp_path)
    sent = []

    def send(subject, body):
        sent.append(subject)
        return True

    run_and_notify(state=state, now=NOW, send=send, gcs_bucket_mb=lambda: 1.6)
    assert len(sent) == 1 and "経理報告" in sent[0]          # 初回は定期報告
    for d in (4, 5, 6, 7):                                     # 4日間は何も送らない
        run_and_notify(state=state, now=NOW.replace(day=d), send=send, gcs_bucket_mb=lambda: 1.6)
    assert len(sent) == 1
    run_and_notify(state=state, now=NOW.replace(day=8), send=send, gcs_bucket_mb=lambda: 1.6)
    assert len(sent) == 2                                      # 5日後に次の報告

    # 異常は即メール。同じ日に何度点検しても1回だけ
    run_and_notify(state=state, now=NOW.replace(day=9), send=send, gcs_bucket_mb=lambda: 900.0)
    run_and_notify(state=state, now=NOW.replace(day=9), send=send, gcs_bucket_mb=lambda: 900.0)
    assert len(sent) == 3 and sent[2].startswith("【要確認】")


def test_record_llm_usage_appends(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from src.accounting import usage

    monkeypatch.setattr(usage, "STATE_DIR", tmp_path)
    u = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=None,
                        cache_creation_input_tokens=0,
                        server_tool_use=SimpleNamespace(web_search_requests=2))
    usage.record_llm_usage("claude-haiku-4-5-20251001", u)
    usage.record_llm_usage("claude-haiku-4-5-20251001", SimpleNamespace(input_tokens=1, output_tokens=1))
    rows = json.loads(next(tmp_path.glob("llm_usage-*.json")).read_text())
    assert [r["web_search"] for r in rows] == [2, 0]
    assert rows[0]["input"] == 100 and rows[0]["cache_read"] == 0


def test_progress_page_renders(tmp_path):
    from types import SimpleNamespace

    from src.accounting import progress
    from src.common.models import Brand, Platform, PostStatus

    res = run_check(state=_state(tmp_path), now=NOW, gcs_bucket_mb=lambda: 1.6)
    post = SimpleNamespace(status=PostStatus.PUBLISHED, platform_post_id="v", post_key="k",
                           brand=Brand.CAT, platform=Platform.YOUTUBE,
                           published_at=datetime(2026, 10, 3, 12, 0, tzinfo=JST))
    store = SimpleNamespace(
        list_account_daily=lambda: [SimpleNamespace(date="2026-10-03", brand=Brand.CAT,
                                                    platform=Platform.YOUTUBE, followers=25)],
        list_posts=lambda: [post],
        list_snapshots=lambda post_key: [SimpleNamespace(snapshot="latest", views=1028)],
        list_decisions=list,
    )
    md = progress.build(store, res)
    assert "| YouTube 猫 | 25 |" in md
    assert "| YouTube 猫 | 1 | 2026-10-03 | 1,028 |" in md
    # 予定ペース(2本×¥88)で見積もる: 残り ¥4,824 ÷ ¥176 ≒ 27日 → 10/30
    assert "2026-10-30: Veo（動画生成） の前払いが尽きる見込み" in md
