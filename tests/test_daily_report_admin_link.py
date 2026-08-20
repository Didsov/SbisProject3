from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src import daily_run
from src.mailing import daily_report


class DailyReportAdminLinkTestCase(unittest.IsolatedAsyncioTestCase):
    def test_build_admin_details_url_uses_run_and_fallback(self) -> None:
        with patch.object(
            daily_report,
            "ADMIN_PUBLIC_URL",
            "https://example.invalid/admin/",
        ):
            self.assertEqual(
                daily_report.build_admin_details_url(17),
                "https://example.invalid/admin/runs/17",
            )
            self.assertEqual(
                daily_report.build_admin_details_url(None),
                "https://example.invalid/admin",
            )

    def test_report_versions_include_link_and_escape_html(self) -> None:
        details_url = (
            'https://example.invalid/admin/runs/17'
            '?next=<unsafe>&quote="'
        )

        html_body = daily_report.build_html_report(
            [],
            details_url=details_url,
        )
        text_body = daily_report.build_text_report(
            [],
            details_url=details_url,
        )

        self.assertIn(
            "Открыть подробности в админке",
            html_body,
        )
        self.assertIn(
            'href="https://example.invalid/admin/runs/17'
            '?next=&lt;unsafe&gt;&amp;quote=&quot;"',
            html_body,
        )
        self.assertNotIn("?next=<unsafe>", html_body)
        self.assertIn(
            f"Подробности:\n{details_url}",
            text_body,
        )

    async def test_send_daily_report_builds_current_run_link(self) -> None:
        provider = SimpleNamespace(
            send=AsyncMock(
                return_value=SimpleNamespace(
                    success=True,
                    error=None,
                )
            )
        )

        with (
            patch.object(
                daily_report,
                "ADMIN_PUBLIC_URL",
                "https://mail.projectsbis.ru/admin",
            ),
            patch.object(
                daily_report,
                "get_campaign_delivery_rows",
                return_value=[],
            ),
            patch.object(
                daily_report,
                "build_xlsx_report",
                return_value=b"xlsx",
            ),
            patch.object(
                daily_report,
                "get_report_recipients",
                return_value=["report@example.invalid"],
            ),
            patch.object(
                daily_report.SMTPMailProvider,
                "from_env",
                return_value=provider,
            ),
        ):
            await daily_report.send_daily_report(
                3,
                run_id=17,
            )

        message = provider.send.await_args.args[0]
        expected_url = (
            "https://mail.projectsbis.ru/admin/runs/17"
        )
        self.assertIn(expected_url, message.html_body)
        self.assertIn(
            f"Подробности:\n{expected_url}",
            message.text_body,
        )

    async def test_daily_run_passes_current_run_id_to_report(self) -> None:
        counts = {
            "sent_count": 1,
            "delivered_count": 1,
            "bounced_count": 0,
            "deferred_count": 0,
            "failed_count": 0,
        }
        report = AsyncMock()

        with (
            patch.object(daily_run, "initialize_database"),
            patch.object(
                daily_run,
                "get_or_create_mail_campaign",
                return_value=11,
            ),
            patch.object(
                daily_run,
                "create_mail_run",
                return_value=22,
            ),
            patch.object(
                daily_run,
                "load_daily_selection",
                new=AsyncMock(),
            ),
            patch.object(daily_run, "ensure_campaign_template"),
            patch.object(
                daily_run,
                "populate_mail_recipients",
                return_value=1,
            ),
            patch.object(daily_run, "update_mail_run_counts"),
            patch.object(
                daily_run,
                "run_sender",
                new=AsyncMock(),
            ),
            patch.object(
                daily_run,
                "refresh_mail_run_counts",
                return_value=counts,
            ),
            patch.object(
                daily_run,
                "wait_for_delivery_results",
                new=AsyncMock(),
            ),
            patch.object(
                daily_run,
                "send_daily_report",
                new=report,
            ),
            patch.object(daily_run, "finish_mail_run"),
        ):
            await daily_run._run_daily_locked(
                selection_id=5984,
                skip_load=True,
                real_send=True,
                limit=10,
                delivery_wait=0,
                mail_log=Path("mail.log"),
            )

        report.assert_awaited_once_with(
            11,
            run_id=22,
        )


if __name__ == "__main__":
    unittest.main()
