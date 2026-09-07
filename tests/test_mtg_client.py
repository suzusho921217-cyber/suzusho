"""src/mtg/client.py: 一過性エラーのリトライ挙動を確認する（実API呼び出しはしない）。"""

from __future__ import annotations

import anthropic
import httpx
import pytest

from src.mtg import client


def _connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        message="boom", request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


def _fake_response():
    return object()


def test_create_with_retry_succeeds_after_transient_errors(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []

    class _Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise _connection_error()
                return "ok"

    result = client._create_with_retry(_Client(), {}, sleep=sleeps.append)

    assert result == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2  # 1・2回目の失敗後にだけスリープする


def test_create_with_retry_gives_up_after_max_attempts():
    calls = {"n": 0}

    class _Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                calls["n"] += 1
                raise _connection_error()

    with pytest.raises(anthropic.APIConnectionError):
        client._create_with_retry(_Client(), {}, sleep=lambda _: None)

    assert calls["n"] == client._MAX_ATTEMPTS


def test_create_with_retry_does_not_retry_non_transient_errors():
    calls = {"n": 0}

    class _Client:
        class messages:
            @staticmethod
            def create(**kwargs):
                calls["n"] += 1
                raise anthropic.AuthenticationError(
                    "invalid api key", response=httpx.Response(
                        401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                    ),
                    body=None,
                )

    with pytest.raises(anthropic.AuthenticationError):
        client._create_with_retry(_Client(), {}, sleep=lambda _: None)

    assert calls["n"] == 1  # 4xx はリトライしない
