"""
Тестовый модуль отправки почтовой кампании.

Назначение модуля:
- получить pending-получателей почтовой кампании;
- сформировать письмо для каждого получателя;
- показать тему, адрес получателя и содержимое письма;
- имитировать успешную отправку через MockMailProvider;
- ничего реально не отправлять во внешние почтовые сервисы;
- не изменять статусы получателей в базе данных.

Текущий модуль используется для безопасной отладки
рассылочного сценария до подключения реального провайдера.

Пример запуска:

    python -m src.mailing.sender \
        --campaign-id 1 \
        --limit 1 \
        --dry-run

Функции и классы:
- MailMessage — модель сформированного письма;
- MailSendResult — результат попытки отправки;
- MockMailProvider — тестовый почтовый провайдер;
- parse_arguments() — разобрать CLI-параметры;
- build_mail_message() — сформировать письмо;
- run_sender() — обработать очередь получателей;
- main() — точка входа CLI.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import uuid4

from src.database import (
    confirm_mail_send,
    get_mail_campaign,
    get_pending_mail_recipients,
)
from src.mailing.templates.registry import get_mail_template
@dataclass(slots=True)
class MailMessage:
    """
    Описание письма, готового к отправке.

    Поля:
        recipient_id:
            ID получателя из mail_recipients.

        to_email:
            Email получателя.

        subject:
            Тема письма.

        text_body:
            Текстовая версия письма.

        html_body:
            HTML-версия письма.
    """

    recipient_id: int
    to_email: str
    subject: str
    text_body: str
    html_body: str


@dataclass(slots=True)
class MailSendResult:
    """
    Результат отправки письма почтовым провайдером.

    Поля:
        success:
            True, если провайдер принял письмо.

        provider_message_id:
            Идентификатор сообщения у провайдера.

        error:
            Текст ошибки при неуспешной отправке.
    """

    success: bool
    provider_message_id: str | None
    error: str | None = None


class MockMailProvider:
    """
    Тестовый почтовый провайдер.

    Провайдер не выполняет сетевых запросов и не отправляет
    реальных писем.

    Метод send() имитирует успешную отправку и возвращает
    фиктивный provider_message_id.

    Используется для проверки:
    - формирования писем;
    - очереди получателей;
    - будущей логики интеграции с реальным провайдером.
    """

    async def send(
        self,
        message: MailMessage,
    ) -> MailSendResult:
        """
        Имитировать успешную отправку письма.

        Аргументы:
            message:
                Сформированное письмо.

        Возвращает:
            MailSendResult с success=True и фиктивным
            идентификатором сообщения.
        """
        provider_message_id = (
            f"mock-{uuid4()}"
        )

        return MailSendResult(
            success=True,
            provider_message_id=provider_message_id,
        )


def parse_arguments() -> argparse.Namespace:
    """
    Разобрать параметры запуска тестового sender.

    Поддерживаемые параметры:
    - --campaign-id — ID кампании;
    - --limit — максимальное количество получателей;
    - --dry-run — обязательный безопасный режим.

    Возвращает:
        argparse.Namespace с параметрами запуска.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Тестово сформировать письма "
            "для pending-получателей кампании."
        )
    )

    parser.add_argument(
        "--campaign-id",
        type=int,
        required=True,
        help="ID кампании из mail_campaigns.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help=(
            "Максимальное количество получателей "
            "для тестового запуска."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Только показать сформированные письма. "
            "База данных не изменяется."
        ),
    )

    parser.add_argument(
        "--mock-send",
        action="store_true",
        help=(
            "Имитировать отправку через MockMailProvider "
            "и сохранить результат в базе данных."
        ),
    )

    arguments = parser.parse_args()

    if arguments.campaign_id < 1:
        parser.error(
            "--campaign-id должен быть больше 0"
        )

    if arguments.limit < 1:
        parser.error(
            "--limit должен быть больше 0"
        )

    if arguments.dry_run == arguments.mock_send:
        parser.error(
            "Нужно указать ровно один режим: "
            "--dry-run или --mock-send"
        )

    return arguments


