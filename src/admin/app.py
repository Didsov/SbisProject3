"""Read-only HTTP-админка ProjectSbis."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable, Sequence

from aiohttp import web

from src.database import (
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


def _send_status(value: object) -> str:
    return _badge(value, SEND_STATUSES)


def _delivery_status(value: object) -> str:
    return _badge(value, DELIVERY_STATUSES)


def _event_status(value: object) -> str:
    return _badge(value, EVENT_STATUSES)


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
            <div class="eyebrow">Рассылки</div><h1>Последний запуск</h1>
        </div>
        <section class="panel empty">Запуски рассылки пока отсутствуют.</section>
        """
        return _response(title="Сводка", content=content)

    latest = runs[0]
    run_id = int(latest["run_id"])
    messages = get_mail_run_messages(run_id)
    opened_count = sum(
        int(message["opened_count"] or 0) > 0
        for message in messages
    )
    clicked_count = sum(
        int(message["clicked_count"] or 0) > 0
        for message in messages
    )
    duration = _duration(latest["started_at"], latest["finished_at"])
    content = (
        '<div class="page-heading"><div class="eyebrow">Рассылки</div>'
        '<h1>Последний запуск</h1>'
        '<p class="subtitle">Ключевые результаты последней ежедневной рассылки.</p></div>'
        + _metrics(
            (
                ("Получателей", latest["recipients_added"]),
                ("Отправлено", latest["sent_count"]),
                ("Принято сервером", latest["delivered_count"]),
                ("Bounce", latest["bounced_count"]),
                ("Открыли", opened_count),
                ("Кликнули", clicked_count),
            )
        )
        + '<section class="panel run-card"><div class="run-card-header">'
        + '<h2 class="run-card-title">Параметры запуска</h2>'
        + f'<a class="text-link" href="/admin/runs/{run_id}">Подробнее о запуске →</a>'
        + "</div>"
        + _details(
            (
                ("Статус", _run_status(latest["status"])),
                ("Начало", _value(latest["started_at"])),
                ("Окончание", _value(latest["finished_at"])),
                ("Длительность", escape(duration)),
                ("Кампания", _value(latest["campaign_name"])),
                ("Selection", _value(latest["selection_id"])),
            )
        )
        + "</section>"
    )
    return _response(title="Сводка", content=content)


async def handle_runs(request: web.Request) -> web.Response:
    """Показать последние запуски рассылки."""
    del request
    runs = get_recent_mail_runs(limit=RECENT_RUNS_LIMIT)
    table = _table(
        headers=(
            "Запуск",
            "Кампания",
            "Начало",
            "Длительность",
            "Получатели",
            "Отправлено",
            "Принято сервером",
            "Bounce",
            "Ошибки",
            "Статус",
        ),
        rows=(
            (
                (
                    f'<strong class="numeric">#{_value(run["run_id"])}</strong>',
                    _value(run["campaign_name"]),
                    _value(run["started_at"]),
                    escape(_duration(run["started_at"], run["finished_at"])),
                    _value(run["recipients_added"]),
                    _value(run["sent_count"]),
                    _value(run["delivered_count"]),
                    _value(run["bounced_count"]),
                    _value(run["failed_count"]),
                    _run_status(run["status"]),
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
            "Компания",
            "Email",
            "Отправка",
            "Доставка",
            "Открытия",
            "Клики",
            "Отправлено",
            "Последнее событие",
        ),
        rows=(
            (
                (
                    _value(message["company_name"]),
                    f'<span class="email">{_value(message["email"])}</span>',
                    _send_status(message["send_status"]),
                    _delivery_status(message["delivery_status"]),
                    _value(message["opened_count"]),
                    _value(message["clicked_count"]),
                    _value(message["sent_at"]),
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
    content = (
        '<div class="page-heading"><div class="eyebrow">История рассылок</div>'
        f'<h1>Запуск #{run_id}</h1>'
        '<p class="subtitle">Результаты отправки, реакции получателей и журнал событий.</p></div>'
        + _metrics(
            (
                ("Получателей", details["recipients_added"]),
                ("Отправлено", details["sent_count"]),
                ("Принято сервером", details["delivered_count"]),
                ("Bounce", details["bounced_count"]),
                ("Deferred", details["deferred_count"]),
                ("Ошибки SMTP", details["failed_count"]),
            )
        )
        + '<section class="panel run-card">'
        + _details(
            (
                ("Статус", _run_status(details["status"])),
                ("Кампания", _value(details["campaign_name"])),
                ("Selection", _value(details["selection_id"])),
                ("Начало", _value(details["started_at"])),
                ("Окончание", _value(details["finished_at"])),
                ("Длительность", escape(duration)),
            )
        )
        + "</section><h2>Сообщения</h2>"
        + messages_table
        + "<h2>Последние события</h2>"
        + events_table
    )
    return _response(title=f"Запуск #{run_id}", content=content)


def create_app() -> web.Application:
    """Создать read-only aiohttp-приложение админки."""
    app = web.Application()
    app.router.add_get("/admin", handle_admin)
    app.router.add_get("/admin/runs", handle_runs)
    app.router.add_get("/admin/runs/{run_id}", handle_run_details)
    return app


def main() -> None:
    """Запустить админку только на loopback-интерфейсе."""
    print(f"ProjectSbis admin listening on {ADMIN_URL}", flush=True)
    web.run_app(create_app(), host=ADMIN_HOST, port=ADMIN_PORT)


if __name__ == "__main__":
    main()
