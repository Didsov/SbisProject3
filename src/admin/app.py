"""Read-only HTTP-админка ProjectSbis."""

from __future__ import annotations

from datetime import datetime
from html import escape
import json
from typing import Iterable, Mapping, Sequence

from aiohttp import web

from src.database import (
    get_latest_mail_run_with_sent_messages,
    get_mail_message_details,
    get_mail_message_timeline,
    get_mail_run_details,
    get_mail_run_events,
    get_mail_run_messages,
    get_recent_mail_runs,
)


ADMIN_HOST = "127.0.0.1"
ADMIN_PORT = 8081
ADMIN_URL = f"http://{ADMIN_HOST}:{ADMIN_PORT}/admin"
RECENT_RUNS_LIMIT = 100
RECENT_EVENTS_LIMIT = 100


PAGE_STYLE = """
:root {
    color-scheme: light;
    font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #1c2434;
    background: #f2f5f9;
}
* { box-sizing: border-box; }
body { margin: 0; background: #f2f5f9; }
a { color: #175cd3; text-decoration: none; }
a:hover { color: #0b4ab8; }
.page { width: min(1440px, calc(100% - 40px)); margin: 0 auto; padding: 22px 0 52px; }
.topbar {
    display: flex; align-items: center; justify-content: space-between; gap: 24px;
    margin-bottom: 34px; padding: 12px 16px; border: 1px solid #e0e6ef;
    border-radius: 14px; background: rgba(255, 255, 255, .92);
    box-shadow: 0 8px 28px rgba(26, 39, 66, .06);
}
.brand { display: flex; align-items: center; gap: 10px; color: #172033; font-size: 18px; font-weight: 760; }
.brand-mark {
    display: grid; width: 32px; height: 32px; place-items: center;
    border-radius: 9px; background: #175cd3; color: #fff; font-size: 14px;
}
.nav { display: flex; align-items: center; gap: 6px; }
.nav a { padding: 8px 11px; border-radius: 8px; color: #475467; font-size: 14px; font-weight: 650; }
.nav a:hover { background: #eef4ff; color: #175cd3; }
.page-heading { margin-bottom: 22px; }
.eyebrow { margin-bottom: 6px; color: #667085; font-size: 12px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
h1 { margin: 0; color: #172033; font-size: clamp(26px, 4vw, 34px); line-height: 1.2; }
h2 { margin: 32px 0 14px; color: #273142; font-size: 20px; }
.subtitle { max-width: 760px; margin: 8px 0 0; color: #667085; font-size: 14px; line-height: 1.5; }
.muted { color: #98a2b3; }
.panel {
    padding: 22px; border: 1px solid #e1e7ef; border-radius: 14px;
    background: #fff; box-shadow: 0 8px 26px rgba(26, 39, 66, .055);
}
.metrics { display: grid; grid-template-columns: repeat(6, minmax(130px, 1fr)); gap: 12px; }
.metric {
    min-width: 0; padding: 16px; border: 1px solid #e4e9f1; border-radius: 12px;
    background: linear-gradient(145deg, #fff 0%, #f9fbfd 100%);
}
.metric-label { min-height: 34px; margin-bottom: 8px; color: #667085; font-size: 12px; line-height: 1.35; }
.metric-value { color: #172033; font-size: clamp(22px, 3vw, 28px); font-weight: 760; overflow-wrap: anywhere; }
.run-card { margin-top: 16px; }
.run-card-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.run-card-title { margin: 0; color: #344054; font-size: 15px; font-weight: 730; }
.details-grid { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 18px 24px; }
.detail-label { margin-bottom: 5px; color: #667085; font-size: 12px; }
.detail-value { color: #273142; font-size: 14px; font-weight: 650; overflow-wrap: anywhere; }
.text-link { font-size: 14px; font-weight: 700; white-space: nowrap; }
.status {
    display: inline-flex; align-items: center; min-height: 26px; padding: 4px 9px;
    border-radius: 999px; background: #eef2f6; color: #475467;
    font-size: 12px; font-weight: 750; line-height: 1.2; white-space: nowrap;
}
.status-success, .status-sent, .status-delivered, .status-opened, .status-clicked { background: #dcfae6; color: #067647; }
.status-partial, .status-running, .status-deferred, .status-pending, .status-unknown { background: #fef0c7; color: #93370d; }
.status-failed, .status-bounced { background: #fee4e2; color: #b42318; }
.table-wrap {
    overflow-x: auto; border: 1px solid #e1e7ef; border-radius: 14px;
    background: #fff; box-shadow: 0 8px 26px rgba(26, 39, 66, .045);
}
table { width: 100%; border-collapse: collapse; }
th, td { padding: 13px 14px; border-bottom: 1px solid #eaecf0; text-align: left; vertical-align: middle; }
th { background: #f8fafc; color: #667085; font-size: 11px; font-weight: 750; letter-spacing: .025em; white-space: nowrap; }
td { color: #344054; font-size: 13px; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover { background: #f8fbff; }
.row-cell-link { display: block; margin: -13px -14px; padding: 13px 14px; color: inherit; }
.row-cell-link:hover { color: inherit; }
.numeric { font-variant-numeric: tabular-nums; }
.email { overflow-wrap: anywhere; }
.event-data summary { color: #175cd3; cursor: pointer; font-size: 12px; font-weight: 650; }
.event-data code {
    display: block; max-width: 520px; max-height: 160px; margin-top: 8px;
    padding: 10px; overflow: auto; border-radius: 8px; background: #f5f7fa;
    color: #344054; font-size: 11px; white-space: pre-wrap; overflow-wrap: anywhere;
}
.empty { padding: 40px 24px; text-align: center; color: #667085; }
@media (max-width: 1100px) {
    .metrics { grid-template-columns: repeat(3, 1fr); }
    .details-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 720px) {
    .page { width: min(100% - 20px, 1440px); padding-top: 10px; }
    .topbar { align-items: flex-start; flex-direction: column; gap: 10px; margin-bottom: 24px; }
    .nav { width: 100%; }
    .nav a { flex: 1; text-align: center; }
    .metrics { grid-template-columns: repeat(2, 1fr); }
    .details-grid { grid-template-columns: repeat(2, 1fr); }
    .run-card-header { align-items: flex-start; flex-direction: column; }
    .panel { padding: 16px; }
    .table-wrap { overflow: visible; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
    table, tbody { display: block; width: 100%; }
    thead { display: none; }
    tbody { display: grid; gap: 12px; }
    tr { display: block; overflow: hidden; border: 1px solid #e1e7ef; border-radius: 12px; background: #fff; box-shadow: 0 5px 18px rgba(26, 39, 66, .045); }
    td { display: grid; grid-template-columns: minmax(108px, 38%) minmax(0, 1fr); gap: 12px; align-items: start; padding: 10px 12px; }
    td::before { content: attr(data-label); color: #667085; font-size: 11px; font-weight: 720; }
    .row-cell-link { margin: -10px -12px; padding: 10px 12px; }
    .event-data code { max-width: 100%; }
}
@media (max-width: 420px) {
    .metrics { grid-template-columns: 1fr; }
    .details-grid { grid-template-columns: 1fr 1fr; gap: 15px; }
    td { grid-template-columns: 100px minmax(0, 1fr); }
}
"""


