"""
CLI для проверки и безопасной замены SBIS_BROWSER_COOKIE.

Проверка:
    python -m src.sbis.cookie_manager --check

Интерактивная замена на VPS:
    sudo .venv/bin/python -m src.sbis.cookie_manager

Cookie вводится через getpass, проверяется до записи и никогда не выводится.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.config import (
    LOCAL_ENV_FILE,
    PRODUCTION_ENV_FILE,
)
from src.sbis.auth import (
    SbisAuthCheckResult,
    check_sbis_browser_cookie,
)


COOKIE_KEY = "SBIS_BROWSER_COOKIE"
COOKIE_LINE_PATTERN = re.compile(
    r"^(?P<prefix>[ \t]*(?:export[ \t]+)?"
    r"SBIS_BROWSER_COOKIE[ \t]*=[ \t]*).*$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class CookieUpdateResult:
    """Результат проверки и возможной записи новой cookie."""

    auth: SbisAuthCheckResult
    target_path: Path
    backup_path: Path | None = None
    updated: bool = False


def get_cookie_env_path() -> Path:
    """Выбрать production env при наличии, иначе корневой локальный .env."""
    if PRODUCTION_ENV_FILE.is_file():
        return PRODUCTION_ENV_FILE
    return LOCAL_ENV_FILE


def _quote_dotenv_value(value: str) -> str:
    """Закодировать секрет как одинарно-кавычечное значение python-dotenv."""
    if "\n" in value or "\r" in value:
        raise ValueError(
            "SBIS_BROWSER_COOKIE не может содержать перевод строки"
        )
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _replace_cookie_line(
    source: str,
    cookie: str,
) -> str:
    """Заменить только assignment целевой переменной, сохранив остальной env."""
    formatted = _quote_dotenv_value(cookie)
    matches = list(COOKIE_LINE_PATTERN.finditer(source))

    if matches:
        # Заменяются все активные определения одного ключа, чтобы более поздний
        # дубликат не продолжил перекрывать проверенное значение.
        return COOKIE_LINE_PATTERN.sub(
            lambda match: f"{match.group('prefix')}{formatted}",
            source,
        )

    newline = "\r\n" if "\r\n" in source else "\n"
    separator = "" if not source or source.endswith(("\n", "\r")) else newline
    return (
        f"{source}{separator}{COOKIE_KEY}={formatted}{newline}"
    )


def _atomic_write_env(
    path: Path,
    content: bytes,
) -> Path | None:
    """Атомарно записать env, сохранив mode/owner и предварительный backup."""
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_symlink():
        raise RuntimeError(
            "Отказ от замены env: целевой путь является symlink"
        )

    original_stat = path.stat() if path.exists() else None
    backup_path: Path | None = None
    temporary_path: Path | None = None

    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)

        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        if original_stat is not None:
            os.chmod(
                temporary_path,
                stat.S_IMODE(original_stat.st_mode),
            )
            if hasattr(os, "chown"):
                os.chown(
                    temporary_path,
                    original_stat.st_uid,
                    original_stat.st_gid,
                )

            backup_path = Path(f"{path}.bak")
            backup_number = 1
            while os.path.lexists(backup_path):
                backup_path = Path(
                    f"{path}.bak.{backup_number}"
                )
                backup_number += 1
            shutil.copy2(path, backup_path)
            if hasattr(os, "chown"):
                os.chown(
                    backup_path,
                    original_stat.st_uid,
                    original_stat.st_gid,
                )
        else:
            os.chmod(temporary_path, 0o600)

        os.replace(temporary_path, path)
        temporary_path = None
        return backup_path
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def replace_sbis_browser_cookie(
    path: Path,
    cookie: str,
) -> Path | None:
    """Заменить только SBIS_BROWSER_COOKIE в указанном env-файле."""
    target_path = path.absolute()
    if target_path.exists():
        source_bytes = target_path.read_bytes()
        try:
            source = source_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError(
                "Env-файл должен быть в кодировке UTF-8"
            ) from error
    else:
        source = ""

    updated = _replace_cookie_line(source, cookie)
    return _atomic_write_env(
        target_path,
        updated.encode("utf-8"),
    )


async def validate_and_replace_cookie(
    cookie: str,
    *,
    target_path: Path | None = None,
) -> CookieUpdateResult:
    """Проверить новую cookie и записать её только при статусе valid."""
    path = (
        target_path.absolute()
        if target_path is not None
        else get_cookie_env_path().absolute()
    )
    clean_cookie = cookie.strip()
    auth_result = await check_sbis_browser_cookie(clean_cookie)
    if not auth_result.is_valid:
        return CookieUpdateResult(
            auth=auth_result,
            target_path=path,
        )

    backup_path = replace_sbis_browser_cookie(
        path,
        clean_cookie,
    )
    return CookieUpdateResult(
        auth=auth_result,
        target_path=path,
        backup_path=backup_path,
        updated=True,
    )


def _format_check_result(result: SbisAuthCheckResult) -> str:
    """Сформировать CLI-строку без cookie и тела ответа СБИС."""
    if result.is_valid:
        return "SBIS_BROWSER_COOKIE: OK"

    status_suffix = (
        f" (HTTP {result.http_status})"
        if result.http_status is not None
        else ""
    )
    if result.is_invalid_auth:
        return f"SBIS_BROWSER_COOKIE: INVALID{status_suffix}"
    return f"SBIS_BROWSER_COOKIE: ERROR{status_suffix}"


def _exit_code(result: SbisAuthCheckResult) -> int:
    """Вернуть 0/2/1 для valid/invalid/network-server результата."""
    if result.is_valid:
        return 0
    if result.is_invalid_auth:
        return 2
    return 1


async def run_check() -> int:
    """Проверить текущую cookie и вернуть документированный exit code."""
    result = await check_sbis_browser_cookie()
    print(_format_check_result(result))
    return _exit_code(result)


async def run_interactive_update() -> int:
    """Безопасно запросить, проверить и записать новую cookie."""
    target_path = get_cookie_env_path().absolute()
    print(f"Целевой env-файл: {target_path}")
    cookie = getpass.getpass(
        "Введите новую SBIS_BROWSER_COOKIE: "
    )

    if not cookie.strip():
        print("SBIS_BROWSER_COOKIE: INVALID (пустое значение)")
        return 2

    result = await validate_and_replace_cookie(
        cookie,
        target_path=target_path,
    )
    print(_format_check_result(result.auth))

    if not result.updated:
        print("Env-файл не изменён.")
        return _exit_code(result.auth)

    print(f"SBIS_BROWSER_COOKIE обновлена в: {result.target_path}")
    if result.backup_path is not None:
        print(f"Backup: {result.backup_path}")
    return 0


def parse_arguments() -> argparse.Namespace:
    """Разобрать единственный безопасный режим --check."""
    parser = argparse.ArgumentParser(
        description="Проверить или заменить SBIS_BROWSER_COOKIE.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Только проверить текущую cookie, не изменяя env.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI-точка входа cookie manager."""
    arguments = parse_arguments()
    exit_code = asyncio.run(
        run_check()
        if arguments.check
        else run_interactive_update()
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
