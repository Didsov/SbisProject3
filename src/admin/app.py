"""Первый read-only HTTP-каркас админки ProjectSbis."""

from __future__ import annotations

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
    color: #172033;
    background: #f4f6fa;
}
* { box-sizing: border-box; }
body { margin: 0; background: #f4f6fa; }
a { color: #2457d6; text-decoration: none; }
a:hover { text-decoration: underline; }
.page { width: min(1440px, calc(100% - 32px)); margin: 0 auto; padding: 24px 0 48px; }
.topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 24px;
}
.brand { color: #172033; font-size: 20px; font-weight: 750; }
.nav { display: flex; gap: 16px; }
h1 { margin: 0 0 20px; font-size: 28px; }
h2 { margin: 28px 0 14px; font-size: 20px; }
.muted { color: #697386; }
.panel {
    background: #fff;
    border: 1px solid #e0e5ef;
    border-radius: 12px;
    box-shadow: 0 4px 18px rgba(36, 52, 85, 0.06);
    padding: 20px;
}
.metrics {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
}
.metric { padding: 14px; border: 1px solid #e6eaf2; border-radius: 9px; }
.metric-label { color: #697386; font-size: 12px; margin-bottom: 7px; }
.metric-value { font-size: 19px; font-weight: 700; overflow-wrap: anywhere; }
.actions { margin-top: 18px; }
.button {
    display: inline-block;
    padding: 9px 13px;
    border-radius: 8px;
    background: #2457d6;
    color: #fff;
    font-weight: 650;
}
.button:hover { color: #fff; text-decoration: none; background: #1949bc; }
.table-wrap { overflow-x: auto; border: 1px solid #e0e5ef; border-radius: 10px; }
table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { padding: 11px 12px; border-bottom: 1px solid #e8ebf2; text-align: left; vertical-align: top; }
th { background: #f8f9fc; color: #515b70; font-size: 12px; white-space: nowrap; }
td { font-size: 14px; }
tr:last-child td { border-bottom: 0; }
.status {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    background: #edf1f7;
    font-size: 12px;
    font-weight: 700;
}
.status-success, .status-sent, .status-delivered { background: #dcf7e7; color: #146c3a; }
.status-partial, .status-deferred, .status-running { background: #fff2cc; color: #805b00; }
.status-failed, .status-bounced { background: #fde2e2; color: #9b2525; }
.event-data { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
.empty { padding: 28px; text-align: center; color: #697386; }
@media (max-width: 680px) {
    .page { width: min(100% - 20px, 1440px); padding-top: 16px; }
    .topbar { align-items: flex-start; flex-direction: column; }
    h1 { font-size: 24px; }
}
"""


def _value(value: object) -> str:
    """Безопасно подготовить значение БД для HTML."""
    if value is None or value == "":
        return '<span class="muted">—</span>'

    return escape(str(value))


def _status(value: object) -> str:
    """Отобразить статус безопасным компактным badge."""
    status_text = str(value or "unknown")
    allowed_classes = {
        "success",
        "partial",
        "failed",
        "running",
        "sent",
        "delivered",
        "deferred",
        "bounced",
    }
    status_class = (
        status_text
        if status_text in allowed_classes
        else "unknown"
    )

    return (
        f'<span class="status status-{status_class}">'
        f"{escape(status_text)}"
        "</span>"
    )


def _page(
    *,
    title: str,
    content: str,
) -> str:
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
            <a class="brand" href="/admin">ProjectSbis Admin</a>
            <nav class="nav" aria-label="Основная навигация">
                <a href="/admin">Сводка</a>
                <a href="/admin/runs">Запуски</a>
            </nav>
        </header>
        {content}
    </main>
</body>
</html>"""


def _response(
    *,
    title: str,
    content: str,
) -> web.Response:
    """Вернуть UTF-8 HTML-ответ."""
    return web.Response(
        text=_page(
            title=title,
            content=content,
        ),
        content_type="text/html",
        charset="utf-8",
    )


def _metrics(
    items: Iterable[tuple[str, object]],
) -> str:
    """Собрать компактную сетку значений."""
    cards = "".join(
        (
            '<div class="metric">'
            f'<div class="metric-label">{escape(label)}</div>'
            f'<div class="metric-value">{_value(value)}</div>'
            "</div>"
        )
        for label, value in items
    )

    return f'<div class="metrics">{cards}</div>'


def _table(
    *,
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    empty_text: str,
) -> str:
    """Собрать простую адаптивную таблицу."""
    rendered_rows = list(rows)

    if not rendered_rows:
        return f'<div class="panel empty">{escape(empty_text)}</div>'

    header_html = "".join(
        f"<th>{escape(header)}</th>"
        for header in headers
    )
    rows_html = "".join(
        "<tr>"
        + "".join(
            f"<td>{cell}</td>"
            for cell in row
        )
        + "</tr>"
        for row in rendered_rows
    )

    return (
        '<div class="table-wrap">'
        "<table>"
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
        "</div>"
    )


async def handle_admin(
    request: web.Request,
) -> web.Response:
    """Показать сводку последнего запуска."""
    del request
    runs = get_recent_mail_runs(limit=1)

    if not runs:
        content = """
        <h1>Сводка</h1>
        <section class="panel empty">
            Запуски рассылки пока отсутствуют.
        </section>
        <div class="actions"><a class="button" href="/admin/runs">Все запуски</a></div>
        """
        return _response(
            title="Сводка",
            content=content,
        )

    latest = runs[0]
    run_id = int(latest["run_id"])
    content = (
        "<h1>Последний запуск</h1>"
        '<section class="panel">'
        + _metrics(
            (
                ("Status", latest["status"]),
                ("Started at", latest["started_at"]),
                ("Finished at", latest["finished_at"]),
                ("Sent", latest["sent_count"]),
                ("Delivered", latest["delivered_count"]),
                ("Bounced", latest["bounced_count"]),
                ("Failed", latest["failed_count"]),
            )
        )
        + '<div class="actions">'
        + f'<a class="button" href="/admin/runs/{run_id}">Открыть запуск #{run_id}</a> '
        + '<a href="/admin/runs">Все запуски</a>'
        + "</div>"
        + "</section>"
    )

    return _response(
        title="Сводка",
        content=content,
    )


async def handle_runs(
    request: web.Request,
) -> web.Response:
    """Показать последние запуски рассылки."""
    del request
    runs = get_recent_mail_runs(
        limit=RECENT_RUNS_LIMIT
    )
    table = _table(
        headers=(
            "Run ID",
            "Campaign",
            "Selection",
            "Trigger",
            "Status",
            "Started",
            "Finished",
            "Recipients",
            "Sent",
            "Delivered",
            "Bounced",
            "Deferred",
            "Failed",
        ),
        rows=(
            (
                f'<a href="/admin/runs/{int(run["run_id"])}">#{_value(run["run_id"])}</a>',
                _value(run["campaign_name"]),
                _value(run["selection_id"]),
                _value(run["trigger"]),
                _status(run["status"]),
                _value(run["started_at"]),
                _value(run["finished_at"]),
                _value(run["recipients_added"]),
                _value(run["sent_count"]),
                _value(run["delivered_count"]),
                _value(run["bounced_count"]),
                _value(run["deferred_count"]),
                _value(run["failed_count"]),
            )
            for run in runs
        ),
        empty_text="Запуски рассылки пока отсутствуют.",
    )

    return _response(
        title="Запуски",
        content=(
            "<h1>История запусков</h1>"
            + table
        ),
    )


def _parse_run_id(request: web.Request) -> int:
    """Проверить run_id из маршрута."""
    raw_run_id = request.match_info.get(
        "run_id",
        "",
    )

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


async def handle_run_details(
    request: web.Request,
) -> web.Response:
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
    recent_events = list(
        reversed(events[-RECENT_EVENTS_LIMIT:])
    )

    messages_table = _table(
        headers=(
            "Company",
            "Email",
            "Send status",
            "Delivery status",
            "Sent at",
            "Opened",
            "Clicked",
            "Last event",
        ),
        rows=(
            (
                _value(message["company_name"]),
                _value(message["email"]),
                _status(message["send_status"]),
                _status(message["delivery_status"]),
                _value(message["sent_at"]),
                _value(message["opened_count"]),
                _value(message["clicked_count"]),
                _value(message["last_event_at"]),
            )
            for message in messages
        ),
        empty_text="У запуска нет боевых сообщений.",
    )
    events_table = _table(
        headers=(
            "Time",
            "Company",
            "Email",
            "Event",
            "Event data",
        ),
        rows=(
            (
                _value(event["event_at"]),
                _value(event["company_name"]),
                _value(event["email"]),
                _status(event["event_type"]),
                f'<code class="event-data">{_value(event["event_data"])}</code>',
            )
            for event in recent_events
        ),
        empty_text="У запуска нет событий.",
    )
    summary = _metrics(
        (
            ("Campaign", details["campaign_name"]),
            ("Selection", details["selection_id"]),
            ("Trigger", details["trigger"]),
            ("Status", details["status"]),
            ("Started", details["started_at"]),
            ("Finished", details["finished_at"]),
            ("Recipients", details["recipients_added"]),
            ("Sent", details["sent_count"]),
            ("Delivered", details["delivered_count"]),
            ("Bounced", details["bounced_count"]),
            ("Deferred", details["deferred_count"]),
            ("Failed", details["failed_count"]),
        )
    )
    content = (
        f"<h1>Запуск #{run_id}</h1>"
        '<section class="panel">'
        + summary
        + "</section>"
        + "<h2>Сообщения</h2>"
        + messages_table
        + "<h2>Последние события</h2>"
        + events_table
    )

    return _response(
        title=f"Запуск #{run_id}",
        content=content,
    )


def create_app() -> web.Application:
    """Создать read-only aiohttp-приложение админки."""
    app = web.Application()
    app.router.add_get(
        "/admin",
        handle_admin,
    )
    app.router.add_get(
        "/admin/runs",
        handle_runs,
    )
    app.router.add_get(
        "/admin/runs/{run_id}",
        handle_run_details,
    )
    return app


def main() -> None:
    """Запустить админку только на loopback-интерфейсе."""
    print(
        f"ProjectSbis admin listening on {ADMIN_URL}",
        flush=True,
    )
    web.run_app(
        create_app(),
        host=ADMIN_HOST,
        port=ADMIN_PORT,
    )


if __name__ == "__main__":
    main()