RUN_STATUSES = {
    "success": ("✓ Успешно", "success"),
    "partial": ("⚠ Частично", "partial"),
    "failed": ("✕ Ошибка", "failed"),
    "running": ("… Выполняется", "running"),
}
SEND_STATUSES = {
    "sent": ("✓", "sent"),
    "failed": ("✕", "failed"),
    "pending": ("…", "pending"),
}
MESSAGE_SEND_STATUSES = {
    "sent": ("✓ Отправлено", "sent"),
    "failed": ("✕ Ошибка SMTP", "failed"),
    "pending": ("… Ожидание", "pending"),
}
DELIVERY_STATUSES = {
    "delivered": ("✓ Принято", "delivered"),
    "bounced": ("✕ Bounce", "bounced"),
    "deferred": ("… Ожидание", "deferred"),
    "unknown": ("… Нет результата", "unknown"),
}
EVENT_STATUSES = {
    "sent": ("Отправлено", "sent"),
    "delivered": ("Принято сервером", "delivered"),
    "bounced": ("Bounce", "bounced"),
    "deferred": ("Временная ошибка", "deferred"),
    "opened": ("Открыто", "opened"),
    "clicked": ("Клик", "clicked"),
}
CLICK_CHANNEL_LABELS = {
    "phone": "Phone",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "max": "MAX",
    "cta_email": "Email",
}


