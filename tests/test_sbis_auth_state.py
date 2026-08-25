from __future__ import annotations

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src import daily_run
from src.sbis import auth
from src.sbis import auth_monitor
from src.sbis import auth_state
from src.sbis import cookie_manager


def result(
    status: auth.SbisAuthStatus,
    http_status: int | None,
) -> auth.SbisAuthCheckResult:
    return auth.SbisAuthCheckResult(
        status=status,
        checked_at=datetime.now().astimezone(),
        http_status=http_status,
    )


class SbisAuthStateMachineTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.state_path = self.directory / "sbis_auth_state.json"

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def _process(
        self,
        check_result: auth.SbisAuthCheckResult,
        *,
        alert: bool = True,
    ) -> auth_monitor.SbisAuthMonitorOutcome:
        return await auth_monitor.process_sbis_auth_result(
            check_result,
            alert_on_invalid=alert,
            state_path=self.state_path,
        )

    async def test_ten_invalid_checks_send_exactly_one_alert(self) -> None:
        send_alert = AsyncMock()
        outcomes: list[auth_monitor.SbisAuthMonitorOutcome] = []

        with patch.object(
            auth_monitor,
            "send_sbis_auth_alert",
            new=send_alert,
        ):
            for _ in range(10):
                outcomes.append(
                    await self._process(
                        result("invalid_auth", 401),
                    )
                )

        send_alert.assert_awaited_once()
        self.assertTrue(outcomes[0].alert_sent)
        self.assertTrue(
            all(item.alert_suppressed for item in outcomes[1:])
        )
        state = auth_state.load_sbis_auth_state(self.state_path)
        self.assertEqual(state.status, "alerted")
        self.assertEqual(state.last_http_status, 401)
        self.assertIsNotNone(state.last_check_at)
        self.assertIsNotNone(state.last_invalid_at)

    async def test_invalid_valid_invalid_sends_two_episode_alerts(self) -> None:
        send_alert = AsyncMock()
        with patch.object(
            auth_monitor,
            "send_sbis_auth_alert",
            new=send_alert,
        ):
            await self._process(result("invalid_auth", 401))
            await self._process(result("invalid_auth", 401))
            valid_outcome = await self._process(result("valid", 200))
            await self._process(result("invalid_auth", 403))

        self.assertEqual(send_alert.await_count, 2)
        self.assertEqual(valid_outcome.state.status, "healthy")
        final_state = auth_state.load_sbis_auth_state(self.state_path)
        self.assertEqual(final_state.status, "alerted")
        self.assertEqual(final_state.last_http_status, 403)
        self.assertIsNotNone(final_state.last_valid_at)

    async def test_hourly_first_daily_does_not_duplicate(self) -> None:
        invalid = result("invalid_auth", 401)
        send_alert = AsyncMock()

        with (
            patch.object(
                auth_monitor,
                "send_sbis_auth_alert",
                new=send_alert,
            ),
            patch.object(
                cookie_manager,
                "check_sbis_browser_cookie",
                new=AsyncMock(return_value=invalid),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exit_code = await cookie_manager.run_check(
                alert=True,
                state_path=self.state_path,
            )
            with patch.object(
                daily_run,
                "check_sbis_browser_cookie",
                new=AsyncMock(return_value=invalid),
            ):
                with self.assertRaises(
                    daily_run.SbisAuthPreflightError,
                ):
                    await daily_run.run_sbis_auth_preflight(
                        state_path=self.state_path,
                    )

        self.assertEqual(exit_code, 2)
        send_alert.assert_awaited_once()

    async def test_daily_first_hourly_does_not_duplicate(self) -> None:
        invalid = result("invalid_auth", 403)
        send_alert = AsyncMock()

        with (
            patch.object(
                auth_monitor,
                "send_sbis_auth_alert",
                new=send_alert,
            ),
            patch.object(
                daily_run,
                "check_sbis_browser_cookie",
                new=AsyncMock(return_value=invalid),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(daily_run.SbisAuthPreflightError):
                await daily_run.run_sbis_auth_preflight(
                    state_path=self.state_path,
                )

            with patch.object(
                cookie_manager,
                "check_sbis_browser_cookie",
                new=AsyncMock(return_value=invalid),
            ):
                exit_code = await cookie_manager.run_check(
                    alert=True,
                    state_path=self.state_path,
                )

        self.assertEqual(exit_code, 2)
        send_alert.assert_awaited_once()

    async def test_valid_hourly_check_resets_alerted_state(self) -> None:
        send_alert = AsyncMock()
        with patch.object(
            auth_monitor,
            "send_sbis_auth_alert",
            new=send_alert,
        ):
            await self._process(result("invalid_auth", 401))

        valid = result("valid", 200)
        with (
            patch.object(
                cookie_manager,
                "check_sbis_browser_cookie",
                new=AsyncMock(return_value=valid),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exit_code = await cookie_manager.run_check(
                alert=True,
                state_path=self.state_path,
            )

        self.assertEqual(exit_code, 0)
        state = auth_state.load_sbis_auth_state(self.state_path)
        self.assertEqual(state.status, "healthy")
        self.assertIsNotNone(state.last_valid_at)

    async def test_successful_manual_update_resets_state(self) -> None:
        env_path = self.directory / ".env"
        env_path.write_text(
            "OTHER=value\nSBIS_BROWSER_COOKIE='old'\n",
            encoding="utf-8",
        )
        send_alert = AsyncMock()
        with patch.object(
            auth_monitor,
            "send_sbis_auth_alert",
            new=send_alert,
        ):
            await self._process(result("invalid_auth", 401))

        with patch.object(
            cookie_manager,
            "check_sbis_browser_cookie",
            new=AsyncMock(return_value=result("valid", 200)),
        ):
            update = await cookie_manager.validate_and_replace_cookie(
                "new-secret-value",
                target_path=env_path,
                state_path=self.state_path,
            )

        self.assertTrue(update.updated)
        state = auth_state.load_sbis_auth_state(self.state_path)
        self.assertEqual(state.status, "healthy")
        self.assertIsNotNone(state.last_valid_at)

    async def test_network_error_does_not_alert_or_change_episode(self) -> None:
        send_alert = AsyncMock()
        network_error = result("network_server_error", 503)
        with patch.object(
            auth_monitor,
            "send_sbis_auth_alert",
            new=send_alert,
        ):
            healthy_outcome = await self._process(network_error)
            await self._process(result("invalid_auth", 401))
            alerted_outcome = await self._process(network_error)

        self.assertEqual(send_alert.await_count, 1)
        self.assertEqual(healthy_outcome.state.status, "healthy")
        self.assertEqual(alerted_outcome.state.status, "alerted")
        self.assertEqual(alerted_outcome.state.last_http_status, 503)

    async def test_corrupt_state_recovers_and_first_invalid_alerts(self) -> None:
        self.state_path.write_text(
            "{not-valid-json",
            encoding="utf-8",
        )
        send_alert = AsyncMock()
        with patch.object(
            auth_monitor,
            "send_sbis_auth_alert",
            new=send_alert,
        ):
            outcome = await self._process(
                result("invalid_auth", 401),
            )

        self.assertTrue(outcome.alert_sent)
        send_alert.assert_awaited_once()
        payload = json.loads(
            self.state_path.read_text(encoding="utf-8"),
        )
        self.assertEqual(payload["status"], "alerted")

    async def test_state_contains_only_safe_fields_and_no_cookie(self) -> None:
        send_alert = AsyncMock()
        with patch.object(
            auth_monitor,
            "send_sbis_auth_alert",
            new=send_alert,
        ):
            await self._process(result("invalid_auth", 401))

        raw_state = self.state_path.read_text(encoding="utf-8")
        payload = json.loads(raw_state)
        self.assertEqual(
            set(payload),
            {
                "status",
                "last_check_at",
                "last_invalid_at",
                "last_valid_at",
                "last_http_status",
            },
        )
        self.assertNotIn("cookie", raw_state.casefold())
        self.assertEqual(
            list(self.directory.glob("*.tmp")),
            [],
        )

    async def test_alert_error_does_not_replace_daily_auth_failure(self) -> None:
        invalid = result("invalid_auth", 401)
        send_alert = AsyncMock(
            side_effect=RuntimeError("simulated smtp failure"),
        )
        output = io.StringIO()
        with (
            patch.object(
                auth_monitor,
                "send_sbis_auth_alert",
                new=send_alert,
            ),
            patch.object(
                daily_run,
                "check_sbis_browser_cookie",
                new=AsyncMock(return_value=invalid),
            ),
            contextlib.redirect_stdout(output),
        ):
            with self.assertRaises(
                daily_run.SbisAuthPreflightError,
            ) as captured:
                await daily_run.run_sbis_auth_preflight(
                    state_path=self.state_path,
                )

        self.assertIs(captured.exception.result, invalid)
        self.assertIn("исходная ошибка авторизации", output.getvalue())
        self.assertEqual(
            auth_state.load_sbis_auth_state(self.state_path).status,
            "alerted",
        )

    async def test_concurrent_invalid_claims_only_one_alert(self) -> None:
        alert_started = asyncio.Event()
        release_alert = asyncio.Event()
        calls = 0

        async def delayed_alert(
            check_result: auth.SbisAuthCheckResult,
        ) -> None:
            nonlocal calls
            calls += 1
            alert_started.set()
            await release_alert.wait()

        invalid = result("invalid_auth", 401)
        with patch.object(
            auth_monitor,
            "send_sbis_auth_alert",
            new=delayed_alert,
        ):
            first = asyncio.create_task(self._process(invalid))
            await alert_started.wait()
            second = asyncio.create_task(self._process(invalid))
            await asyncio.sleep(0)
            release_alert.set()
            outcomes = await asyncio.gather(first, second)

        self.assertEqual(calls, 1)
        self.assertEqual(
            sum(item.alert_sent for item in outcomes),
            1,
        )
        self.assertEqual(
            sum(item.alert_suppressed for item in outcomes),
            1,
        )


class SbisAuthSystemdUnitsTestCase(unittest.TestCase):
    def test_hourly_service_and_timer_contract(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        service = (
            project_root
            / "deploy"
            / "projectsbis-sbis-auth-check.service"
        ).read_text(encoding="utf-8")
        timer = (
            project_root
            / "deploy"
            / "projectsbis-sbis-auth-check.timer"
        ).read_text(encoding="utf-8")

        self.assertIn("Type=oneshot", service)
        self.assertIn(
            "EnvironmentFile=/etc/projectsbis/projectsbis.env",
            service,
        )
        self.assertIn(
            "ExecStart=/opt/projectsbis/repository/.venv/bin/python "
            "-m src.sbis.cookie_manager --check --alert",
            service,
        )
        self.assertIn("OnCalendar=hourly", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn(
            "Unit=projectsbis-sbis-auth-check.service",
            timer,
        )
        self.assertIn("WantedBy=timers.target", timer)


if __name__ == "__main__":
    unittest.main()
