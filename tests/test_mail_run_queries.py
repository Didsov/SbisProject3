from __future__ import annotations

from contextlib import closing
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

import src.database as database


class MailRunQueriesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(
            prefix="projectsbis_mail_run_queries_"
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
            warnings.simplefilter(
                "ignore",
                ResourceWarning,
            )
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

            recipient_ids: list[int] = []

            for number in range(1, 5):
                client_id = connection.execute(
                    """
                    INSERT INTO clients (
                        spp_uuid,
                        inn,
                        name
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        f"fixture-{number}",
                        f"990000000{number}",
                        f"Company {number}",
                    ),
                ).lastrowid
                recipient_id = connection.execute(
                    """
                    INSERT INTO mail_recipients (
                        campaign_id,
                        client_id,
                        email
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        self.campaign_id,
                        client_id,
                        f"company{number}@example.invalid",
                    ),
                ).lastrowid
                recipient_ids.append(int(recipient_id))

            self.run_a = int(
                connection.execute(
                    """
                    INSERT INTO mail_runs (
                        campaign_id,
                        selection_id,
                        trigger,
                        status,
                        started_at,
                        finished_at,
                        recipients_added,
                        sent_count,
                        delivered_count,
                        bounced_count,
                        deferred_count,
                        failed_count
                    )
                    VALUES (
                        ?, 5984, 'manual', 'success',
                        '2026-08-19 09:00:00',
                        '2026-08-19 09:10:00',
                        2, 1, 1, 0, 0, 0
                    )
                    """,
                    (self.campaign_id,),
                ).lastrowid
            )
            self.run_b = int(
                connection.execute(
                    """
                    INSERT INTO mail_runs (
                        campaign_id,
                        selection_id,
                        trigger,
                        status,
                        started_at,
                        finished_at,
                        recipients_added,
                        sent_count,
                        delivered_count,
                        bounced_count,
                        deferred_count,
                        failed_count,
                        error_text
                    )
                    VALUES (
                        ?, 5984, 'manual', 'partial',
                        '2026-08-20 09:00:00',
                        '2026-08-20 09:12:00',
                        2, 1, 0, 1, 0, 1,
                        'fixture partial run'
                    )
                    """,
                    (self.campaign_id,),
                ).lastrowid
            )
            self.run_empty = int(
                connection.execute(
                    """
                    INSERT INTO mail_runs (
                        campaign_id,
                        selection_id,
                        trigger,
                        status,
                        started_at,
                        finished_at,
                        recipients_added,
                        sent_count,
                        delivered_count,
                        bounced_count,
                        deferred_count,
                        failed_count
                    )
                    VALUES (
                        ?, 5984, 'manual', 'success',
                        '2026-08-21 09:00:00',
                        '2026-08-21 09:01:00',
                        0, 0, 0, 0, 0, 0
                    )
                    """,
                    (self.campaign_id,),
                ).lastrowid
            )

            self.message_a = self._insert_message(
                connection,
                recipient_id=recipient_ids[0],
                run_id=self.run_a,
                is_test=0,
                status="sent",
                delivery_status="delivered",
                sent_at="2026-08-19 09:01:00",
            )
            self.test_message_a = self._insert_message(
                connection,
                recipient_id=recipient_ids[1],
                run_id=self.run_a,
                is_test=1,
                status="sent",
                delivery_status="delivered",
                sent_at="2026-08-19 09:02:00",
            )
            self.message_b_sent = self._insert_message(
                connection,
                recipient_id=recipient_ids[2],
                run_id=self.run_b,
                is_test=0,
                status="sent",
                delivery_status="bounced",
                sent_at="2026-08-20 09:01:00",
            )
            self.message_b_failed = self._insert_message(
                connection,
                recipient_id=recipient_ids[3],
                run_id=self.run_b,
                is_test=0,
                status="failed",
                delivery_status="unknown",
                sent_at=None,
            )

            for event_type, event_at, event_data in (
                ("sent", "2026-08-19 09:01:00", None),
                ("delivered", "2026-08-19 09:02:00", None),
                ("opened", "2026-08-19 09:03:00", None),
                ("opened", "2026-08-19 09:04:00", None),
                (
                    "clicked",
                    "2026-08-19 09:05:00",
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
                        self.message_a,
                        event_type,
                        event_at,
                        event_data,
                    ),
                )

            connection.execute(
                """
                INSERT INTO mail_events (
                    message_id,
                    event_type,
                    event_at
                )
                VALUES (?, 'opened', '2026-08-19 10:00:00')
                """,
                (self.test_message_a,),
            )
            connection.execute(
                """
                INSERT INTO mail_events (
                    message_id,
                    event_type,
                    event_at
                )
                VALUES (?, 'bounced', '2026-08-20 09:03:00')
                """,
                (self.message_b_sent,),
            )

            connection.commit()

    @staticmethod
    def _insert_message(
        connection,
        *,
        recipient_id: int,
        run_id: int,
        is_test: int,
        status: str,
        delivery_status: str,
        sent_at: str | None,
    ) -> int:
        message_id = connection.execute(
            """
            INSERT INTO mail_messages (
                recipient_id,
                run_id,
                provider,
                status,
                delivery_status,
                is_test,
                sent_at
            )
            VALUES (?, ?, 'smtp', ?, ?, ?, ?)
            """,
            (
                recipient_id,
                run_id,
                status,
                delivery_status,
                is_test,
                sent_at,
            ),
        ).lastrowid

        return int(message_id)

    def test_get_recent_mail_runs_returns_latest_first(self) -> None:
        runs = database.get_recent_mail_runs(limit=1)

        self.assertEqual(len(runs), 1)
        self.assertEqual(
            set(runs[0]),
            {
                "run_id",
                "campaign_id",
                "campaign_name",
                "selection_id",
                "trigger",
                "status",
                "started_at",
                "finished_at",
                "recipients_added",
                "sent_count",
                "delivered_count",
                "bounced_count",
                "deferred_count",
                "failed_count",
            },
        )
        self.assertEqual(runs[0]["run_id"], self.run_empty)
        self.assertEqual(runs[0]["campaign_id"], self.campaign_id)
        self.assertEqual(
            runs[0]["campaign_name"],
            "new_companies_daily",
        )
        self.assertEqual(runs[0]["selection_id"], 5984)
        self.assertEqual(runs[0]["trigger"], "manual")
        self.assertEqual(runs[0]["status"], "success")
        self.assertEqual(runs[0]["recipients_added"], 0)
        self.assertEqual(runs[0]["sent_count"], 0)
        self.assertEqual(runs[0]["delivered_count"], 0)
        self.assertEqual(runs[0]["bounced_count"], 0)
        self.assertEqual(runs[0]["deferred_count"], 0)
        self.assertEqual(runs[0]["failed_count"], 0)

        with self.assertRaises(ValueError):
            database.get_recent_mail_runs(limit=0)

    def test_get_latest_mail_run_with_sent_messages_skips_empty_run(
        self,
    ) -> None:
        run = database.get_latest_mail_run_with_sent_messages()

        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run["run_id"], self.run_b)
        self.assertEqual(run["sent_count"], 1)
        self.assertNotEqual(run["run_id"], self.run_empty)

    def test_get_mail_run_details(self) -> None:
        details = database.get_mail_run_details(self.run_b)

        self.assertEqual(
            set(details),
            {
                "run_id",
                "campaign_id",
                "campaign_name",
                "selection_id",
                "trigger",
                "status",
                "started_at",
                "finished_at",
                "recipients_added",
                "sent_count",
                "delivered_count",
                "bounced_count",
                "deferred_count",
                "failed_count",
                "error_text",
            },
        )
        self.assertEqual(details["run_id"], self.run_b)
        self.assertEqual(details["campaign_id"], self.campaign_id)
        self.assertEqual(details["campaign_name"], "new_companies_daily")
        self.assertEqual(details["status"], "partial")
        self.assertEqual(details["error_text"], "fixture partial run")
        self.assertEqual(details["started_at"], "2026-08-20 09:00:00")
        self.assertEqual(details["finished_at"], "2026-08-20 09:12:00")

        with self.assertRaises(LookupError):
            database.get_mail_run_details(999_999)

        with self.assertRaises(ValueError):
            database.get_mail_run_details(0)

    def test_get_mail_run_messages_excludes_test_by_default(self) -> None:
        messages = database.get_mail_run_messages(self.run_a)

        self.assertEqual(len(messages), 1)
        self.assertEqual(
            set(messages[0]),
            {
                "message_id",
                "company_name",
                "email",
                "send_status",
                "delivery_status",
                "sent_at",
                "opened_count",
                "clicked_count",
                "last_event_at",
            },
        )
        self.assertEqual(messages[0]["message_id"], self.message_a)
        self.assertEqual(messages[0]["company_name"], "Company 1")
        self.assertEqual(
            messages[0]["email"],
            "company1@example.invalid",
        )
        self.assertEqual(messages[0]["send_status"], "sent")
        self.assertEqual(messages[0]["delivery_status"], "delivered")
        self.assertEqual(messages[0]["opened_count"], 2)
        self.assertEqual(messages[0]["clicked_count"], 1)
        self.assertEqual(
            messages[0]["last_event_at"],
            "2026-08-19 09:05:00",
        )

        messages_with_test = database.get_mail_run_messages(
            self.run_a,
            include_test=True,
        )
        self.assertEqual(len(messages_with_test), 2)
        self.assertEqual(
            {row["message_id"] for row in messages_with_test},
            {self.message_a, self.test_message_a},
        )

        with self.assertRaises(ValueError):
            database.get_mail_run_messages(0)

    def test_get_mail_run_events_excludes_test_by_default(self) -> None:
        events = database.get_mail_run_events(self.run_a)

        self.assertEqual(len(events), 5)
        self.assertEqual(
            set(events[0]),
            {
                "event_id",
                "message_id",
                "company_name",
                "email",
                "event_type",
                "event_at",
                "event_data",
            },
        )
        self.assertEqual(
            [row["event_type"] for row in events],
            ["sent", "delivered", "opened", "opened", "clicked"],
        )
        self.assertTrue(
            all(row["message_id"] == self.message_a for row in events)
        )
        self.assertEqual(events[-1]["company_name"], "Company 1")
        self.assertEqual(
            events[-1]["email"],
            "company1@example.invalid",
        )
        self.assertEqual(
            events[-1]["event_data"],
            '{"click_key":"whatsapp"}',
        )

        events_with_test = database.get_mail_run_events(
            self.run_a,
            include_test=True,
        )
        self.assertEqual(len(events_with_test), 6)
        self.assertEqual(
            {row["message_id"] for row in events_with_test},
            {self.message_a, self.test_message_a},
        )

        with self.assertRaises(ValueError):
            database.get_mail_run_events(0)

    def test_admin_queries_execute_select_only(self) -> None:
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
            database.get_recent_mail_runs(limit=10)
            database.get_latest_mail_run_with_sent_messages()
            database.get_mail_run_details(self.run_a)
            database.get_mail_run_messages(self.run_a)
            database.get_mail_run_events(self.run_a)

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
