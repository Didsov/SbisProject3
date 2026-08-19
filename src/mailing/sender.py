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
from src.config import TEST_MAIL_EMAIL

from src.mailing.smtp_provider import SMTPMailProvider

from src.database import (
    complete_mail_message,
    create_mail_message,
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
    parser.add_argument(
        "--smtp-send",
        action="store_true",
        help=(
            "Реально отправить письма через SMTP "
            "и сохранить результат в базе данных."
        ),
    )
    parser.add_argument(
        "--confirm-real-send",
        action="store_true",
        help=(
            "Явно подтвердить реальную SMTP-отправку "
            "на адреса клиентов кампании."
        ),
    )
    parser.add_argument(
        "--test-email",
        type=str,
        default=None,
        help=(
            "Тестовые email через запятую. "
            "При использовании с --smtp-send письма "
            "отправляются только на эти адреса, "
            "а не реальным получателям кампании."
        ),
    )
    parser.add_argument(
        "--tracking-test",
        action="store_true",
        help=(
            "Отправить полноценное тестовое письмо "
            "с tracking_token на TEST_MAIL_EMAIL. "
            "Статус реального получателя не изменяется."
        ),
    )

    arguments = parser.parse_args()
    if (
        arguments.smtp_send
        and not arguments.test_email
        and not arguments.confirm_real_send
    ):
        parser.error(
            "Реальная SMTP-отправка клиентам заблокирована. "
            "Добавьте --confirm-real-send "
            "или используйте --test-email."
        )

    if arguments.campaign_id < 1:
        parser.error(
            "--campaign-id должен быть больше 0"
        )

    if arguments.limit < 1:
        parser.error(
            "--limit должен быть больше 0"
        )

    selected_modes = sum(
        [
            arguments.dry_run,
            arguments.mock_send,
            arguments.smtp_send,
            arguments.tracking_test,
        ]
    )

    if selected_modes != 1:
        parser.error(
            "Нужно указать ровно один режим: "
            "--dry-run, --mock-send, --smtp-send "
            "или --tracking-test"
        )

    return arguments


def build_mail_message(
    recipient: dict,
    template,
    *,
    tracking_token: str | None = None,
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


    director_first_name = recipient.get(
        "director_first_name"
    )

    if not isinstance(director_first_name, str):
        director_first_name = ""
    else:
        director_first_name = director_first_name.strip()


    director_middle_name = recipient.get(
        "director_middle_name"
    )

    if not isinstance(director_middle_name, str):
        director_middle_name = ""
    else:
        director_middle_name = director_middle_name.strip()


    subject = template.build_subject(
        director_first_name=director_first_name,
        director_middle_name=director_middle_name,
        client_name=client_name,
        inn=inn,
        tracking_token=tracking_token,
    )

    text_body = template.build_text_body(
        director_first_name=director_first_name,
        director_middle_name=director_middle_name,
        client_name=client_name,
        inn=inn,
        tracking_token=tracking_token,
    )

    html_body = template.build_html_body(
        director_first_name=director_first_name,
        director_middle_name=director_middle_name,
        client_name=client_name,
        inn=inn,
        tracking_token=tracking_token,
    ) 

    return MailMessage(
        recipient_id=recipient_id,
        to_email=email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )


def parse_test_emails(
    raw_value: str | None,
) -> list[str]:
    """
    Разобрать строку тестовых email-адресов из CLI.

    Формат:
        email1@example.com,email2@example.com

    Аргументы:
        raw_value:
            Исходное значение --test-email.
            Может быть None.

    Возвращает:
        Список очищенных email-адресов.

    Примечание:
        На этом этапе выполняется только простая очистка:
        - разделение по запятой;
        - strip();
        - удаление пустых значений;
        - удаление дублей с сохранением порядка.
    """
    if not raw_value:
        return []

    emails: list[str] = []

    for item in raw_value.split(","):
        email = item.strip()

        if not email:
            continue

        if email not in emails:
            emails.append(email)

    return emails


async def run_sender(
    campaign_id: int,
    limit: int,
    *,
    dry_run: bool,
    mock_send: bool,
    smtp_send: bool,
    test_emails: list[str],
    tracking_test: bool,
) -> None:
    """
    Обработать очередь получателей почтовой кампании.

    Поддерживаемые режимы:
    - dry-run:
      формирует и показывает письмо,
      но не отправляет его и не изменяет БД;

    - mock-send:
      создаёт mail_messages до отправки,
      генерирует tracking_token,
      имитирует отправку через MockMailProvider,
      после чего фиксирует результат;

    - smtp-send + test-email:
      реально отправляет письмо только на тестовые адреса,
      но не создаёт mail_messages
      и не изменяет статус настоящего получателя;

    - smtp-send:
      создаёт mail_messages до отправки,
      генерирует tracking_token,
      реально отправляет письмо через SMTP
      и фиксирует результат в БД.

    Важный порядок для учётной отправки:

        create_mail_message()
        -> tracking_token
        -> build_mail_message()
        -> provider.send()
        -> complete_mail_message()

    Благодаря этому tracking_token существует
    ещё до формирования HTML-письма.

    Аргументы:
        campaign_id:
            ID кампании из mail_campaigns.

        limit:
            Максимальное количество получателей
            в текущем запуске.

        dry_run:
            Безопасно сформировать письмо
            без отправки и изменения БД.

        mock_send:
            Имитировать отправку через MockMailProvider.

        smtp_send:
            Использовать реальный SMTP-провайдер.

        test_emails:
            Тестовые адреса для безопасной SMTP-отправки.
            При наличии этих адресов настоящий получатель
            и его состояние в БД не изменяются.

    Возвращает:
        None.
    """
    if tracking_test and not TEST_MAIL_EMAIL:
        raise RuntimeError(
            "Для --tracking-test не задан TEST_MAIL_EMAIL"
        )
    if tracking_test:
        print(
            f"Tracking-test будет отправлен на: "
            f"{TEST_MAIL_EMAIL}"
        )

    
    campaign = get_mail_campaign(
        campaign_id
    )

    template_name = campaign.get(
        "template_name"
    )

    if (
        not isinstance(template_name, str)
        or not template_name.strip()
    ):
        raise ValueError(
            f"У кампании #{campaign_id} "
            "не указан template_name"
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

    if smtp_send or tracking_test:
        provider = SMTPMailProvider.from_env()
    else:
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
        # ---------------------------------------------------------
        # DRY-RUN И TEST SMTP
        #
        # В этих режимах mail_messages создавать нельзя,
        # поэтому tracking_token отсутствует.
        # ---------------------------------------------------------
        if dry_run or (
            smtp_send
            and test_emails
        ):
            message = build_mail_message(
                recipient,
                template,
            )

            message_id = None

        # ---------------------------------------------------------
        # MOCK / REAL SMTP
        #
        # Сначала создаём mail_messages и tracking_token,
        # и только после этого формируем письмо.
        # ---------------------------------------------------------
        else:
            provider_name = (
                "smtp"
                if smtp_send or tracking_test
                else "mock"
            )

            mail_message_record = create_mail_message(
                recipient_id=recipient["recipient_id"],
                provider=provider_name,
                is_test=tracking_test,
            )

            message_id = mail_message_record[
                "message_id"
            ]

            tracking_token = mail_message_record[
                "tracking_token"
            ]

            message = build_mail_message(
                recipient,
                template,
                tracking_token=tracking_token,
            )
            if tracking_test:
                message.to_email = TEST_MAIL_EMAIL




        if tracking_test:
            message.to_email = TEST_MAIL_EMAIL
            message.subject = f"[TRACKING TEST] {message.subject}"

        # ---------------------------------------------------------
        # ОБЩИЙ ВЫВОД СФОРМИРОВАННОГО ПИСЬМА
        # ---------------------------------------------------------
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

        # ---------------------------------------------------------
        # DRY-RUN
        # ---------------------------------------------------------
        if dry_run:
            print()
            print(
                "--- DRY RUN ---"
            )

            print(
                "Письмо не отправлено."
            )

            continue

        # ---------------------------------------------------------
        # БЕЗОПАСНЫЙ SMTP TEST
        #
        # Реальное письмо отправляется только
        # на явно указанные тестовые адреса.
        #
        # mail_messages и mail_recipients не изменяются.
        # ---------------------------------------------------------
        if smtp_send and test_emails:
            print()
            print(
                "ТЕСТОВЫЙ SMTP-РЕЖИМ:"
            )

            print(
                "Реальный адрес клиента не используется."
            )

            print(
                f"Исходный получатель: {message.to_email}"
            )

            print(
                "Тестовые получатели: "
                + ", ".join(test_emails)
            )

            for test_email in test_emails:
                test_message = MailMessage(
                    recipient_id=message.recipient_id,
                    to_email=test_email,
                    subject=f"[TEST] {message.subject}",
                    text_body=message.text_body,
                    html_body=message.html_body,
                )

                result = await provider.send(
                    test_message
                )

                print()
                print(
                    "--- SMTP TEST RESULT ---"
                )

                print(
                    f"test_email: {test_email}"
                )

                print(
                    f"success: {result.success}"
                )

                print(
                    "provider_message_id: "
                    f"{result.provider_message_id}"
                )

                if result.error:
                    print(
                        f"error: {result.error}"
                    )

            continue

        # ---------------------------------------------------------
        # MOCK ИЛИ РЕАЛЬНАЯ SMTP-ОТПРАВКА
        #
        # Здесь message_id уже обязательно существует,
        # потому что mail_messages был создан выше.
        # ---------------------------------------------------------
        if message_id is None:
            raise RuntimeError(
                "mail_messages.id отсутствует "
                "перед учётной отправкой"
            )

        result = await provider.send(
            message
        )

        complete_mail_message(
            message_id=message_id,
            provider_message_id=result.provider_message_id,
            success=result.success,
            error=result.error,
        )

        print()

        result_title = (
            "--- SMTP RESULT ---"
            if smtp_send or tracking_test
            else "--- MOCK RESULT ---"
        )

        print(
            result_title
        )

        print(
            f"success: {result.success}"
        )

        print(
            "provider_message_id: "
            f"{result.provider_message_id}"
        )

        if result.error:
            print(
                f"error: {result.error}"
            )

        print(
            f"mail_messages.id: {message_id}"
        )

    print()

    # -------------------------------------------------------------
    # ИТОГ ЗАПУСКА
    # -------------------------------------------------------------
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

    elif smtp_send and test_emails:
        print(
            "Тестовый SMTP-run завершён."
        )

        print(
            "Письма отправлены только на тестовые адреса."
        )

        print(
            "База данных не изменена."
        )

        print(
            "Статусы реальных получателей не изменены."
        )
    elif tracking_test:
        print(
            "Tracking-test завершён."
        )

        print(
            f"Письмо отправлено на: {TEST_MAIL_EMAIL}"
        )

        print(
            "Тестовая попытка сохранена в mail_messages "
            "с is_test=1."
        )

        print(
            "Статус реального получателя не изменён."
        )

    elif smtp_send:
        print(
            "SMTP-run завершён."
        )

        print(
            "Результаты сохранены в базе данных."
        )

        print(
            "Письма реально отправлялись через SMTP."
        )



def main() -> None:
    """
    Запустить тестовый sender из командной строки.

    Возвращает:
        None.
    """
    arguments = parse_arguments()
    test_emails = parse_test_emails(
        arguments.test_email
    )
   

    import asyncio

    asyncio.run(
        run_sender(
            campaign_id=arguments.campaign_id,
            limit=arguments.limit,
            dry_run=arguments.dry_run,
            mock_send=arguments.mock_send,
            smtp_send=arguments.smtp_send,
            test_emails=test_emails,
            tracking_test=arguments.tracking_test,
        )
    )


if __name__ == "__main__":
    main()