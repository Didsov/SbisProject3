from __future__ import annotations

import unittest
from unittest.mock import call, patch

from aiohttp.test_utils import TestClient, TestServer

from src.admin import app as admin_app


RUN = {
    "run_id": 7,
    "campaign_id": 3,
    "campaign_name": "Campaign <unsafe>",
    "selection_id": 5984,
    "trigger": "manual",
    "status": "partial",
    "started_at": "2026-08-20 09:00:00",
    "finished_at": "2026-08-20 09:12:00",
    "recipients_added": 2,
    "sent_count": 2,
    "delivered_count": 1,
    "bounced_count": 1,
    "deferred_count": 0,
    "failed_count": 0,
}

DETAILS = {
    **RUN,
    "error_text": None,
}

MESSAGES = [
    {
        "message_id": 21,
        "company_name": "Company <One>",
        "email": "one@example.invalid",
        "send_status": "sent",
        "delivery_status": "delivered",
        "sent_at": "2026-08-20 09:01:00",
        "opened_count": 2,
        "clicked_count": 1,
        "last_event_at": "2026-08-20 09:05:00",
    },
]

EVENTS = [
    {
        "event_id": 31,
        "message_id": 21,
        "company_name": "Company <One>",
        "email": "one@example.invalid",
        "event_type": "clicked",
        "event_at": "2026-08-20 09:05:00",
        "event_data": '<script>alert("unsafe")</script>',
    },
]


class AdminAppHttpTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.recent_patcher = patch.object(
            admin_app,
            "get_recent_mail_runs",
            return_value=[RUN],
        )
        self.details_patcher = patch.object(
            admin_app,
            "get_mail_run_details",
            return_value=DETAILS,
        )
        self.messages_patcher = patch.object(
            admin_app,
            "get_mail_run_messages",
            return_value=MESSAGES,
        )
        self.events_patcher = patch.object(
            admin_app,
            "get_mail_run_events",
            return_value=EVENTS,
        )

        self.get_recent = self.recent_patcher.start()
        self.get_details = self.details_patcher.start()
        self.get_messages = self.messages_patcher.start()
        self.get_events = self.events_patcher.start()

        self.client = TestClient(
            TestServer(admin_app.create_app())
        )
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.events_patcher.stop()
        self.messages_patcher.stop()
        self.details_patcher.stop()
        self.recent_patcher.stop()

    async def test_routes_are_registered(self) -> None:
        routes = {
            (
                route.method,
                route.resource.canonical,
            )
            for route in self.client.app.router.routes()
            if route.method == "GET"
        }

        self.assertEqual(
            routes,
            {
                ("GET", "/admin"),
                ("GET", "/admin/runs"),
                ("GET", "/admin/runs/{run_id}"),
            },
        )

    async def test_dashboard_and_runs_list(self) -> None:
        dashboard_response = await self.client.get(
            "/admin"
        )
        dashboard_html = await dashboard_response.text()

        self.assertEqual(dashboard_response.status, 200)
        self.assertEqual(
            dashboard_response.content_type,
            "text/html",
        )
        self.assertIn("Последний запуск", dashboard_html)
        for label in (
            "Получателей",
            "Отправлено",
            "Принято сервером",
            "Bounce",
            "Открыли",
            "Кликнули",
            "Длительность",
            "Кампания",
            "Selection",
        ):
            self.assertIn(label, dashboard_html)
        self.assertIn("⚠ Частично", dashboard_html)
        self.assertIn("12 мин", dashboard_html)
        self.assertIn("Campaign &lt;unsafe&gt;", dashboard_html)
        self.assertNotIn("Campaign <unsafe>", dashboard_html)
        self.assertIn('/admin/runs/7', dashboard_html)
        self.get_messages.assert_called_once_with(7)

        runs_response = await self.client.get(
            "/admin/runs"
        )
        runs_html = await runs_response.text()

        self.assertEqual(runs_response.status, 200)
        self.assertIn("История запусков", runs_html)
        for label in (
            "Запуск",
            "Кампания",
            "Начало",
            "Длительность",
            "Получатели",
            "Отправлено",
            "Принято сервером",
            "Ошибки",
            "Статус",
        ):
            self.assertIn(label, runs_html)
        self.assertIn("⚠ Частично", runs_html)
        self.assertGreaterEqual(
            runs_html.count('href="/admin/runs/7"'),
            10,
        )
        self.assertIn('@media (max-width: 720px)', runs_html)
        self.assertIn('data-label="Кампания"', runs_html)
        self.assertIn("Campaign &lt;unsafe&gt;", runs_html)
        self.assertNotIn("Campaign <unsafe>", runs_html)
        self.assertEqual(
            self.get_recent.call_args_list,
            [
                call(limit=1),
                call(limit=admin_app.RECENT_RUNS_LIMIT),
            ],
        )

    async def test_run_details_contains_messages_and_events(self) -> None:
        response = await self.client.get(
            "/admin/runs/7"
        )
        page_html = await response.text()

        self.assertEqual(response.status, 200)
        self.assertIn("Запуск #7", page_html)
        self.assertIn("Company &lt;One&gt;", page_html)
        self.assertIn("Отправка", page_html)
        self.assertIn("Доставка", page_html)
        self.assertIn("Открытия", page_html)
        self.assertIn("Клики", page_html)
        self.assertIn("✓ Принято", page_html)
        self.assertIn("Ошибки SMTP", page_html)
        self.assertIn("Последние события", page_html)
        self.assertIn("Клик", page_html)
        self.assertIn('<details class="event-data">', page_html)
        self.assertIn("<summary>Данные</summary>", page_html)
        self.assertIn(
            '&lt;script&gt;alert(&quot;unsafe&quot;)&lt;/script&gt;',
            page_html,
        )
        self.assertNotIn("<script>alert", page_html)
        self.get_details.assert_called_once_with(7)
        self.get_messages.assert_called_once_with(7)
        self.get_events.assert_called_once_with(7)

    async def test_invalid_run_id_returns_400(self) -> None:
        response = await self.client.get(
            "/admin/runs/not-a-number"
        )

        self.assertEqual(response.status, 400)
        self.assertEqual(
            await response.text(),
            "Некорректный run_id.",
        )
        self.get_details.assert_not_called()
        self.get_messages.assert_not_called()
        self.get_events.assert_not_called()

    async def test_missing_run_returns_404(self) -> None:
        self.get_details.side_effect = LookupError(
            "fixture missing"
        )

        response = await self.client.get(
            "/admin/runs/999"
        )

        self.assertEqual(response.status, 404)
        self.assertEqual(
            await response.text(),
            "Запуск не найден.",
        )
        self.get_details.assert_called_once_with(999)
        self.get_messages.assert_not_called()
        self.get_events.assert_not_called()


