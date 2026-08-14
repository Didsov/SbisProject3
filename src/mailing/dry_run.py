"""
Dry-run почтовой кампании.

Назначение модуля:
- показать состояние очереди рассылки;
- вывести статистику кампании;
- показать несколько первых получателей;
- ничего не отправлять;
- ничего не изменять в базе данных.

Запуск:

    python -m src.mailing.dry_run --campaign-id 1

    python -m src.mailing.dry_run \
        --campaign-id 1 \
        --limit 10

Функции:
- parse_arguments() — разобрать CLI-параметры;
- run_dry_run() — вывести состояние кампании;
- main() — точка входа.
"""

from __future__ import annotations

import argparse

from src.database import (
    get_mail_campaign_stats,
    get_pending_mail_recipients,
)


def parse_arguments() -> argparse.Namespace:
    """
    Разобрать параметры запуска dry-run.

    Поддерживаемые параметры:
    - --campaign-id — обязательный ID кампании;
    - --limit — сколько первых pending-получателей показать.

    Возвращает:
        argparse.Namespace с параметрами запуска.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Показать состояние очереди почтовой кампании "
            "без отправки писем."
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
        default=10,
        help=(
            "Количество первых pending-получателей "
            "для отображения."
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

    return arguments


def run_dry_run(
    campaign_id: int,
    limit: int,
) -> None:
    """
    Вывести статистику и очередь кампании.

    Что делает:
    - получает сводную статистику;
    - получает первые pending-записи;
    - выводит информацию в консоль;
    - не изменяет базу данных.

    Аргументы:
        campaign_id:
            ID кампании.

        limit:
            Максимальное количество получателей,
            отображаемых в консоли.

    Возвращает:
        None.
    """
    stats = get_mail_campaign_stats(
        campaign_id
    )

    recipients = get_pending_mail_recipients(
        campaign_id=campaign_id,
        limit=limit,
    )

    print(
        f"Кампания #{campaign_id}"
    )

    print()
    print(
        f"Всего получателей: {stats['total']}"
    )
    print(
        f"Ожидают отправки: {stats['pending']}"
    )
    print(
        f"Отправлено: {stats['sent']}"
    )
    print(
        f"Доставлено: {stats['delivered']}"
    )
    print(
        f"Открыто: {stats['opened']}"
    )
    print(
        f"Клики: {stats['clicked']}"
    )
    print(
        f"Ошибки доставки: {stats['bounced']}"
    )
    print(
        f"Ошибки отправки: {stats['failed']}"
    )
    print(
        f"Отписались: {stats['unsubscribed']}"
    )

    print()
    print(
        f"Первые pending-получатели "
        f"(максимум {limit}):"
    )

    if not recipients:
        print(
            "Очередь пуста."
        )
        return

    for recipient in recipients:
        print(
            f"[{recipient['recipient_id']}] "
            f"{recipient['name']} | "
            f"ИНН {recipient['inn']} | "
            f"{recipient['email']}"
        )


def main() -> None:
    """
    Запустить dry-run кампании из командной строки.

    Возвращает:
        None.
    """
    arguments = parse_arguments()

    run_dry_run(
        campaign_id=arguments.campaign_id,
        limit=arguments.limit,
    )


if __name__ == "__main__":
    main()