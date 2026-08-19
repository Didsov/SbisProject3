"""
SMTP-провайдер для реальной отправки писем через собственный Postfix.

Назначение модуля:
- прочитать SMTP-настройки из .env;
- подключиться к SMTP Submission-серверу;
- включить STARTTLS;
- выполнить SMTP AUTH;
- сформировать корректное MIME-письмо в UTF-8;
- отправить текстовую и HTML-версии письма;
- вернуть результат отправки вызывающему коду.

Текущая инфраструктура:
- SMTP host: mail.projectsbis.ru;
- SMTP port: 587;
- шифрование: STARTTLS;
- авторизация: SMTP AUTH;
- отправитель: info@projectsbis.ru.

Модуль намеренно не работает с базой данных.
Он отвечает только за SMTP-доставку одного письма.

Блокирующая библиотека smtplib запускается через asyncio.to_thread(),
чтобы SMTP-соединение не блокировало основной asyncio event loop.
"""

from __future__ import annotations

from pathlib import Path

import asyncio
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol
from src.config import (
    MAIL_FROM_EMAIL,
    MAIL_FROM_NAME,
    MAIL_SMTP_HOST,
    MAIL_SMTP_PASSWORD,
    MAIL_SMTP_PORT,
    MAIL_SMTP_USERNAME,
)

LOGO_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "atlantis_email_logo.png"
)

LOGO_CID = "atlantis-logo"


class MailMessageLike(Protocol):
    """
    Минимальный интерфейс сообщения, который нужен SMTP-провайдеру.

    Такой Protocol позволяет использовать существующий MailMessage
    из sender.py без прямого импорта sender.py и без циклических импортов.
    """

    recipient_id: int
    to_email: str
    subject: str
    text_body: str
    html_body: str


@dataclass(slots=True)
class SMTPSendResult:
    """
    Результат попытки SMTP-отправки.

    Поля:
        success:
            True, если SMTP-сервер принял письмо без исключения.

        provider_message_id:
            Значение заголовка Message-ID созданного письма.

            У собственного Postfix нет provider_message_id в том же
            смысле, как у API-провайдеров, поэтому используем стандартный
            Message-ID самого письма.

        error:
            Текст ошибки при неуспешной отправке.
    """

    success: bool
    provider_message_id: str | None
    error: str | None = None