class AdminAppCliTestCase(unittest.TestCase):
    def test_main_binds_only_loopback(self) -> None:
        with (
            patch.object(admin_app.web, "run_app") as run_app,
            patch("builtins.print") as print_mock,
        ):
            admin_app.main()

        print_mock.assert_called_once_with(
            "ProjectSbis admin listening on "
            "http://127.0.0.1:8081/admin",
            flush=True,
        )
        self.assertEqual(
            run_app.call_args.kwargs["host"],
            "127.0.0.1",
        )
        self.assertEqual(
            run_app.call_args.kwargs["port"],
            8081,
        )


class AdminAppPresentationTestCase(unittest.TestCase):
    def test_all_required_statuses_are_localized(self) -> None:
        cases = (
            (admin_app._run_status, "success", "✓ Успешно"),
            (admin_app._run_status, "partial", "⚠ Частично"),
            (admin_app._run_status, "failed", "✕ Ошибка"),
            (admin_app._run_status, "running", "… Выполняется"),
            (admin_app._send_status, "sent", "✓"),
            (admin_app._send_status, "failed", "✕"),
            (admin_app._delivery_status, "delivered", "✓ Принято"),
            (admin_app._delivery_status, "bounced", "✕ Bounce"),
            (admin_app._delivery_status, "deferred", "… Ожидание"),
            (admin_app._delivery_status, "unknown", "… Нет результата"),
            (admin_app._event_status, "sent", "Отправлено"),
            (admin_app._event_status, "delivered", "Принято сервером"),
            (admin_app._event_status, "bounced", "Bounce"),
            (admin_app._event_status, "deferred", "Временная ошибка"),
            (admin_app._event_status, "opened", "Открыто"),
            (admin_app._event_status, "clicked", "Клик"),
        )

        for renderer, status, label in cases:
            with self.subTest(status=status, label=label):
                self.assertIn(label, renderer(status))

    def test_unknown_status_is_escaped(self) -> None:
        rendered = admin_app._run_status("<script>alert(1)</script>")

        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)


if __name__ == "__main__":
    unittest.main()
