"""
Единый ежедневный сценарий ProjectSbis.

Production workflow:

    СБИС selection
        ↓
    загрузка клиентов
        ↓
    ContractorCard.Read
        ↓
    дневная mail campaign
        ↓
    mail_recipients
        ↓
    SMTP
        ↓
    Postfix delivery sync
        ↓
    delivered / bounced / deferred
        ↓
    daily report
        ↓
    HTML + XLSX → DAILY_REPORT_EMAILS

Production:

    python -m src.daily_run \
        --selection 5984 \
        --send \
        --confirm-real-send

Тест на уже подготовленной локальной выборке:

    python -m src.daily_run \
        --selection 990001 \
        --skip-load \
        --send \
        --confirm-real-send
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from src.client_loader import run as run_client_loader
from src.database import (
    get_connection,
    get_or_create_mail_campaign,
    populate_mail_recipients,
)
from src.mailing.daily_report import send_daily_report
from src.mailing.postfix_delivery import (
    synchronize_delivery_statuses,
)
from src.mailing.sender import run_sender


DEFAULT_SELECTION_ID = 5984

DEFAULT_MAIL_LOG = Path(
    "/var/log/mail.log"
)

DEFAULT_SEND_LIMIT = 1000

DEFAULT_DELIVERY_WAIT_SECONDS = 60
DEFAULT_DELIVERY_POLL_SECONDS = 5


def parse_arguments() -> argparse.Namespace:
    """
    Разобрать параметры daily-run.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Выполнить полный ежедневный ProjectSbis workflow."
        )
    )

    parser.add_argument(
        "--selection",
        type=int,
        default=DEFAULT_SELECTION_ID,
        help=(
            "Номер выборки СБИС. "
            "По умолчанию: 5984."
        ),
    )

    parser.add_argument(
        "--skip-load",
        action="store_true",
        help=(
            "Не загружать выборку из СБИС. "
            "Использовать уже имеющиеся данные в БД. "
            "Предназначено прежде всего для тестов."
        ),
    )

    parser.add_argument(
        "--send",
        action="store_true",
        help="Выполнить реальную SMTP-рассылку.",
    )

    parser.add_argument(
        "--confirm-real-send",
        action="store_true",
        help=(
            "Явное подтверждение реальной SMTP-рассылки."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SEND_LIMIT,
        help=(
            "Максимальное количество писем "
            "за текущий запуск."
        ),
    )

    parser.add_argument(
        "--delivery-wait",
        type=int,
        default=DEFAULT_DELIVERY_WAIT_SECONDS,
        help=(
            "Сколько секунд ждать результатов Postfix "
            "перед формированием отчёта."
        ),
    )

    parser.add_argument(
        "--mail-log",
        type=Path,
        default=DEFAULT_MAIL_LOG,
        help="Путь к журналу Postfix.",
    )

    arguments = parser.parse_args()

    if arguments.selection < 1:
        parser.error(
            "--selection должен быть больше 0"
        )

    if arguments.limit < 1:
        parser.error(
            "--limit должен быть больше 0"
        )

    if arguments.delivery_wait < 0:
        parser.error(
            "--delivery-wait не может быть меньше 0"
        )

    if (
        arguments.send
        and not arguments.confirm_real_send
    ):
        parser.error(
            "Для реальной отправки дополнительно укажите "
            "--confirm-real-send"
        )

    return arguments


def build_campaign_name(
    selection_id: int,
) -> str:
    """
    Сформировать уникальное имя дневной кампании.
    """
    today = date.today().isoformat()

    return (
        f"new_companies_{today}"
        f"_selection_{selection_id}"
    )


def ensure_campaign_template(
    campaign_id: int,
) -> None:
    """
    Установить шаблон new_companies для дневной кампании.

    get_or_create_mail_campaign() сейчас принимает
    только name и selection_id, поэтому template_name
    устанавливается отдельно.
    """
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE mail_campaigns
            SET template_name = 'new_companies'
            WHERE id = ?
            """,
            (campaign_id,),
        )


def count_unresolved_messages(
    campaign_id: int,
) -> int:
    """
    Посчитать боевые письма кампании,
    по которым ещё нет финального delivery-статуса.
    """
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS count
            FROM mail_messages AS mm

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            WHERE
                mr.campaign_id = ?
                AND mm.is_test = 0
                AND mm.status IN (
                    'sent',
                    'deferred'
                )
            """,
            (campaign_id,),
        ).fetchone()

    return int(
        row["count"]
    )