class SMTPMailProvider:
    """
    Реальный SMTP-провайдер для собственного почтового сервера.

    Провайдер:
    1. подключается к SMTP-серверу;
    2. выполняет EHLO;
    3. включает STARTTLS;
    4. повторно выполняет EHLO;
    5. авторизуется;
    6. передаёт письмо серверу;
    7. закрывает соединение.

    SMTP-настройки передаются в конструктор.
    Для загрузки их из .env используется from_env().
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_email: str,
        from_name: str,
        timeout: float = 30.0,
    ) -> None:
        """
        Создать SMTP-провайдер.

        Аргументы:
            host:
                SMTP hostname, например mail.projectsbis.ru.

            port:
                Submission-порт. В нашей конфигурации — 587.

            username:
                Пользователь SMTP AUTH.

            password:
                Пароль SMTP AUTH.

            from_email:
                Email, который будет использоваться как From.

            from_name:
                Отображаемое имя отправителя.

            timeout:
                Максимальное время сетевой операции в секундах.
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.from_name = from_name
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "SMTPMailProvider":
        """
        Создать SMTPMailProvider из переменных окружения.

        Ожидаются:
        - MAIL_SMTP_HOST;
        - MAIL_SMTP_PORT;
        - MAIL_SMTP_USERNAME;
        - MAIL_SMTP_PASSWORD;
        - MAIL_FROM_EMAIL;
        - MAIL_FROM_NAME.

        Возвращает:
            Настроенный SMTPMailProvider.

        Исключения:
            ValueError:
                Если обязательная настройка отсутствует
                или MAIL_SMTP_PORT не является числом.
        """

        host = (MAIL_SMTP_HOST or "").strip()
        port_raw = MAIL_SMTP_PORT
        username = (MAIL_SMTP_USERNAME or "").strip()
        password = MAIL_SMTP_PASSWORD or ""
        from_email = (MAIL_FROM_EMAIL or "").strip()
        from_name = (MAIL_FROM_NAME or "").strip()

        required_values = {
            "MAIL_SMTP_HOST": host,
            "MAIL_SMTP_PORT": port_raw,
            "MAIL_SMTP_USERNAME": username,
            "MAIL_SMTP_PASSWORD": password,
            "MAIL_FROM_EMAIL": from_email,
            "MAIL_FROM_NAME": from_name,
        }

        missing = [
            name
            for name, value in required_values.items()
            if not value
        ]

        if missing:
            raise ValueError(
                "Не заполнены SMTP-настройки в .env: "
                + ", ".join(missing)
            )

        try:
            port = int(port_raw)
        except ValueError as error:
            raise ValueError(
                "MAIL_SMTP_PORT должен быть целым числом"
            ) from error

        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            from_email=from_email,
            from_name=from_name,
        )

    async def send(
        self,
        message: MailMessageLike,
    ) -> SMTPSendResult:
        """
        Асинхронно отправить одно письмо.

        Фактический smtplib является блокирующим, поэтому работа SMTP
        выполняется в отдельном потоке через asyncio.to_thread().

        Аргументы:
            message:
                Объект письма с адресом получателя, темой,
                text_body и html_body.

        Возвращает:
            SMTPSendResult.
        """
        return await asyncio.to_thread(
            self._send_sync,
            message,
        )

    def _send_sync(
        self,
        message: MailMessageLike,
    ) -> SMTPSendResult:
        """
        Выполнить фактическую синхронную SMTP-отправку.

        Возвращает:
            SMTPSendResult с Message-ID при успехе
            либо описанием ошибки при неудаче.
        """
        email_message = EmailMessage()

        message_id = make_msgid(
            domain="projectsbis.ru"
        )

        email_message["Message-ID"] = message_id
        email_message["From"] = formataddr(
            (
                self.from_name,
                self.from_email,
            )
        )
        email_message["To"] = message.to_email
        email_message["Subject"] = message.subject

        # EmailMessage самостоятельно формирует правильные
        # MIME-заголовки и кодировку UTF-8.
        email_message.set_content(
            message.text_body
        )

        email_message.add_alternative(
            message.html_body,
            subtype="html",
        )
        if not LOGO_PATH.exists():
            return SMTPSendResult(
                success=False,
                provider_message_id=None,
                error=(
                    "Не найден логотип для email: "
                    f"{LOGO_PATH}"
                ),
            )

        logo_bytes = LOGO_PATH.read_bytes()

        html_part = email_message.get_payload()[-1]

        html_part.add_related(
            logo_bytes,
            maintype="image",
            subtype="png",
            cid=f"<{LOGO_CID}>",
            filename="atlantis-logo.png",
            disposition="inline",
        )

        tls_context = ssl.create_default_context()

        try:
            with smtplib.SMTP(
                host=self.host,
                port=self.port,
                timeout=self.timeout,
            ) as smtp:
                smtp.ehlo()

                smtp.starttls(
                    context=tls_context
                )

                # После STARTTLS SMTP-возможности нужно получить заново.
                smtp.ehlo()

                smtp.login(
                    self.username,
                    self.password,
                )

                smtp.send_message(
                    email_message,
                    from_addr=self.from_email,
                    to_addrs=[message.to_email],
                )

        except (
            smtplib.SMTPException,
            OSError,
            ssl.SSLError,
        ) as error:
            return SMTPSendResult(
                success=False,
                provider_message_id=None,
                error=str(error),
            )

        return SMTPSendResult(
            success=True,
            provider_message_id=message_id,
        )