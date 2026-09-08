"""src/sheets/client.py: Sheets API 呼び出しの一過性エラーのリトライ挙動を確認する。"""

from __future__ import annotations

import pytest
from googleapiclient.errors import HttpError

from src.sheets import client


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "error"


def _http_error(status: int) -> HttpError:
    return HttpError(_Resp(status), b"{}", uri="https://example.com")


class _Request:
    def __init__(self, fail_statuses: list[int]) -> None:
        self._fail_statuses = list(fail_statuses)
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self._fail_statuses:
            raise _http_error(self._fail_statuses.pop(0))
        return "ok"


def test_execute_with_retry_succeeds_after_transient_errors():
    req = _Request([503, 429])
    sleeps: list[float] = []

    result = client._execute_with_retry(req, sleep=sleeps.append)

    assert result == "ok"
    assert req.calls == 3
    assert len(sleeps) == 2  # 1・2回目の失敗後にだけスリープする


def test_execute_with_retry_gives_up_after_max_attempts():
    req = _Request([503, 503, 503])

    with pytest.raises(HttpError):
        client._execute_with_retry(req, sleep=lambda _: None)

    assert req.calls == client._MAX_ATTEMPTS


def test_execute_with_retry_does_not_retry_non_transient_errors():
    req = _Request([401])

    with pytest.raises(HttpError):
        client._execute_with_retry(req, sleep=lambda _: None)

    assert req.calls == 1  # 認証エラー等はリトライしない
