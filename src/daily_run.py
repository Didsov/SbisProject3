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
    create_mail_run,
    finish_mail_run,
    get_connection,
    get_or_create_mail_campaign,
    get_mail_run_message_counts,
    initialize_database,
    populate_mail_recipients,
    update_mail_run_counts,
)
from src.daily_lock import (
    DailyRunAlreadyRunningError,
    daily_run_lock,
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

DAILY_RUN_ALREADY_RUNNING_EXIT_CODE = 3


def refresh_mail_run_counts(
    *,
    run_id: int,
    campaign_id: int,
    recipients_added: int,
) -> dict[str, int]:
    """Пересчитать и сохранить счётчики сообщений текущего запуска."""
    message_counts = get_mail_run_message_counts(
        run_id=run_id,
        campaign_id=campaign_id,
    )

    update_mail_run_counts(
        run_id,
        recipients_added=recipients_added,
        **message_counts,
    )

    return message_counts


def determine_mail_run_status(
    counts: dict[str, int],
) -> str:
    """Определить success/partial по завершённым результатам писем."""
    if any(
        counts[field_name] > 0
        for field_name in (
            "bounced_count",
            "deferred_count",
            "failed_count",
        )
    ):
        return "partial"

    # Неизвестный delivery_status после ожидания тоже означает,
    # что запуск пока нельзя считать полностью успешным.
    if counts["delivered_count"] < counts["sent_count"]:
        return "partial"

    return "success"


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
                AND mm.status = 'sent'
                AND mm.delivery_status IN (
                    'unknown',
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

    Если часть писем остаётся deferred/unknown,
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


async def load_daily_selection(
    *,
    selection_id: int,
    skip_load: bool,
) -> None:
    """Выполнить существующий этап загрузки и обогащения выборки."""
    if skip_load:
        print()
        print(
            "Загрузка СБИС пропущена (--skip-load)."
        )
        print(
            "Используются уже существующие данные БД."
        )
        return

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


async def run_daily(
    *,
    selection_id: int,
    skip_load: bool,
    real_send: bool,
    limit: int,
    delivery_wait: int,
    mail_log: Path,
) -> None:
    """Выполнить daily workflow под единой межпроцессной блокировкой."""
    with daily_run_lock():
        await _run_daily_locked(
            selection_id=selection_id,
            skip_load=skip_load,
            real_send=real_send,
            limit=limit,
            delivery_wait=delivery_wait,
            mail_log=mail_log,
        )


async def _run_daily_locked(
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

    # Гарантирует применение повторяемых миграций mailing-схемы
    # также в режиме --skip-load.
    initialize_database()

    campaign_name = build_campaign_name(
        selection_id
    )

    campaign_id = get_or_create_mail_campaign(
        name=campaign_name,
        selection_id=selection_id,
    )

    run_id = create_mail_run(
        campaign_id=campaign_id,
        selection_id=selection_id,
        trigger="manual",
    )

    recipients_added = 0
    message_counts = {
        "sent_count": 0,
        "delivered_count": 0,
        "bounced_count": 0,
        "deferred_count": 0,
        "failed_count": 0,
    }

    print(
        f"Campaign: {campaign_name}"
    )
    print(
        f"Campaign ID: {campaign_id}"
    )
    print(
        f"Mail run ID: {run_id}"
    )

    try:
        # ---------------------------------------------------------
        # 1. СБИС + ОБОГАЩЕНИЕ
        # ---------------------------------------------------------
        await load_daily_selection(
            selection_id=selection_id,
            skip_load=skip_load,
        )

        # ---------------------------------------------------------
        # 2. ДНЕВНАЯ КАМПАНИЯ
        # ---------------------------------------------------------
        print()
        print(
            "ЭТАП 2: подготовка дневной кампании"
        )

        ensure_campaign_template(
            campaign_id
        )

        # ---------------------------------------------------------
        # 3. ПОЛУЧАТЕЛИ
        # ---------------------------------------------------------
        print()
        print(
            "ЭТАП 3: формирование получателей"
        )

        recipients_added = populate_mail_recipients(
            campaign_id
        )

        update_mail_run_counts(
            run_id,
            recipients_added=recipients_added,
            **message_counts,
        )

        print(
            f"Новых получателей добавлено: {recipients_added}"
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
                run_id=run_id,
                dry_run=True,
                mock_send=False,
                smtp_send=False,
                test_emails=[],
                tracking_test=False,
            )

            message_counts = refresh_mail_run_counts(
                run_id=run_id,
                campaign_id=campaign_id,
                recipients_added=recipients_added,
            )

            finish_mail_run(
                run_id,
                status="success",
                recipients_added=recipients_added,
                **message_counts,
            )

            print()
            print(
                "Daily dry-run завершён."
            )
            print(
                f"Campaign ID: {campaign_id}"
            )
            print(
                f"Mail run ID: {run_id}"
            )

            return

        print()
        print(
            "ЭТАП 4: реальная SMTP-рассылка"
        )

        await run_sender(
            campaign_id=campaign_id,
            limit=limit,
            run_id=run_id,
            dry_run=False,
            mock_send=False,
            smtp_send=True,
            test_emails=[],
            tracking_test=False,
        )

        message_counts = refresh_mail_run_counts(
            run_id=run_id,
            campaign_id=campaign_id,
            recipients_added=recipients_added,
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

        message_counts = refresh_mail_run_counts(
            run_id=run_id,
            campaign_id=campaign_id,
            recipients_added=recipients_added,
        )

        # ---------------------------------------------------------
        # 6. DAILY REPORT
        # ---------------------------------------------------------
        print()
        print(
            "ЭТАП 6: дневной отчёт"
        )

        await send_daily_report(
            campaign_id,
            run_id=run_id,
        )

        final_status = determine_mail_run_status(
            message_counts
        )

        finish_mail_run(
            run_id,
            status=final_status,
            recipients_added=recipients_added,
            **message_counts,
        )

        print()
        print("=" * 60)
        print("DAILY RUN ЗАВЕРШЁН")
        print("=" * 60)

        print(
            f"Campaign ID: {campaign_id}"
        )
        print(
            f"Mail run ID: {run_id}"
        )
        print(
            f"Mail run status: {final_status}"
        )

    except Exception as error:
        error_text = (
            f"{type(error).__name__}: {error}"
        )

        try:
            message_counts = get_mail_run_message_counts(
                run_id=run_id,
                campaign_id=campaign_id,
            )

            finish_mail_run(
                run_id,
                status="failed",
                recipients_added=recipients_added,
                error_text=error_text,
                **message_counts,
            )
        except Exception as history_error:
            print(
                "Не удалось сохранить failed в mail_runs: "
                f"{history_error}"
            )

        print(
            f"Mail run #{run_id} завершён с ошибкой: "
            f"{error_text}"
        )

        raise


def main() -> None:
    """
    CLI-точка входа daily-run.
    """
    arguments = parse_arguments()

    try:
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
    except DailyRunAlreadyRunningError as error:
        print(error)
        raise SystemExit(
            DAILY_RUN_ALREADY_RUNNING_EXIT_CODE
        ) from None


if __name__ == "__main__":
    main()
