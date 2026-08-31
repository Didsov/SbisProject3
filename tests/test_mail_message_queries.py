from __future__ import annotations

from contextlib import closing
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

import src.database as database


class MailMessageQueriesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(
            prefix="projectsbis_mail_message_queries_"
        )
        self.database_path = (
            Path(self.temp_directory.name) / "project.db"
        )
        self.database_patch = patch.object(
            database,
            "DATABASE_FILE",
            self.database_path,
        )
        self.database_patch.start()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            database.initialize_database()
            gc.collect()

        self._create_fixture()

    def tearDown(self) -> None:
        self.database_patch.stop()
        gc.collect()
        self.temp_directory.cleanup()

    def _create_fixture(self) -> None:
        with closing(database.get_connection()) as connection:
            campaign = connection.execute(
                """
                SELECT id
                FROM mail_campaigns
                WHERE name = 'new_companies_daily'
                """
            ).fetchone()
            self.campaign_id = int(campaign["id"])

            self.client_id = int(
                connection.execute(
                    """
                    INSERT INTO clients (
                        spp_uuid,
                        inn,
                        name
                    )
                    VALUES (
                        'message-query-fixture',
                        '9900000099',
                        'Fixture Company'
                    )
                    """
                ).lastrowid
            )
            self.recipient_id = int(
                connection.execute(
                    """
                    INSERT INTO mail_recipients (
                        campaign_id,
                        client_id,
                        email
                    )
                    VALUES (?, ?, 'fixture@example.invalid')
                    """,
                    (
                        self.campaign_id,
                        self.client_id,
                    ),
                ).lastrowid
            )
            self.run_id = int(
                connection.execute(
                    """
                    INSERT INTO mail_runs (
                        campaign_id,
                        selection_id,
                        trigger,
                        status,
                        started_at
                    )
                    VALUES (?, 5984, 'manual', 'running', ?)
                    """,
                    (
                        self.campaign_id,
                        "2026-08-21 09:00:00",
                    ),
                ).lastrowid
            )
            self.message_id = int(
                connection.execute(
                    """
                    INSERT INTO mail_messages (
                        recipient_id,
                        run_id,
                        provider,
                        provider_message_id,
                        tracking_token,
                        is_test,
                        status,
                        delivery_status,
                        sent_at
                    )
                    VALUES (
                        ?, ?, 'smtp', 'provider-message-1',
                        'tracking-token-1', 0,
                        'sent', 'delivered',
                        '2026-08-21 09:01:00'
                    )
                    """,
                    (
                        self.recipient_id,
                        self.run_id,
                    ),
                ).lastrowid
            )
            self.message_without_events_id = int(
                connection.execute(
                    """
                    INSERT INTO mail_messages (
                        recipient_id,
                        run_id,
                        provider,
                        provider_message_id,
                        tracking_token,
                        is_test,
                        status,
                        delivery_status,
                        sent_at
                    )
                    VALUES (
                        ?, NULL, 'smtp', 'provider-message-test',
                        'tracking-token-test', 1,
                        'sent', 'unknown',
                        '2026-08-21 09:10:00'
                    )
                    """,
                    (self.recipient_id,),
                ).lastrowid
            )

            # Вставка намеренно не хронологическая: запрос должен
            # сортировать сначала по event_at, затем по id.
            for event_type, event_at, event_data in (
                (
                    "delivered",
                    "2026-08-21 09:02:00",
                    '{"smtp":"250 accepted"}',
                ),
                ("sent", "2026-08-21 09:01:00", None),
                ("opened", "2026-08-21 09:03:00", None),
                (
                    "clicked",
                    "2026-08-21 09:03:00",
                    '{"click_key":"whatsapp"}',
                ),
            ):
                connection.execute(
                    """
                    INSERT INTO mail_events (
                        message_id,
                        event_type,
                        event_at,
                        event_data
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        self.message_id,
                        event_type,
                        event_at,
                        event_data,
                    ),
                )

            connection.commit()

    def test_get_mail_message_details_with_engagement_counts(self) -> None:
        details = database.get_mail_message_details(
            self.message_id
        )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(
            set(details),
            {
                "message_id",
                "run_id",
                "run_status",
                "run_started_at",
                "campaign_id",
                "campaign_name",
                "campaign_family",
                "client_id",
                "company_name",
                "inn",
                "email",
                "provider",
                "provider_message_id",
                "send_status",
                "delivery_status",
                "sent_at",
                "delivered_at",
                "tracking_token",
                "opened_count",
                "clicked_count",
                "last_event_at",
            },
        )
        self.assertEqual(details["message_id"], self.message_id)
        self.assertEqual(details["run_id"], self.run_id)
        self.assertEqual(details["campaign_id"], self.campaign_id)
        self.assertEqual(details["campaign_name"], "new_companies_daily")
        self.assertEqual(details["client_id"], self.client_id)
        self.assertEqual(details["company_name"], "Fixture Company")
        self.assertEqual(details["email"], "fixture@example.invalid")
        self.assertEqual(details["provider"], "smtp")
        self.assertEqual(
            details["provider_message_id"],
            "provider-message-1",
        )
        self.assertEqual(details["send_status"], "sent")
        self.assertEqual(details["delivery_status"], "delivered")
        self.assertEqual(details["tracking_token"], "tracking-token-1")
        self.assertEqual(details["opened_count"], 1)
        self.assertEqual(details["clicked_count"], 1)
        self.assertEqual(
            details["last_event_at"],
            "2026-08-21 09:03:00",
        )

    def test_get_mail_message_timeline_is_chronological(self) -> None:
        timeline = database.get_mail_message_timeline(
            self.message_id
        )

        self.assertEqual(len(timeline), 4)
        self.assertEqual(
            set(timeline[0]),
            {
                "event_id",
                "event_type",
                "event_at",
                "event_data",
            },
        )
        self.assertEqual(
            [event["event_type"] for event in timeline],
            ["sent", "delivered", "opened", "clicked"],
        )
        self.assertLess(
            timeline[2]["event_id"],
            timeline[3]["event_id"],
        )
        self.assertEqual(
            timeline[-1]["event_data"],
            '{"click_key":"whatsapp"}',
        )

    def test_message_without_events_and_without_run_is_supported(self) -> None:
        details = database.get_mail_message_details(
            self.message_without_events_id
        )
        timeline = database.get_mail_message_timeline(
            self.message_without_events_id
        )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertIsNone(details["run_id"])
        self.assertEqual(details["opened_count"], 0)
        self.assertEqual(details["clicked_count"], 0)
        self.assertIsNone(details["last_event_at"])
        self.assertEqual(timeline, [])

    def test_missing_message_contract(self) -> None:
        self.assertIsNone(
            database.get_mail_message_details(999_999)
        )
        self.assertEqual(
            database.get_mail_message_timeline(999_999),
            [],
        )

        with self.assertRaises(ValueError):
            database.get_mail_message_details(0)
        with self.assertRaises(ValueError):
            database.get_mail_message_timeline(0)

    def test_queries_execute_select_only(self) -> None:
        statements: list[str] = []
        original_get_connection = database.get_connection

        def get_traced_connection():
            connection = original_get_connection()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(
            database,
            "get_connection",
            side_effect=get_traced_connection,
        ):
            database.get_mail_message_details(self.message_id)
            database.get_mail_message_timeline(self.message_id)
            database.get_mail_message_details(999_999)
            database.get_mail_message_timeline(999_999)

        self.assertTrue(statements)
        self.assertTrue(
            all(
                statement.lstrip().upper().startswith("SELECT")
                for statement in statements
            ),
            statements,
        )

    def test_temporary_database_is_valid(self) -> None:
        with closing(database.get_connection()) as connection:
            result = connection.execute(
                "PRAGMA quick_check"
            ).fetchone()[0]

        self.assertEqual(result, "ok")


if __name__ == "__main__":
    unittest.main()