def build_mail_message(
    recipient: dict[str, object],
    template,
) -> MailMessage:
    """
    Сформировать письмо для одного получателя.

    Пока используется временный тестовый шаблон.
    Он нужен только для проверки архитектуры sender.

    Аргументы:
        recipient:
            Получатель из get_pending_mail_recipients().

            Ожидаются поля:
            - recipient_id;
            - name;
            - inn;
            - email.

    Возвращает:
        Готовый MailMessage.

    Исключения:
        ValueError:
            Если отсутствует корректный recipient_id
            или email.
    """
    recipient_id = recipient.get(
        "recipient_id"
    )

    if not isinstance(recipient_id, int):
        raise ValueError(
            "Получатель не содержит корректный recipient_id"
        )

    email = recipient.get(
        "email"
    )

    if not isinstance(email, str) or not email.strip():
        raise ValueError(
            "Получатель не содержит корректный email"
        )

    email = email.strip()

    client_name = recipient.get(
        "name"
    )

    if not isinstance(client_name, str) or not client_name.strip():
        client_name = "организация"
    else:
        client_name = client_name.strip()

    inn = recipient.get(
        "inn"
    )

    if not isinstance(inn, str):
        inn = ""

    subject = template.build_subject(
        client_name=client_name,
        inn=inn,
    )

    text_body = template.build_text_body(
        client_name=client_name,
        inn=inn,
    )

    html_body = template.build_html_body(
        client_name=client_name,
        inn=inn,
    ) 

    return MailMessage(
        recipient_id=recipient_id,
        to_email=email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


async def run_sender(
    campaign_id: int,
    limit: int,
    *,
    dry_run: bool,
    mock_send: bool,
) -> None:
    """
    Выполнить тестовый проход по очереди кампании.

    Что делает:
    - получает pending-получателей;
    - формирует для каждого письмо;
    - выводит письмо в консоль;
    - передаёт его MockMailProvider;
    - показывает фиктивный результат отправки;
    - не изменяет базу данных.

    Аргументы:
        campaign_id:
            ID кампании.

        limit:
            Максимальное количество получателей
            текущего тестового запуска.

    Возвращает:
        None.
    """
    campaign = get_mail_campaign(
        campaign_id
    )

    template_name = campaign.get(
        "template_name"
    )

    if not isinstance(template_name, str) or not template_name.strip():
        raise ValueError(
            f"У кампании #{campaign_id} не указан template_name"
        )

    template = get_mail_template(
        template_name
    )


    recipients = get_pending_mail_recipients(
        campaign_id=campaign_id,
        limit=limit,
    )

    if not recipients:
        print(
            "Pending-получателей нет."
        )
        return

    provider = MockMailProvider()

    print(
        f"Тестовая обработка кампании #{campaign_id}"
    )

    print(
        f"Получателей в текущем запуске: {len(recipients)}"
    )

    for index, recipient in enumerate(
        recipients,
        start=1,
    ):
        message = build_mail_message(
            recipient,
            template,
        )

        print()
        print(
            f"[{index}/{len(recipients)}]"
        )

        print(
            f"Получатель: {message.to_email}"
        )

        print(
            f"Тема: {message.subject}"
        )

        print()
        print(
            "--- TEXT ---"
        )

        print(
            message.text_body
        )

        print()
        print(
            "--- HTML ---"
        )

        print(
            message.html_body
        )

        if dry_run:
            print()
            print(
                "--- DRY RUN ---"
            )

            print(
                "Письмо не отправлено."
            )

 
            continue

        result = await provider.send(
            message
        )
        message_id = confirm_mail_send(
            recipient_id=message.recipient_id,
            provider="mock",
            provider_message_id=result.provider_message_id,
            success=result.success,
        )

        print()
        print(
            "--- MOCK RESULT ---"
        )

        print(
            f"success: {result.success}"
        )

        print(
            "provider_message_id: "
            f"{result.provider_message_id}"
        )
        print(
            f"mail_messages.id: {message_id}"
        )

    print()

    if dry_run:
        print(
            "Dry-run завершён."
        )

        print(
            "База данных не изменена."
        )

        print(
            "Реальные письма не отправлялись."
        )

    elif mock_send:
        print(
            "Mock-run завершён."
        )

        print(
            "Результаты сохранены в базе данных."
        )

        print(
            "Реальные письма не отправлялись."
        )

def main() -> None:
    """
    Запустить тестовый sender из командной строки.

    Возвращает:
        None.
    """
    arguments = parse_arguments()

    import asyncio

    asyncio.run(
        run_sender(
            campaign_id=arguments.campaign_id,
            limit=arguments.limit,
            dry_run=arguments.dry_run,
            mock_send=arguments.mock_send,
        )
    )


if __name__ == "__main__":
    main()