def _value(value: object) -> str:
    """Безопасно подготовить значение БД для HTML."""
    if value is None or value == "":
        return '<span class="muted">—</span>'
    return escape(str(value))


def _badge(
    value: object,
    labels: dict[str, tuple[str, str]],
) -> str:
    """Отобразить локализованный статус с безопасным fallback."""
    status = str(value or "unknown")
    label, css_class = labels.get(status, (status, "unknown"))
    return (
        f'<span class="status status-{css_class}">'
        f"{escape(label)}</span>"
    )


def _run_status(value: object) -> str:
    return _badge(value, RUN_STATUSES)


def _is_empty_successful_run(
    run: Mapping[str, object],
) -> bool:
    """Определить успешную проверку без новых получателей и отправок."""
    return (
        run.get("status") == "success"
        and int(run.get("recipients_added") or 0) == 0
        and int(run.get("sent_count") or 0) == 0
        and int(run.get("failed_count") or 0) == 0
    )


def _display_run_status(
    run: Mapping[str, object],
) -> str:
    """Показать статус запуска без изменения значения в БД."""
    if _is_empty_successful_run(run):
        return (
            '<span class="status status-empty">'
            "○ Нет новых получателей"
            "</span>"
        )
    return _run_status(run.get("status"))


def _latest_check_text(
    run: Mapping[str, object],
) -> str:
    """Кратко описать последний фактический запуск для dashboard."""
    run_id = int(run["run_id"])
    if _is_empty_successful_run(run):
        return f"#{run_id} — новых получателей нет"
    return f"#{run_id} — запуск завершён"


def _send_status(value: object) -> str:
    return _badge(value, SEND_STATUSES)


def _message_send_status(value: object) -> str:
    return _badge(value, MESSAGE_SEND_STATUSES)


def _delivery_status(value: object) -> str:
    return _badge(value, DELIVERY_STATUSES)


def _event_status(value: object) -> str:
    return _badge(value, EVENT_STATUSES)


def _click_channel(event_data: object) -> str | None:
    """Извлечь известный канал клика из JSON event_data."""
    if isinstance(event_data, str):
        try:
            parsed_data = json.loads(event_data)
        except (TypeError, ValueError):
            return None
    elif isinstance(event_data, Mapping):
        parsed_data = event_data
    else:
        return None

    if not isinstance(parsed_data, Mapping):
        return None

    for field_name in ("click_key", "channel", "key"):
        raw_channel = parsed_data.get(field_name)
        channel = str(raw_channel or "").strip().lower()
        if channel in CLICK_CHANNEL_LABELS:
            return CLICK_CHANNEL_LABELS[channel]

    return None


def _message_event_status(
    event_type: object,
    event_data: object,
) -> str:
    """Отобразить событие сообщения с каналом распознанного клика."""
    if event_type == "clicked":
        channel = _click_channel(event_data)
        if channel is not None:
            return (
                '<span class="status status-clicked">'
                f"Клик · {escape(channel)}"
                "</span>"
            )

    return _event_status(event_type)


def _test_recipient_status(value: object) -> str:
    if bool(value):
        return '<span class="status status-partial">TEST RECIPIENT</span>'
    return '<span class="muted">Нет</span>'


