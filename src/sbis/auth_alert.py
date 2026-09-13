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
    "cd /opt/projectsbis/repository\n"
    "sudo .venv/bin/python -m src.sbis.cookie_manager"
)
COOKIE_CHECK_COMMAND = (
    "cd /opt/projectsbis/repository\n"
    ".venv/bin/python -m src.sbis.cookie_manager --check"
)
COOKIE_ACQUISITION_STEPS = (
    "Авторизуйтесь в https://online.sbis.ru в браузере.",
    "Откройте DevTools (F12), вкладку Network, и обновите страницу.",
    "Откройте запрос к online.sbis.ru/service/ и раздел Request Headers.",
    "Скопируйте целиком значение заголовка Cookie, без префикса «Cookie:».",
)
COOKIE_RECOVERY_NOTES = (
    "Команда запускает интерактивное восстановление cookie.",
    "Cookie вводится скрыто через getpass и не отображается в терминале.",
    "Новая cookie сначала проверяется запросом к СБИС.",
    "Если проверка не пройдена, env-файл не изменяется.",
    "Если cookie валидна, обновляется SBIS_BROWSER_COOKIE, создаётся backup "
    "env-файла, а auth-state переводится в HEALTHY.",
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
            "Автоматический daily run остановлен.",
            "Рассылка не выполнялась и не должна выполняться до восстановления.",
            "",
            "Как получить актуальную cookie:",
            *(f"{index}. {step}" for index, step in enumerate(
                COOKIE_ACQUISITION_STEPS,
                1,
            )),
            "Не используйте document.cookie: в нём могут отсутствовать HttpOnly cookie.",
            "Не отправляйте значение cookie по почте или в мессенджерах.",
            "",
            "Команда восстановления:",
            COOKIE_RECOVERY_COMMAND,
            "",
            *COOKIE_RECOVERY_NOTES,
            "",
            "Только проверить текущую cookie:",
            COOKIE_CHECK_COMMAND,
        )
    )

    acquisition_steps = "".join(
        f"<li>{escape(step)}</li>"
        for step in COOKIE_ACQUISITION_STEPS
    )
    recovery_notes = "".join(
        f"<li>{escape(note)}</li>"
        for note in COOKIE_RECOVERY_NOTES
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
            Автоматический daily run остановлен.<br>
            Рассылка не выполнялась и не должна выполняться до восстановления.
        </p>
        <p><strong>Как получить актуальную cookie:</strong></p>
        <ol>{acquisition_steps}</ol>
        <p>
            Не используйте <code>document.cookie</code>: в нём могут отсутствовать
            HttpOnly cookie. Не отправляйте значение cookie по почте или в мессенджерах.
        </p>
        <p><strong>Команда восстановления:</strong></p>
        <pre style="padding:12px;background:#f3f4f6;white-space:pre-wrap;">{escape(COOKIE_RECOVERY_COMMAND)}</pre>
        <ul>{recovery_notes}</ul>
        <p><strong>Только проверить текущую cookie:</strong></p>
        <pre style="padding:12px;background:#f3f4f6;white-space:pre-wrap;">{escape(COOKIE_CHECK_COMMAND)}</pre>
    </body>
    </html>
    """.strip()
    return text_body, html_body


async def send_sbis_auth_alert(
    result: SbisAuthCheckResult,
) -> bool:
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
        return False

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
    return True
