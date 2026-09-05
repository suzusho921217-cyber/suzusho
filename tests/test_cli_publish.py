"""cli publish の配線テスト（plan → generate → poll → publish、dry-run）。"""

import json

import pytest

from src.cli import main


@pytest.fixture
def generated(tmp_path, monkeypatch):
    monkeypatch.setattr("src.cli.STATE_DIR", tmp_path)
    out = tmp_path / "plan-2026-09-02.json"
    main(["plan-daily", "--date", "2026-09-02", "--out", str(out),
          "--winning-tags", str(tmp_path / "missing.json")])
    main(["generate", "--date", "2026-09-02"])
    main(["poll-generation", "--date", "2026-09-02"])
    return tmp_path


def _posts(root):
    return json.loads((root / "db" / "posts.json").read_text(encoding="utf-8"))


def test_publish_dryrun_publishes_all(generated):
    rc = main(["publish", "--date", "2026-09-02"])
    assert rc == 0
    pub = json.loads((generated / "publish-2026-09-02.json").read_text(encoding="utf-8"))
    assert pub["mode"] == "dryrun"
    assert {o["action"] for o in pub["outcomes"]} == {"PUBLISHED"}
    posts = _posts(generated)
    assert len(posts) == 6
    assert all(p["status"] == "PUBLISHED" and p["platform_post_id"] for p in posts.values())


def test_publish_is_idempotent_second_run(generated):
    main(["publish", "--date", "2026-09-02"])
    main(["publish", "--date", "2026-09-02"])
    pub = json.loads((generated / "publish-2026-09-02.json").read_text(encoding="utf-8"))
    assert {o["action"] for o in pub["outcomes"]} == {"ALREADY_PUBLISHED"}
    assert len(_posts(generated)) == 6  # 増えない


def test_publish_respects_guard_hold(generated):
    (generated / "guard.json").write_text(json.dumps({
        "targets": [{"brand": "cat", "platform": "youtube", "action": "HOLD",
                     "reasons": ["媒体警告が連続"]}],
    }), encoding="utf-8")
    main(["publish", "--date", "2026-09-02"])
    pub = json.loads((generated / "publish-2026-09-02.json").read_text(encoding="utf-8"))
    actions = {(o["plan_id"], o["action"]) for o in pub["outcomes"]}
    assert any(a == "HOLD_GUARD" for _, a in actions)
    # dog は止まらない
    assert any(o["action"] == "PUBLISHED" for o in pub["outcomes"])
