"""agent-mtg: Claude APIの薄いラッパー。

コスト優先でHaikuを既定モデルにする（環境変数 MTG_MODEL で上書き可）。
researcherロールのみWeb検索ツールを付ける。
"""

from __future__ import annotations

from src.common.config import env

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 3000


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

    resp = client.messages.create(**kwargs)
    return "".join(block.text for block in resp.content if block.type == "text")
