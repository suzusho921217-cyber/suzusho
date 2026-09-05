"""common.notify: 環境変数未設定時は送信せずスキップすること、送信経路の呼び出し確認。"""

from unittest.mock import MagicMock, patch

from src.common.notify import send_alert_email


def test_send_alert_email_skips_without_config():
    assert send_alert_email("subject", "body") is False


def test_send_alert_email_sends_via_smtp(monkeypatch):
    monkeypatch.setenv("GMAIL_SENDER_ADDRESS", "bot@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("ALERT_EMAIL_TO", "you@example.com")

    smtp_instance = MagicMock()
    smtp_instance.__enter__.return_value = smtp_instance
    with patch("smtplib.SMTP_SSL", return_value=smtp_instance) as smtp_cls:
        ok = send_alert_email("件名", "本文")

    assert ok is True
    smtp_cls.assert_called_once()
    smtp_instance.login.assert_called_once_with("bot@example.com", "app-password")
    smtp_instance.sendmail.assert_called_once()
    args = smtp_instance.sendmail.call_args[0]
    assert args[0] == "bot@example.com"
    assert args[1] == ["you@example.com"]


def test_send_alert_email_returns_false_on_smtp_failure(monkeypatch):
    monkeypatch.setenv("GMAIL_SENDER_ADDRESS", "bot@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("ALERT_EMAIL_TO", "you@example.com")

    with patch("smtplib.SMTP_SSL", side_effect=OSError("network down")):
        ok = send_alert_email("件名", "本文")

    assert ok is False
