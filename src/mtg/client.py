"""agent-mtg: Claude APIの薄いラッパー。

コスト優先でHaikuを既定モデルにする（環境変数 MTG_MODEL で上書き可）。
researcherロールのみWeb検索ツールを付ける。

5役職(analyst→researcher→marketer→critic→coordinator)を順に呼ぶ設計なので、
一時的なAPI過負荷等の突発エラーが1回でも起きると会議全体・メール送信ごと
落ちてしまう（2026-09-07 朝のagent_mtg失敗はこれが原因の可能性が高い）。
5xx・過負荷・タイムアウト・レート制限は一過性なので軽くリトライする。
認証エラー等（4xx）はリトライしても直らないのでそのまま投げる。
"""

from __future__ import annotations

import time

from src.common.config import env

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 3000
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SEC = 5.0


def _model() -> str:
    return env("MTG_MODEL", _DEFAULT_MODEL)


def call_role(
    system: str, user_content: str, *, with_web_search: bool = False,
    max_tokens: int = _MAX_TOKENS,
) -> str:
    """1役職ぶんのAPI呼び出し。テキスト応答を返す（複数text blockは結合）。"""
    import anthropic

    api_key = env("ANTHROPIC_API_KEY", required=True)
    client = anthropic.Anthropic(api_key=api_key)

    kwargs: dict = {
        "model": _model(),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }
    if with_web_search:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    resp = _create_with_retry(client, kwargs)
    return "".join(block.text for block in resp.content if block.type == "text")


def _create_with_retry(client, kwargs: dict, *, sleep=time.sleep):
    """一過性エラー(過負荷・レート制限・タイムアウト・接続断)だけ数回リトライする。"""
    import anthropic

    transient = (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    )
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return client.messages.create(**kwargs)
        except transient as e:
            if attempt == _MAX_ATTEMPTS:
                raise
            print(f"[mtg] Claude API 一時エラー（{attempt}/{_MAX_ATTEMPTS}回目）: {e}. "
                  f"{_RETRY_BACKOFF_SEC}秒後にリトライ")
            sleep(_RETRY_BACKOFF_SEC * attempt)
