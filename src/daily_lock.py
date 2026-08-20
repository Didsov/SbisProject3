"""Межпроцессная блокировка ежедневного сценария ProjectSbis."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
from typing import BinaryIO, Iterator


PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DAILY_RUN_LOCK_PATH = (
    PROJECT_ROOT / "data" / "daily_run.lock"
)

DAILY_RUN_LOCK_PATH_ENV = (
    "PROJECTSBIS_DAILY_RUN_LOCK_PATH"
)


if os.name == "posix":
    import fcntl
elif os.name == "nt":
    import msvcrt


class DailyRunAlreadyRunningError(RuntimeError):
    """Другой процесс уже удерживает блокировку daily-run."""


def get_daily_run_lock_path() -> Path:
    """Получить путь lock-файла с возможностью явного переопределения."""
    configured_path = os.getenv(
        DAILY_RUN_LOCK_PATH_ENV,
        "",
    ).strip()

    if configured_path:
        return Path(configured_path)

    return DEFAULT_DAILY_RUN_LOCK_PATH


def _acquire_os_lock(lock_file: BinaryIO) -> None:
    """Неблокирующе захватить advisory lock текущей ОС."""
    try:
        if os.name == "posix":
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
            return

        if os.name == "nt":
            lock_file.seek(0, os.SEEK_END)

            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()

            lock_file.seek(0)
            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
            return

        raise RuntimeError(
            f"ОС {os.name!r} не поддерживается daily-run lock"
        )
    except OSError as error:
        if error.errno in {
            errno.EACCES,
            errno.EAGAIN,
            errno.EDEADLK,
        }:
            raise DailyRunAlreadyRunningError(
                "Daily run уже выполняется другим процессом."
            ) from error

        raise


def _release_os_lock(lock_file: BinaryIO) -> None:
    """Освободить ранее полученный advisory lock."""
    if os.name == "posix":
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_UN,
        )
        return

    if os.name == "nt":
        lock_file.seek(0)
        msvcrt.locking(
            lock_file.fileno(),
            msvcrt.LK_UNLCK,
            1,
        )
        return

    raise RuntimeError(
        f"ОС {os.name!r} не поддерживается daily-run lock"
    )


@contextmanager
def daily_run_lock(
    lock_path: Path | None = None,
) -> Iterator[Path]:
    """Удерживать OS-level блокировку в течение всего daily-run."""
    resolved_path = (
        lock_path
        if lock_path is not None
        else get_daily_run_lock_path()
    )
    resolved_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with resolved_path.open("a+b") as lock_file:
        acquired = False

        try:
            _acquire_os_lock(lock_file)
            acquired = True
            yield resolved_path
        finally:
            if acquired:
                _release_os_lock(lock_file)
