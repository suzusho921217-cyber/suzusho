"""agent-mtg: 5役職（analyst→researcher→marketer→critic→coordinator）を順番に呼び、
coordinatorの結論から安全な変更だけを自動反映し、結果をメールする。

金銭・ポリシーが絡む提案は絶対に自動反映しない（apply.py が構造的に許可しない）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.notify import send_alert_email
from src.mtg import roles
from src.mtg.apply import apply_all
from src.mtg.client import call_role
from src.mtg.context import STATE_DIR, gather_context

JST = timezone(timedelta(hours=9))
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class MtgResult:
    date: str
    transcripts: dict[str, str] = field(default_factory=dict)
    coordinator_json: dict | None = None
    apply_results: list[str] = field(default_factory=list)
    error: str | None = None


def _extract_json(text: str) -> dict | None:
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def run() -> MtgResult:
    date = datetime.now(JST).date().isoformat()
    result = MtgResult(date=date)

    context = gather_context()

    analyst_out = call_role(roles.ANALYST_SYSTEM, context)
    result.transcripts["analyst"] = analyst_out

    researcher_out = call_role(roles.RESEARCHER_SYSTEM, context, with_web_search=True)
    result.transcripts["researcher"] = researcher_out

    marketer_input = (
        f"{context}\n\n## analystの分析\n{analyst_out}\n\n## researcherの調査\n{researcher_out}"
    )
    marketer_out = call_role(roles.MARKETER_SYSTEM, marketer_input)
    result.transcripts["marketer"] = marketer_out

    critic_input = f"## marketerの提案\n{marketer_out}\n\n## 参考データ\n{context}"
    critic_out = call_role(roles.CRITIC_SYSTEM, critic_input)
    result.transcripts["critic"] = critic_out

    coordinator_input = (
        f"## analyst\n{analyst_out}\n\n## researcher\n{researcher_out}\n\n"
        f"## marketer\n{marketer_out}\n\n## critic\n{critic_out}"
    )
    coordinator_out = call_role(roles.COORDINATOR_SYSTEM, coordinator_input, max_tokens=4000)
    result.transcripts["coordinator"] = coordinator_out

    parsed = _extract_json(coordinator_out)
    result.coordinator_json = parsed
    if parsed is None:
        result.error = "coordinatorの出力からJSONを抽出できなかった（自動反映はスキップ）"
        return result

    auto_apply = parsed.get("auto_apply") or []
    result.apply_results = apply_all(auto_apply)
    return result


def _write_log(result: MtgResult) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"mtg-{result.date}.json"
    path.write_text(
        json.dumps(
            {
                "date": result.date,
                "transcripts": result.transcripts,
                "coordinator_json": result.coordinator_json,
                "apply_results": result.apply_results,
                "error": result.error,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return path


_ROLE_LABELS = {
    "analyst": "📊 分析役",
    "researcher": "🔎 調査役",
    "marketer": "💡 企画役",
    "critic": "🧐 批判役",
}


def _paras(text: str) -> str:
    """長い文を句点で改行して読みやすくする（HTML）。末尾の（…）は前の文にくっつける。"""
    import html as _html

    s = _html.escape(str(text)).strip()
    # 「。」の後で改行（ただし閉じ括弧・閉じ鉤括弧が続く場合は割らない）
    s = s.replace("。", "。<br>").replace("。<br>）", "。）").replace("。<br>」", "。」")
    return s.removesuffix("<br>")


def _email_body(result: MtgResult) -> str:
    """要点だけの HTML 本文。各役職の全文・統括レポートは .state/mtg-<date>.json に残す。"""
    import html as _html

    esc = _html.escape
    cj = result.coordinator_json or {}
    p: list[str] = [
        "<div style='font-size:14px;line-height:1.7'>",
        f"<h2 style='margin:0 0 10px'>🗓️ エージェントMTG {esc(result.date)}</h2>",
    ]

    if result.error:
        p.append(f"<p>⚠️ {esc(result.error)}</p>")

    # 1. 結論
    if cj.get("headline"):
        p.append(
            "<p style='background:#f4f6f8;padding:10px 12px;border-radius:6px'>"
            f"<b>📋 結論</b><br>{_paras(cj['headline'])}</p>"
        )

    # 自動で反映した変更があれば、承認不要でも必ず知らせる（透明性）
    if result.apply_results:
        li = "".join(f"<li>{esc(str(r))}</li>" for r in result.apply_results)
        p.append(
            "<h3 style='margin:16px 0 4px'>✅ 自動で反映した変更</h3>"
            f"<ul style='margin:0;padding-left:20px'>{li}</ul>"
        )

    # 2. 収益化までの進捗
    if cj.get("monetization_progress"):
        p.append(
            "<h3 style='margin:16px 0 4px'>💰 収益化までの進捗</h3>"
            f"<p style='margin:0'>{_paras(cj['monetization_progress'])}</p>"
        )

    # 3. 要ユーザー承認
    needs = cj.get("needs_user_approval") or []
    if needs:
        li = ""
        for it in needs:
            cost = it.get("estimated_cost_jpy_per_month", 0) or 0
            li += (
                f"<li style='margin-bottom:8px'>{_paras(it.get('description', ''))}"
                f"<br><span style='color:#888'>（月額目安 ¥{cost:,.0f}）</span></li>"
            )
        p.append(
            "<h3 style='margin:16px 0 4px'>⚠️ 要ユーザー承認（未実行）</h3>"
            f"<ul style='margin:0;padding-left:20px'>{li}</ul>"
        )

    # 4. 各エージェントのポイント
    topics = cj.get("agent_topics") or {}
    li = "".join(
        f"<li><b>{_ROLE_LABELS.get(k, k)}</b>：{_paras(topics[k])}</li>"
        for k in ("analyst", "researcher", "marketer", "critic")
        if topics.get(k)
    )
    if li:
        p.append(
            "<h3 style='margin:16px 0 4px'>🤖 各エージェントのポイント</h3>"
            f"<ul style='margin:0;padding-left:20px'>{li}</ul>"
        )

    p.append(
        "<p style='color:#999;font-size:12px;margin-top:18px'>"
        f"📄 各役職の全文・意思決定ログは .state/mtg-{esc(result.date)}.json"
        " / GitHub Actions のログ / スプレッドシート</p></div>"
    )
    return "".join(p)


def run_and_report() -> MtgResult:
    result = run()
    log_path = _write_log(result)
    print(f"[agent-mtg] wrote {log_path}")
    subject = f"[AI動画自動投稿] エージェントMTG {result.date}"
    if result.coordinator_json:
        subject += f" - {result.coordinator_json.get('headline', '')[:40]}"
    sent = send_alert_email(subject, _email_body(result), html=True)
    print(f"[agent-mtg] メール送信: {'成功' if sent else '失敗/未設定'}")
    if result.apply_results:
        for r in result.apply_results:
            print(f"[agent-mtg] {r}")
    return result
