from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, call, patch

import aiohttp

from src import client_loader


class ClientLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_skip_collection_enriches_existing_selection(self) -> None:
        with (
            patch.object(client_loader, "initialize_database"),
            patch.object(client_loader, "get_all_clients", new=AsyncMock()) as get_all,
            patch.object(client_loader, "upsert_clients") as upsert,
            patch.object(client_loader, "get_unenriched_clients", return_value=[]),
            patch.object(
                client_loader,
                "enrich_selection_clients",
                new=AsyncMock(),
            ) as enrich,
            redirect_stdout(io.StringIO()),
        ):
            await client_loader.run(
                selection_id=5984,
                inn=None,
                inn_file=None,
                enrich_limit=0,
                enrich_all=True,
                skip_collection=True,
            )

        get_all.assert_not_awaited()
        upsert.assert_not_called()
        enrich.assert_awaited_once_with(
            selection_id=5984,
            enrich_limit=0,
            enrich_all=True,
        )

    async def test_network_operation_retries_after_one_and_two_minutes(self) -> None:
        operation = AsyncMock(
            side_effect=[
                aiohttp.ClientConnectionError(),
                aiohttp.ClientConnectionError(),
                "ok",
            ]
        )
        with (
            patch.object(
                client_loader.asyncio,
                "sleep",
                new=AsyncMock(),
            ) as sleep_mock,
            redirect_stdout(io.StringIO()) as output,
        ):
            result = await client_loader.request_with_network_retries(
                operation,
                "argument",
                keyword="value",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(operation.await_count, 3)
        operation.assert_has_awaits(
            [
                call("argument", keyword="value"),
                call("argument", keyword="value"),
                call("argument", keyword="value"),
            ]
        )
        self.assertEqual(
            [call.args[0] for call in sleep_mock.await_args_list],
            [60, 120],
        )
        self.assertIn("Соединение с интернетом потеряно", output.getvalue())

    async def test_cli_stops_after_third_connection_failure(self) -> None:
        arguments = argparse.Namespace(
            selection=5984,
            inn=None,
            inn_file=None,
            enrich_limit=0,
            enrich_all=True,
            skip_collection=True,
        )

        with (
            patch.object(
                client_loader,
                "run",
                new=AsyncMock(side_effect=aiohttp.ClientConnectionError()),
            ) as run_mock,
            patch.object(
                client_loader.asyncio,
                "sleep",
                new=AsyncMock(),
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            completed = await client_loader.run_cli(arguments)

        self.assertFalse(completed)
        self.assertEqual(run_mock.await_count, 1)
        self.assertIn("после двух повторных попыток", output.getvalue())

    async def test_network_operation_raises_after_third_failure(self) -> None:
        operation = AsyncMock(side_effect=aiohttp.ClientConnectionError())

        with (
            patch.object(
                client_loader.asyncio,
                "sleep",
                new=AsyncMock(),
            ) as sleep_mock,
            redirect_stdout(io.StringIO()),
            self.assertRaises(aiohttp.ClientConnectionError),
        ):
            await client_loader.request_with_network_retries(operation)

        self.assertEqual(operation.await_count, 3)
        self.assertEqual(sleep_mock.await_count, 2)


class ClientLoaderArgumentTests(unittest.TestCase):
    def test_skip_collection_requires_enrichment_mode(self) -> None:
        with (
            patch(
                "sys.argv",
                [
                    "client_loader",
                    "--selection",
                    "5984",
                    "--skip-collection",
                ],
            ),
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            client_loader.parse_arguments()

    def test_skip_collection_accepts_existing_selection_enrichment(self) -> None:
        with patch(
            "sys.argv",
            [
                "client_loader",
                "--selection",
                "5984",
                "--skip-collection",
                "--enrich-all",
            ],
        ):
            arguments = client_loader.parse_arguments()

        self.assertTrue(arguments.skip_collection)
        self.assertTrue(arguments.enrich_all)
        self.assertEqual(arguments.selection, 5984)


if __name__ == "__main__":
    unittest.main()
