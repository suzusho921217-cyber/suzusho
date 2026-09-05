"""異常時のメール通知（§13 §14 のガードレール発火時に使う）。

Gmail のアプリパスワードで SMTP 送信する。標準ライブラリの smtplib のみ使用。
必要な環境変数（無ければ何もせず False を返す。呼び出し側の処理は止めない）:
  GMAIL_SENDER_ADDRESS  … 送信元 Gmail アドレス
  GMAIL_APP_PASSWORD    … そのアカウントのアプリパスワード（16桁）
  ALERT_EMAIL_TO        … 通知先アドレス
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from src.common.config import env

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 465


def send_alert_email(subject: str, body: str) -> bool:
    """送信できたら True。設定不足や送信失敗時は False（例外は投げない）。"""
    sender = env("GMAIL_SENDER_ADDRESS")
    password = env("GMAIL_APP_PASSWORD")
    to_addr = env("ALERT_EMAIL_TO")
    if not (sender and password and to_addr):
        print(f"[notify] 送信スキップ（GMAIL_SENDER_ADDRESS/GMAIL_APP_PASSWORD/ALERT_EMAIL_TO 未設定）: {subject}")
        return False

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr

    try:
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, timeout=15) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, [to_addr], msg.as_string())
        return True
    except (smtplib.SMTPException, OSError) as e:
        print(f"[notify] メール送信失敗: {e}")
        return False
