"""Служебное SMTP-уведомление об истёкшей авторизации СБИС."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from src.config import SBIS_AUTH_ALERT_EMAILS
from src.mailing.smtp_provider import SMTPMailProvider
from src.sbis.auth import SbisAuthCheckResult


AUTH_ALERT_SUBJECT = (
    "ProjectSbis: требуется обновить авторизацию СБИС"
)
COOKIE_RECOVERY_COMMAND = (
    "sudo /opt/projectsbis/repository/.venv/bin/python "
    "-m src.sbis.cookie_manager"
)


@dataclass(slots=True)
class SbisAuthAlertMessage:
    """Минимальное сообщение, совместимое с SMTPMailProvider."""

    recipient_id: int
    to_email: str
    subject: str
    text_body: str
    html_body: str
    attachments: None = None
    include_logo: bool = False


def get_sbis_auth_alert_recipients() -> list[str]:
    """Разобрать отдельный список адресов без fallback на daily report."""
    raw_value = str(
        SBIS_AUTH_ALERT_EMAILS or ""
    ).strip()
    return [
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    ]


def _format_http_status(result: SbisAuthCheckResult) -> str:
    """Сформировать безопасное представление HTTP-статуса."""
    if result.http_status is None:
        return "не получен"
    return str(result.http_status)


def build_sbis_auth_alert_bodies(
    result: SbisAuthCheckResult,
) -> tuple[str, str]:
    """Сформировать text/HTML без cookie и других секретов."""
    checked_at = result.checked_at.isoformat(
        timespec="seconds",
    )
    http_status = _format_http_status(result)

    text_body = "\n".join(
        (
            "Требуется обновить авторизацию СБИС.",
            "",
            f"Время проверки: {checked_at}",
            f"HTTP status: {http_status}",
            "Daily run остановлен.",
            "Рассылка не выполнялась.",
            "",
            "Команда восстановления:",
            COOKIE_RECOVERY_COMMAND,
        )
    )

    html_body = f"""
    <!doctype html>
    <html lang="ru">
    <body style="font-family:Arial,Helvetica,sans-serif;color:#1f2937;">
        <h2>Требуется обновить авторизацию СБИС</h2>
        <p>
            <strong>Время проверки:</strong> {escape(checked_at)}<br>
            <strong>HTTP status:</strong> {escape(http_status)}
        </p>
        <p>
            Daily run остановлен.<br>
            Рассылка не выполнялась.
        </p>
        <p><strong>Команда восстановления:</strong></p>
        <pre style="padding:12px;background:#f3f4f6;white-space:pre-wrap;">{escape(COOKIE_RECOVERY_COMMAND)}</pre>
    </body>
    </html>
    """.strip()
    return text_body, html_body


async def send_sbis_auth_alert(
    result: SbisAuthCheckResult,
) -> None:
    """
    Отправить auth-alert только специальным получателям.

    Отсутствие адресов считается корректной конфигурацией без отправки.
    SMTP-ошибки поднимаются вызывающему коду, который обязан сохранить
    исходный результат auth-preflight.
    """
    recipients = get_sbis_auth_alert_recipients()
    if not recipients:
        print(
            "SBIS_AUTH_ALERT_EMAILS не настроен: "
            "служебное auth-уведомление не отправлено."
        )
        return

    text_body, html_body = build_sbis_auth_alert_bodies(
        result,
    )
    provider = SMTPMailProvider.from_env()
    failures: list[str] = []

    for email in recipients:
        message = SbisAuthAlertMessage(
            recipient_id=0,
            to_email=email,
            subject=AUTH_ALERT_SUBJECT,
            text_body=text_body,
            html_body=html_body,
        )
        send_result = await provider.send(message)
        if send_result.success:
            print(f"Auth-alert {email}: OK")
            continue

        print(f"Auth-alert {email}: ERROR")
        failures.append(email)

    if failures:
        raise RuntimeError(
            "Не удалось отправить auth-alert: "
            f"ошибок {len(failures)}"
        )
