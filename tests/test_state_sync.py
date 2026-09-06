"""state_sync: ワークフロー間で .state/ を GCS 経由で引き継ぐ層のテスト（GCS はフェイク）。"""

import base64
import hashlib

import pytest

from src.common import state_sync


class _FakeBlob:
    def __init__(self, store: dict, name: str):
        self._store = store
        self.name = name

    @property
    def md5_hash(self):
        data = self._store.get(self.name)
        return base64.b64encode(hashlib.md5(data).digest()).decode() if data is not None else None

    def upload_from_filename(self, path):
        with open(path, "rb") as fh:
            self._store[self.name] = fh.read()

    def download_to_filename(self, path):
        with open(path, "wb") as fh:
            fh.write(self._store[self.name])


class _FakeBucket:
    def __init__(self, store: dict):
        self.store = store

    def blob(self, name):
        return _FakeBlob(self.store, name)

    def list_blobs(self, prefix=""):
        return [_FakeBlob(self.store, k) for k in sorted(self.store) if k.startswith(prefix)]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    state = tmp_path / ".state"
    state.mkdir()
    remote: dict = {}
    monkeypatch.setattr(state_sync, "STATE_DIR", state)
    monkeypatch.setattr(state_sync, "_bucket", lambda: _FakeBucket(remote))
    monkeypatch.setattr(state_sync, "_bucket_name", lambda: "fake")
    state_sync._pulled_md5.clear()
    return state, remote


def test_noop_when_disabled(wired, monkeypatch):
    state, remote = wired
    monkeypatch.delenv("STATE_SYNC", raising=False)
    (state / "plan.json").write_text("x")
    assert state_sync.push() == 0
    assert remote == {}


def test_push_only_uploads_changed_files(wired, monkeypatch):
    state, remote = wired
    monkeypatch.setenv("STATE_SYNC", "1")

    (state / "plan.json").write_text("plan-v1")
    (state / "sub").mkdir()
    (state / "sub" / "job.json").write_text("job-v1")
    assert state_sync.push() == 2
    assert remote["state/plan.json"] == b"plan-v1"
    assert remote["state/sub/job.json"] == b"job-v1"

    # pull で md5 台帳を作り直す → 変更なしなら push は 0 件
    state_sync.pull()
    assert state_sync.push() == 0

    # 1 ファイルだけ変える → その 1 件だけ上がる
    (state / "plan.json").write_text("plan-v2")
    assert state_sync.push() == 1
    assert remote["state/plan.json"] == b"plan-v2"


def test_pull_restores_tree(wired, monkeypatch):
    state, remote = wired
    monkeypatch.setenv("STATE_SYNC", "1")
    remote["state/winning_tags.json"] = b"tags"
    remote["state/generation/cat-01.mp4"] = b"\x00\x01video"

    assert state_sync.pull() == 2
    assert (state / "winning_tags.json").read_bytes() == b"tags"
    assert (state / "generation" / "cat-01.mp4").read_bytes() == b"\x00\x01video"
