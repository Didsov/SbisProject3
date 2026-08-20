"""
Сброс почтовой истории тестовой выборки ProjectSbis.

Используется для повторного полного тестирования daily-run.

ВАЖНО:
модуль разрешает очищать только тестовую selection_id=990001.

Удаляются:
- mail_events;
- mail_messages;
- mail_recipients;
- mail_campaigns,

связанные с тестовой выборкой.

Клиенты, контакты и client_selections сохраняются.

Запуск:

    python -m src.mailing.reset_test_selection --yes
"""

from __future__ import annotations

import argparse

from src.database import get_connection


TEST_SELECTION_ID = 990001


def reset_test_selection() -> dict[str, int]:
    """
    Очистить почтовую историю selection 990001.
    """
    stats = {
        "events": 0,
        "messages": 0,
        "recipients": 0,
        "campaigns": 0,
    }

    with get_connection() as connection:
        campaign_rows = connection.execute(
            """
            SELECT id
            FROM mail_campaigns
            WHERE selection_id = ?
            """,
            (TEST_SELECTION_ID,),
        ).fetchall()

        campaign_ids = [
            int(row["id"])
            for row in campaign_rows
        ]

        if not campaign_ids:
            return stats

        placeholders = ",".join(
            "?"
            for _ in campaign_ids
        )

        # mail_events
        cursor = connection.execute(
            f"""
            DELETE FROM mail_events
            WHERE message_id IN (
                SELECT mm.id
                FROM mail_messages AS mm

                INNER JOIN mail_recipients AS mr
                    ON mr.id = mm.recipient_id

                WHERE mr.campaign_id IN (
                    {placeholders}
                )
            )
            """,
            tuple(campaign_ids),
        )

        stats["events"] = cursor.rowcount

        # mail_messages
        cursor = connection.execute(
            f"""
            DELETE FROM mail_messages
            WHERE recipient_id IN (
                SELECT id
                FROM mail_recipients
                WHERE campaign_id IN (
                    {placeholders}
                )
            )
            """,
            tuple(campaign_ids),
        )

        stats["messages"] = cursor.rowcount

        # mail_recipients
        cursor = connection.execute(
            f"""
            DELETE FROM mail_recipients
            WHERE campaign_id IN (
                {placeholders}
            )
            """,
            tuple(campaign_ids),
        )

        stats["recipients"] = cursor.rowcount

        # mail_campaigns
        cursor = connection.execute(
            f"""
            DELETE FROM mail_campaigns
            WHERE id IN (
                {placeholders}
            )
            """,
            tuple(campaign_ids),
        )

        stats["campaigns"] = cursor.rowcount

    return stats


def main() -> None:
    """
    CLI-точка входа.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Очистить почтовую историю "
            "тестовой выборки 990001."
        )
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Подтвердить очистку.",
    )

    arguments = parser.parse_args()

    if not arguments.yes:
        parser.error(
            "Для очистки необходимо указать --yes"
        )

    print(
        f"Очищаю почтовую историю "
        f"selection #{TEST_SELECTION_ID}..."
    )

    stats = reset_test_selection()

    print()
    print("RESET завершён.")
    print(
        f"mail_events удалено: {stats['events']}"
    )
    print(
        f"mail_messages удалено: {stats['messages']}"
    )
    print(
        f"mail_recipients удалено: {stats['recipients']}"
    )
    print(
        f"mail_campaigns удалено: {stats['campaigns']}"
    )

    print()
    print(
        "clients / client_contacts / "
        "client_selections не изменялись."
    )


if __name__ == "__main__":
    main()