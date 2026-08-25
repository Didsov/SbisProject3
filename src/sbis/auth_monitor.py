"""Общая orchestration state machine для hourly check и daily_run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.sbis.auth import SbisAuthCheckResult
from src.sbis.auth_alert import send_sbis_auth_alert
from src.sbis.auth_state import (
    SbisAuthState,
    update_sbis_auth_state,
)


@dataclass(frozen=True, slots=True)
class SbisAuthMonitorOutcome:
    """Итог state-перехода и возможной одноразовой alert-попытки."""

    state: SbisAuthState
    alert_required: bool = False
    alert_sent: bool = False
    alert_suppressed: bool = False
    alert_error_type: str | None = None
    state_error_type: str | None = None


async def process_sbis_auth_result(
    result: SbisAuthCheckResult,
    *,
    alert_on_invalid: bool,
    state_path: Path | None = None,
) -> SbisAuthMonitorOutcome:
    """
    Обновить общий state и при первом invalid отправить ровно один alert.

    Переход в ALERTED сохраняется до SMTP-вызова. Это является общим claim
    эпизода для hourly и daily процессов и исключает конкурентный дубль.
    Ошибка alert возвращается как metadata и не заменяет auth-result.
    """
    try:
        state, should_alert = update_sbis_auth_state(
            result,
            arm_alert=alert_on_invalid,
            state_path=state_path,
        )
    except Exception as error:
        # Без надёжного общего state нельзя безопасно гарантировать отсутствие
        # дублей, поэтому email не отправляется. Auth failure остаётся у caller.
        return SbisAuthMonitorOutcome(
            state=SbisAuthState(),
            state_error_type=type(error).__name__,
        )

    suppressed = (
        alert_on_invalid
        and result.is_invalid_auth
        and not should_alert
        and state.status == "alerted"
    )
    if not should_alert:
        return SbisAuthMonitorOutcome(
            state=state,
            alert_suppressed=suppressed,
        )

    try:
        send_result = await send_sbis_auth_alert(result)
    except Exception as error:
        return SbisAuthMonitorOutcome(
            state=state,
            alert_required=True,
            alert_error_type=type(error).__name__,
        )

    return SbisAuthMonitorOutcome(
        state=state,
        alert_required=True,
        # Старые/тестовые callback без return считаются успешными. Только
        # явный False означает, что адреса не были настроены и письма не было.
        alert_sent=(send_result is not False),
    )