async def wait_for_delivery_results(
    *,
    campaign_id: int,
    log_path: Path,
    timeout_seconds: int,
) -> None:
    """
    Периодически синхронизировать Postfix до получения
    финальных статусов либо окончания timeout.

    Если часть писем остаётся deferred/sent,
    daily-report покажет для них многоточие.
    """
    elapsed = 0

    while True:
        print()
        print("Проверяю результаты доставки Postfix...")

        stats = synchronize_delivery_statuses(
            log_path=log_path,
            limit=None,
        )

        unresolved = count_unresolved_messages(
            campaign_id
        )

        print(
            "Delivery sync:"
            f" delivered={stats['delivered']},"
            f" bounced={stats['bounced']},"
            f" deferred={stats['deferred']},"
            f" unresolved={unresolved}"
        )

        if unresolved == 0:
            print(
                "Все письма получили финальный "
                "delivery-статус."
            )
            return

        if elapsed >= timeout_seconds:
            print(
                "Истекло время ожидания delivery-статусов."
            )
            return

        wait_seconds = min(
            DEFAULT_DELIVERY_POLL_SECONDS,
            timeout_seconds - elapsed,
        )

        if wait_seconds <= 0:
            return

        print(
            f"Повторная проверка через "
            f"{wait_seconds} сек."
        )

        await asyncio.sleep(
            wait_seconds
        )

        elapsed += wait_seconds


async def run_daily(
    *,
    selection_id: int,
    skip_load: bool,
    real_send: bool,
    limit: int,
    delivery_wait: int,
    mail_log: Path,
) -> None:
    """
    Выполнить полный daily workflow.
    """
    print()
    print("=" * 60)
    print("PROJECTSBIS DAILY RUN")
    print("=" * 60)

    print(
        f"Selection: {selection_id}"
    )

    # ---------------------------------------------------------
    # 1. СБИС + ОБОГАЩЕНИЕ
    # ---------------------------------------------------------
    if skip_load:
        print()
        print(
            "Загрузка СБИС пропущена (--skip-load)."
        )
        print(
            "Используются уже существующие данные БД."
        )

    else:
        print()
        print(
            "ЭТАП 1: загрузка выборки и обогащение"
        )

        await run_client_loader(
            selection_id=selection_id,
            inn=None,
            inn_file=None,
            enrich_limit=0,
            enrich_all=True,
        )

    # ---------------------------------------------------------
    # 2. ДНЕВНАЯ КАМПАНИЯ
    # ---------------------------------------------------------
    print()
    print(
        "ЭТАП 2: подготовка дневной кампании"
    )

    campaign_name = build_campaign_name(
        selection_id
    )

    campaign_id = get_or_create_mail_campaign(
        name=campaign_name,
        selection_id=selection_id,
    )

    ensure_campaign_template(
        campaign_id
    )

    print(
        f"Campaign: {campaign_name}"
    )
    print(
        f"Campaign ID: {campaign_id}"
    )

    # ---------------------------------------------------------
    # 3. ПОЛУЧАТЕЛИ
    # ---------------------------------------------------------
    print()
    print(
        "ЭТАП 3: формирование получателей"
    )

    added = populate_mail_recipients(
        campaign_id
    )

    print(
        f"Новых получателей добавлено: {added}"
    )

    # ---------------------------------------------------------
    # 4. DRY RUN / SMTP
    # ---------------------------------------------------------
    if not real_send:
        print()
        print(
            "РЕЖИМ БЕЗ РЕАЛЬНОЙ ОТПРАВКИ."
        )

        await run_sender(
            campaign_id=campaign_id,
            limit=limit,
            dry_run=True,
            mock_send=False,
            smtp_send=False,
            test_emails=[],
            tracking_test=False,
        )

        print()
        print(
            "Daily dry-run завершён."
        )
        print(
            f"Campaign ID: {campaign_id}"
        )

        return

    print()
    print(
        "ЭТАП 4: реальная SMTP-рассылка"
    )

    await run_sender(
        campaign_id=campaign_id,
        limit=limit,
        dry_run=False,
        mock_send=False,
        smtp_send=True,
        test_emails=[],
        tracking_test=False,
    )

    # ---------------------------------------------------------
    # 5. POSTFIX DELIVERY
    # ---------------------------------------------------------
    print()
    print(
        "ЭТАП 5: delivered / bounced"
    )

    await wait_for_delivery_results(
        campaign_id=campaign_id,
        log_path=mail_log,
        timeout_seconds=delivery_wait,
    )

    # ---------------------------------------------------------
    # 6. DAILY REPORT
    # ---------------------------------------------------------
    print()
    print(
        "ЭТАП 6: дневной отчёт"
    )

    await send_daily_report(
        campaign_id
    )

    print()
    print("=" * 60)
    print("DAILY RUN ЗАВЕРШЁН")
    print("=" * 60)

    print(
        f"Campaign ID: {campaign_id}"
    )


def main() -> None:
    """
    CLI-точка входа daily-run.
    """
    arguments = parse_arguments()

    asyncio.run(
        run_daily(
            selection_id=arguments.selection,
            skip_load=arguments.skip_load,
            real_send=arguments.send,
            limit=arguments.limit,
            delivery_wait=arguments.delivery_wait,
            mail_log=arguments.mail_log,
        )
    )


if __name__ == "__main__":
    main()