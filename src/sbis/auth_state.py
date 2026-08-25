"""
Межпроцессное состояние эпизода отказа браузерной авторизации СБИС.

State-файл содержит только статусы, HTTP-код и timestamps. Cookie, HTTP
headers и ответы СБИС в него не записываются. Все read-modify-write операции
защищены OS-level advisory lock, а JSON публикуется атомарным os.replace().
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import BinaryIO, Iterator, Literal

from src.config import SBIS_AUTH_STATE_FILE
from src.sbis.auth import SbisAuthCheckResult


if os.name == "posix":
    import fcntl
elif os.name == "nt":
    import msvcrt


SbisAuthEpisodeStatus = Literal["healthy", "alerted"]


@dataclass(slots=True)
class SbisAuthState:
    """Безопасное сериализуемое состояние auth-monitoring."""

    status: SbisAuthEpisodeStatus = "healthy"
    last_check_at: str | None = None
    last_invalid_at: str | None = None
    last_valid_at: str | None = None
    last_http_status: int | None = None


def get_sbis_auth_state_path() -> Path:
    """Вернуть абсолютный configured path общего auth-state."""
    return SBIS_AUTH_STATE_FILE.absolute()


def get_sbis_auth_state_lock_path(
    state_path: Path,
) -> Path:
    """Получить соседний lock-файл для одной state machine."""
    return Path(f"{state_path.absolute()}.lock")


def _acquire_state_lock(lock_file: BinaryIO) -> None:
    """Блокирующе захватить advisory lock текущей ОС."""
    if os.name == "posix":
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_EX,
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
            msvcrt.LK_LOCK,
            1,
        )
        return

    raise RuntimeError(
        f"ОС {os.name!r} не поддерживается SBIS auth-state lock"
    )


def _release_state_lock(lock_file: BinaryIO) -> None:
    """Освободить advisory lock состояния."""
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
        f"ОС {os.name!r} не поддерживается SBIS auth-state lock"
    )


@contextmanager
def sbis_auth_state_lock(
    state_path: Path | None = None,
) -> Iterator[Path]:
    """Удерживать общий межпроцессный lock для state read-modify-write."""
    resolved_state_path = (
        state_path.absolute()
        if state_path is not None
        else get_sbis_auth_state_path()
    )
    lock_path = get_sbis_auth_state_lock_path(
        resolved_state_path,
    )
    lock_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    if lock_path.is_symlink():
        raise RuntimeError(
            "Отказ от SBIS auth-state lock: путь является symlink"
        )

    with lock_path.open("a+b") as lock_file:
        acquired = False
        try:
            _acquire_state_lock(lock_file)
            acquired = True
            yield resolved_state_path
        finally:
            if acquired:
                _release_state_lock(lock_file)


def _optional_timestamp(value: object) -> str | None:
    """Принять только непустой timestamp-строку из JSON."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_http_status(value: object) -> int | None:
    """Принять только безопасный целочисленный HTTP status."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 100 <= value <= 599:
        return value
    return None


def load_sbis_auth_state(
    state_path: Path,
) -> SbisAuthState:
    """
    Прочитать state; отсутствие, повреждение или неверная схема → HEALTHY.

    HEALTHY для повреждённого файла гарантирует, что первая подтверждённая
    invalid-проверка не будет молча подавлена.
    """
    path = state_path.absolute()
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
        )
    except (
        FileNotFoundError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        return SbisAuthState()

    if not isinstance(payload, dict):
        return SbisAuthState()

    status = payload.get("status")
    if status not in {"healthy", "alerted"}:
        return SbisAuthState()

    return SbisAuthState(
        status=status,
        last_check_at=_optional_timestamp(
            payload.get("last_check_at"),
        ),
        last_invalid_at=_optional_timestamp(
            payload.get("last_invalid_at"),
        ),
        last_valid_at=_optional_timestamp(
            payload.get("last_valid_at"),
        ),
        last_http_status=_optional_http_status(
            payload.get("last_http_status"),
        ),
    )


def save_sbis_auth_state(
    state_path: Path,
    state: SbisAuthState,
) -> None:
    """Атомарно сохранить state JSON без секретных полей."""
    path = state_path.absolute()
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    if path.is_symlink():
        raise RuntimeError(
            "Отказ от записи SBIS auth-state: путь является symlink"
        )

    content = (
        json.dumps(
            asdict(state),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    original_stat = path.stat() if path.exists() else None
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
        else:
            os.chmod(temporary_path, 0o640)

        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def apply_auth_check_to_state(
    state: SbisAuthState,
    result: SbisAuthCheckResult,
    *,
    arm_alert: bool,
) -> bool:
    """
    Применить результат к state и вернуть необходимость одного alert.

    ``arm_alert`` используется daily_run и ``--check --alert``. Обычный
    ``--check`` обновляет timestamps и сбрасывает HEALTHY при valid, но сам
    не переводит новый invalid-эпизод в ALERTED.
    """
    checked_at = result.checked_at.isoformat(
        timespec="seconds",
    )
    state.last_check_at = checked_at
    state.last_http_status = result.http_status

    if result.is_valid:
        state.status = "healthy"
        state.last_valid_at = checked_at
        return False

    if result.is_invalid_auth:
        state.last_invalid_at = checked_at
        should_alert = (
            arm_alert
            and state.status == "healthy"
        )
        if should_alert:
            # Claim эпизода фиксируется до SMTP, поэтому параллельный daily
            # или hourly процесс уже не сможет отправить второе письмо.
            state.status = "alerted"
        return should_alert

    # Network/server error не меняет HEALTHY/ALERTED и не считается новым
    # эпизодом истечения cookie.
    return False


def update_sbis_auth_state(
    result: SbisAuthCheckResult,
    *,
    arm_alert: bool,
    state_path: Path | None = None,
) -> tuple[SbisAuthState, bool]:
    """Под lock атомарно применить результат и вернуть alert decision."""
    with sbis_auth_state_lock(state_path) as locked_path:
        state = load_sbis_auth_state(locked_path)
        should_alert = apply_auth_check_to_state(
            state,
            result,
            arm_alert=arm_alert,
        )
        save_sbis_auth_state(
            locked_path,
            state,
        )
    return state, should_alert


def mark_sbis_auth_healthy(
    result: SbisAuthCheckResult,
    *,
    state_path: Path | None = None,
) -> SbisAuthState:
    """Сбросить episode в HEALTHY после подтверждённой ручной замены."""
    if not result.is_valid:
        raise ValueError(
            "В HEALTHY можно перевести только результат valid"
        )
    state, _ = update_sbis_auth_state(
        result,
        arm_alert=False,
        state_path=state_path,
    )
    return state