def _click_channels(timeline: Sequence[Mapping[str, object]]) -> str:
    """Собрать каналы кликов сообщения без отдельной tracking-схемы."""
    channels = {
        channel
        for event in timeline
        if event["event_type"] == "clicked"
        if (channel := _click_channel(event["event_data"])) is not None
    }
    if not channels:
        return '<span class="muted">—</span>'
    return escape(", ".join(sorted(channels)))


def _duration(started_at: object, finished_at: object) -> str:
    """Вернуть человекочитаемую длительность завершённого запуска."""
    if not started_at or not finished_at:
        return "—"
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        total_seconds = int((finished - started).total_seconds())
    except (TypeError, ValueError):
        return "—"
    if total_seconds < 0:
        return "—"
    if total_seconds < 60:
        return f"{total_seconds} сек"
    hours, minutes = divmod(total_seconds // 60, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    return f"{minutes} мин"


def _page(*, title: str, content: str) -> str:
    """Собрать общую HTML-обвязку страницы."""
    safe_title = escape(title)
    return f"""<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{safe_title} — ProjectSbis</title>
    <style>{PAGE_STYLE}</style>
</head>
<body>
    <main class="page">
        <header class="topbar">
            <a class="brand" href="/admin">
                <span class="brand-mark">PS</span><span>ProjectSbis</span>
            </a>
            <nav class="nav" aria-label="Основная навигация">
                <a href="/admin">Сводка</a>
                <a href="/admin/runs">Запуски</a>
            </nav>
        </header>
        {content}
    </main>
</body>
</html>"""


def _response(*, title: str, content: str) -> web.Response:
    """Вернуть UTF-8 HTML-ответ."""
    return web.Response(
        text=_page(title=title, content=content),
        content_type="text/html",
        charset="utf-8",
    )


def _metrics(items: Iterable[tuple[str, object]]) -> str:
    """Собрать сетку основных числовых показателей."""
    cards = "".join(
        '<div class="metric">'
        f'<div class="metric-label">{escape(label)}</div>'
        f'<div class="metric-value numeric">{_value(value)}</div>'
        "</div>"
        for label, value in items
    )
    return f'<div class="metrics">{cards}</div>'


def _details(items: Iterable[tuple[str, str]]) -> str:
    """Собрать карточку с атрибутами запуска."""
    values = "".join(
        '<div class="detail">'
        f'<div class="detail-label">{escape(label)}</div>'
        f'<div class="detail-value">{value}</div>'
        "</div>"
        for label, value in items
    )
    return f'<div class="details-grid">{values}</div>'


def _table(
    *,
    headers: Sequence[str],
    rows: Iterable[tuple[Sequence[str], str | None]],
    empty_text: str,
) -> str:
    """Собрать таблицу, которая на узком экране становится карточками."""
    rendered_rows = list(rows)
    if not rendered_rows:
        return f'<div class="panel empty">{escape(empty_text)}</div>'

    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    rows_html: list[str] = []
    for cells, row_href in rendered_rows:
        safe_href = escape(row_href, quote=True) if row_href is not None else None
        cell_html: list[str] = []
        for header, cell in zip(headers, cells, strict=True):
            content = cell
            if safe_href is not None:
                content = f'<a class="row-cell-link" href="{safe_href}">{cell}</a>'
            cell_html.append(
                f'<td data-label="{escape(header, quote=True)}">{content}</td>'
            )
        rows_html.append("<tr>" + "".join(cell_html) + "</tr>")

    return (
        '<div class="table-wrap"><table>'
        f"<thead><tr>{header_html}</tr></thead>"
        f'<tbody>{"".join(rows_html)}</tbody>'
        "</table></div>"
    )


def _event_data(value: object) -> str:
    """Спрятать технические данные события под раскрываемым блоком."""
    if value is None or value == "":
        return '<span class="muted">—</span>'
    return (
        '<details class="event-data"><summary>Данные</summary>'
        f"<code>{escape(str(value))}</code></details>"
    )


async def handle_admin(request: web.Request) -> web.Response:
    """Показать dashboard последнего запуска."""
    del request
    runs = get_recent_mail_runs(limit=1)
    if not runs:
        content = """
        <div class="page-heading">
            <div class="eyebrow">Рассылки</div><h1>Последняя рассылка</h1>
        </div>
        <section class="panel empty">Запуски рассылки пока отсутствуют.</section>
        """
        return _response(title="Сводка", content=content)

    latest_run = runs[0]
    latest_run_id = int(latest_run["run_id"])
    latest_mailing = get_latest_mail_run_with_sent_messages()
    latest_check = (
        '<section class="panel run-card"><div class="run-card-header">'
        + '<div><h2 class="run-card-title">Последний запуск</h2>'
        + '<p class="subtitle">Последняя проверка: '
        + f"<strong>{escape(_latest_check_text(latest_run))}</strong>"
        + "</p></div>"
        + f'<a class="text-link" href="/admin/runs/{latest_run_id}">Открыть запуск →</a>'
        + "</div>"
        + _details(
            (
                ("Статус", _display_run_status(latest_run)),
                ("Начало", _value(latest_run["started_at"])),
                ("Окончание", _value(latest_run["finished_at"])),
                (
                    "Длительность",
                    escape(
                        _duration(
                            latest_run["started_at"],
                            latest_run["finished_at"],
                        )
                    ),
                ),
                ("Кампания", _value(latest_run["campaign_name"])),
                ("Семейство", _value(latest_run["campaign_family"])),
                ("Batch", _value(latest_run["batch_number"])),
                ("Selection", _value(latest_run["selection_id"])),
            )
        )
        + "</section>"
    )
    heading = (
        '<div class="page-heading"><div class="eyebrow">Рассылки</div>'
        '<h1>Последняя рассылка</h1>'
        '<p class="subtitle">Ключевые результаты последней ежедневной рассылки.</p></div>'
    )

    if latest_mailing is None:
        content = (
            heading
            + '<section class="panel empty">'
            + "Рассылок с отправленными сообщениями пока нет."
            + "</section>"
            + latest_check
        )
        return _response(title="Сводка", content=content)

    mailing_run_id = int(latest_mailing["run_id"])
    messages = get_mail_run_messages(mailing_run_id)
    opened_count = sum(
        int(message["opened_count"] or 0) > 0
        for message in messages
    )
    clicked_count = sum(
        int(message["clicked_count"] or 0) > 0
        for message in messages
    )
    open_count = sum(int(message["opened_count"] or 0) for message in messages)
    click_count = sum(int(message["clicked_count"] or 0) for message in messages)
    mailing_duration = _duration(
        latest_mailing["started_at"],
        latest_mailing["finished_at"],
    )
    content = (
        heading
        + _metrics(
            (
                ("Подготовлено", latest_mailing["prepared_email_count"]),
                ("Pending", latest_mailing["pending_count"]),
                ("Отправлено", latest_mailing["sent_count"]),
                ("Принято сервером", latest_mailing["delivered_count"]),
                ("Bounce", latest_mailing["bounced_count"]),
                ("Открытия / уник.", f"{open_count} / {opened_count}"),
                ("Клики / уник.", f"{click_count} / {clicked_count}"),
            )
        )
        + '<section class="panel run-card"><div class="run-card-header">'
        + '<h2 class="run-card-title">Параметры рассылки</h2>'
        + f'<a class="text-link" href="/admin/runs/{mailing_run_id}">Подробнее о рассылке →</a>'
        + "</div>"
        + _details(
            (
                ("Статус", _display_run_status(latest_mailing)),
                ("Начало", _value(latest_mailing["started_at"])),
                ("Окончание", _value(latest_mailing["finished_at"])),
                ("Длительность", escape(mailing_duration)),
                ("Кампания", _value(latest_mailing["campaign_name"])),
                ("Семейство", _value(latest_mailing["campaign_family"])),
                ("Batch", _value(latest_mailing["batch_number"])),
                ("Selection", _value(latest_mailing["selection_id"])),
            )
        )
        + "</section>"
        + latest_check
    )
    return _response(title="Сводка", content=content)


async def handle_runs(request: web.Request) -> web.Response:
    """Показать последние запуски рассылки."""
    del request
    runs = get_recent_mail_runs(limit=RECENT_RUNS_LIMIT)
    table = _table(
        headers=(
            "Запуск",
            "Batch",
            "Семейство",
            "Кампания",
            "Начало",
            "ИНН",
            "Подготовлено",
            "Skipped",
            "Pending",
            "Отправлено",
            "Принято сервером",
            "Bounce",
            "Ошибки",
            "Открытия / уник.",
            "Клики / уник.",
            "Статус",
        ),
        rows=(
            (
                (
                    f'<strong class="numeric">#{_value(run["run_id"])}</strong>',
                    _value(run["batch_number"]),
                    _value(run["campaign_family"]),
                    _value(run["campaign_name"]),
                    _value(run["started_at"]),
                    _value(run["input_inns_count"]),
                    _value(run["prepared_email_count"]),
                    _value(run["skipped_count"]),
                    _value(run["pending_count"]),
                    _value(run["sent_count"]),
                    _value(run["delivered_count"]),
                    _value(run["bounced_count"]),
                    _value(run["failed_count"]),
                    f'{_value(run["open_count"])} / {_value(run["unique_open_count"])}',
                    f'{_value(run["click_count"])} / {_value(run["unique_click_count"])}',
                    _display_run_status(run),
                ),
                f'/admin/runs/{int(run["run_id"])}',
            )
            for run in runs
        ),
        empty_text="Запуски рассылки пока отсутствуют.",
    )
    content = (
        '<div class="page-heading"><div class="eyebrow">Рассылки</div>'
        '<h1>История запусков</h1>'
        '<p class="subtitle">Последние запуски и их итоговые показатели.</p></div>'
        + table
    )
    return _response(title="Запуски", content=content)


def _parse_run_id(request: web.Request) -> int:
    """Проверить run_id из маршрута."""
    raw_run_id = request.match_info.get("run_id", "")
    try:
        run_id = int(raw_run_id)
    except ValueError:
        raise web.HTTPBadRequest(
            text="Некорректный run_id.",
            content_type="text/plain",
        ) from None
    if run_id < 1:
        raise web.HTTPBadRequest(
            text="Некорректный run_id.",
            content_type="text/plain",
        )
    return run_id


def _parse_message_id(request: web.Request) -> int:
    """Проверить message_id из маршрута."""
    raw_message_id = request.match_info.get("message_id", "")
    try:
        message_id = int(raw_message_id)
    except ValueError:
        raise web.HTTPBadRequest(
            text="Некорректный message_id.",
            content_type="text/plain",
        ) from None
    if message_id < 1:
        raise web.HTTPBadRequest(
            text="Некорректный message_id.",
            content_type="text/plain",
        )
    return message_id


async def handle_run_details(request: web.Request) -> web.Response:
    """Показать запуск, его сообщения и последние события."""
    run_id = _parse_run_id(request)
    try:
        details = get_mail_run_details(run_id)
    except LookupError:
        raise web.HTTPNotFound(
            text="Запуск не найден.",
            content_type="text/plain",
        ) from None

    messages = get_mail_run_messages(run_id)
    events = get_mail_run_events(run_id)
    recent_events = list(reversed(events[-RECENT_EVENTS_LIMIT:]))

    messages_table = _table(
        headers=(
            "ИНН",
            "Компания",
            "Email клиента",
            "SMTP email",
            "Test recipient",
            "Статус message",
            "Delivery status",
            "Отправлено",
            "Доставлено",
            "Открытия",
            "Клики",
            "Последнее событие",
        ),
        rows=(
            (
                (
                    _value(message["inn"]),
                    (
                        '<a class="text-link" '
                        f'href="/admin/messages/{int(message["message_id"])}">'
                        f'{_value(message["company_name"])}</a>'
                    ),
                    f'<span class="email">{_value(message["email"])}</span>',
                    (
                        '<span class="email">'
                        f'{_value(message["smtp_recipient_email"])}</span>'
                    ),
                    _test_recipient_status(message["is_test_recipient"]),
                    _send_status(message["send_status"]),
                    _delivery_status(message["delivery_status"]),
                    _value(message["sent_at"]),
                    _value(message["delivered_at"]),
                    _value(message["opened_count"]),
                    _value(message["clicked_count"]),
                    _value(message["last_event_at"]),
                ),
                None,
            )
            for message in messages
        ),
        empty_text="У запуска нет боевых сообщений.",
    )
    events_table = _table(
        headers=("Время", "Компания", "Email", "Событие", "Данные"),
        rows=(
            (
                (
                    _value(event["event_at"]),
                    _value(event["company_name"]),
                    f'<span class="email">{_value(event["email"])}</span>',
                    _event_status(event["event_type"]),
                    _event_data(event["event_data"]),
                ),
                None,
            )
            for event in recent_events
        ),
        empty_text="У запуска нет событий.",
    )
    duration = _duration(details["started_at"], details["finished_at"])
    preparation = ""
    if details["campaign_family"] == "etrn":
        preparation = (
            "<h2>Подготовка получателей</h2>"
            '<section class="panel">'
            + _details(
                (
                    ("ИНН во входном списке", _value(details["input_inns_count"])),
                    ("Клиентов найдено", _value(details["clients_found_count"])),
                    ("Клиентов без email", _value(details["clients_without_email_count"])),
                    ("Email после enrichment", _value(details["email_found_after_enrichment_count"])),
                    ("Invalid email", _value(details["invalid_email_count"])),
                    ("Duplicate ETRN", _value(details["duplicate_count"])),
                    ("Bounced до отправки", _value(details["bounced_before_send_count"])),
                    ("К отправке", _value(details["prepared_email_count"])),
                )
            )
            + "</section>"
        )
    content = (
        '<div class="page-heading"><div class="eyebrow">История рассылок</div>'
        f'<h1>Запуск #{run_id}</h1>'
        '<p class="subtitle">Результаты отправки, реакции получателей и журнал событий.</p></div>'
        + _metrics(
            (
                ("ИНН обработано", details["input_inns_count"]),
                ("Получатели", details["recipients_added"]),
                ("Skipped", details["skipped_count"]),
                ("Pending", details["pending_count"]),
                ("Отправлено", details["sent_count"]),
                ("Failed", details["failed_count"]),
                ("Delivered", details["delivered_count"]),
                ("Bounce", details["bounced_count"]),
                ("Открытия", details["open_count"]),
                ("Уник. открытия", details["unique_open_count"]),
                ("Клики", details["click_count"]),
                ("Уник. клики", details["unique_click_count"]),
            )
        )
        + '<section class="panel run-card">'
        + _details(
            (
                ("Статус", _display_run_status(details)),
                ("Кампания", _value(details["campaign_name"])),
                ("Семейство", _value(details["campaign_family"])),
                ("Batch", _value(details["batch_number"])),
                ("Run ID", _value(details["run_id"])),
                ("Selection", _value(details["selection_id"])),
                ("Начало", _value(details["started_at"])),
                ("Окончание", _value(details["finished_at"])),
                ("Длительность", escape(duration)),
            )
        )
        + "</section>"
        + preparation
        + "<h2>Получатели и сообщения</h2>"
        + messages_table
        + "<h2>Последние события</h2>"
        + events_table
    )
    return _response(title=f"Запуск #{run_id}", content=content)


async def handle_message_details(
    request: web.Request,
) -> web.Response:
    """Показать одно сообщение и его хронологию событий."""
    message_id = _parse_message_id(request)
    details = get_mail_message_details(message_id)

    if details is None:
        raise web.HTTPNotFound(
            text="Сообщение не найдено.",
            content_type="text/plain",
        )

    timeline = get_mail_message_timeline(message_id)
    run_id = details["run_id"]

    if run_id is None:
        back_link = (
            '<a class="text-link" href="/admin/runs">'
            "← К списку запусков</a>"
        )
    else:
        safe_run_id = int(run_id)
        back_link = (
            '<a class="text-link" '
            f'href="/admin/runs/{safe_run_id}">'
            f"← К запуску #{safe_run_id}</a>"
        )

    timeline_table = _table(
        headers=("Время", "Событие", "Детали"),
        rows=(
            (
                (
                    _value(event["event_at"]),
                    _message_event_status(
                        event["event_type"],
                        event["event_data"],
                    ),
                    _event_data(event["event_data"]),
                ),
                None,
            )
            for event in timeline
        ),
        empty_text="У сообщения нет событий.",
    )
    summary = _details(
        (
            ("Компания", _value(details["company_name"])),
            ("ИНН", _value(details["inn"])),
            (
                "Email клиента",
                f'<span class="email">{_value(details["email"])}</span>',
            ),
            (
                "Фактический SMTP email",
                '<span class="email">'
                f'{_value(details["smtp_recipient_email"])}</span>',
            ),
            (
                "Test recipient",
                _test_recipient_status(details["is_test_recipient"]),
            ),
            ("Кампания", _value(details["campaign_name"])),
            ("Семейство", _value(details["campaign_family"])),
            ("Run ID", _value(details["run_id"])),
            ("Batch", _value(details["batch_number"])),
            ("Статус запуска", _display_run_status({"status": details["run_status"]})),
            ("Запуск начат", _value(details["run_started_at"])),
            ("Message ID", _value(details["message_id"])),
            (
                "Статус отправки",
                _message_send_status(details["send_status"]),
            ),
            (
                "Статус доставки",
                _delivery_status(details["delivery_status"]),
            ),
            ("Отправлено", _value(details["sent_at"])),
            ("Доставлено", _value(details["delivered_at"])),
            ("Открытий", _value(details["opened_count"])),
            ("Кликов", _value(details["clicked_count"])),
            ("Каналы кликов", _click_channels(timeline)),
            ("Последнее событие", _value(details["last_event_at"])),
        )
    )
    technical_details = (
        '<details class="event-data run-card">'
        "<summary>Технические данные</summary>"
        '<section class="panel run-card">'
        + _details(
            (
                ("Provider", _value(details["provider"])),
                (
                    "Provider message ID",
                    _value(details["provider_message_id"]),
                ),
                (
                    "Tracking token",
                    _value(details["tracking_token"]),
                ),
                ("Client ID", _value(details["client_id"])),
                ("Campaign ID", _value(details["campaign_id"])),
            )
        )
        + "</section></details>"
    )
    content = (
        back_link
        + '<div class="page-heading run-card">'
        + '<div class="eyebrow">История сообщения</div>'
        + f"<h1>Сообщение #{message_id}</h1>"
        + '<p class="subtitle">Отправка, доставка и реакции получателя.</p>'
        + "</div>"
        + '<section class="panel">'
        + summary
        + "</section>"
        + technical_details
        + "<h2>История событий</h2>"
        + timeline_table
    )

    return _response(
        title=f"Сообщение #{message_id}",
        content=content,
    )


def create_app() -> web.Application:
    """Создать read-only aiohttp-приложение админки."""
    app = web.Application()
    app.router.add_get("/admin", handle_admin)
    app.router.add_get("/admin/runs", handle_runs)
    app.router.add_get("/admin/runs/{run_id}", handle_run_details)
    app.router.add_get(
        "/admin/messages/{message_id}",
        handle_message_details,
    )
    return app


def main() -> None:
    """Запустить админку только на loopback-интерфейсе."""
    print(f"ProjectSbis admin listening on {ADMIN_URL}", flush=True)
    web.run_app(create_app(), host=ADMIN_HOST, port=ADMIN_PORT)


if __name__ == "__main__":
    main()
