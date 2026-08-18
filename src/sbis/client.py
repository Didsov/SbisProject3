"""
Низкоуровневое взаимодействие с веб-интерфейсом СБИС.

Назначение файла:
- загрузить JSON-RPC запрос из config/request.json;
- подставить в запрос значения из .env;
- выполнить HTTP POST-запрос в СБИС;
- вернуть сырой JSON-ответ без бизнес-обработки.

На текущем этапе модуль намеренно не занимается:
- пагинацией;
- разбором клиентов;
- сохранением в базу данных;
- фильтрацией результата;
- рассылкой.

Функции:
- load_request() — загружает шаблон JSON-RPC запроса;
- prepare_request() — подставляет значения среды в шаблон;
- send_raw_request() — отправляет запрос и возвращает сырой JSON.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import aiohttp

from src.config import (
    REQUEST_FILE,
    get_sbis_url,
    require_env,
)


def load_request() -> dict[str, Any]:
    """
    Загрузить шаблон JSON-RPC запроса из config/request.json.

    Что делает:
    - открывает request.json;
    - преобразует JSON в Python-словарь;
    - проверяет тип корневого объекта.

    Возвращает:
        Словарь с шаблоном JSON-RPC запроса.

    Исключения:
        ValueError:
            Если корневое значение JSON не является объектом.
    """
    with REQUEST_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError(
            "config/request.json должен содержать JSON-объект"
        )

    return payload

async def send_raw_request(
    selection_id: int,
    position: str | None = None,
) -> dict[str, Any]:
    """
Отправить запрос в СБИС и вернуть сырой JSON-ответ.

Что делает:
- загружает шаблон запроса из config/request.json;
- при наличии position подставляет курсор в Навигация.Position;
- выполняет HTTP POST запрос;
- проверяет HTTP-ответ;
- возвращает JSON СБИС без бизнес-разбора.

Аргументы:
    position:
        Непрозрачный курсор следующей страницы СБИС.

        Для первой страницы передаётся None.
        Для следующей страницы используется строка,
        полученная из metadata["nextPosition"].

Возвращает:
    Сырой JSON-ответ СБИС.
"""
    cookie = require_env("SBIS_BROWSER_COOKIE")
    url = get_sbis_url()
    payload = load_request()
    filter_record = payload["params"]["Фильтр"]

    set_record_value(
        filter_record,
        "Раздел",
        f".{selection_id}..",
    )

    if position is not None:
        navigation = payload["params"]["Навигация"]

        # СБИС ожидает Position именно как record.
        # Сам курсор остаётся непрозрачной строкой и помещается
        # во вложенное поле Cursor без дополнительного разбора.
        navigation["d"][3] = {
            "d": [
                position
            ],
            "s": [
                {
                    "t": "Строка",
                    "n": "Cursor",
                }
            ],
            "_type": "record",
            "f": 1,
        }
        # Cookie браузера используется для воспроизведения
    # авторизованного запроса веб-интерфейса СБИС.
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        ),
        "Origin": "https://online.sbis.ru",
        "Referer": "https://online.sbis.ru/",
    }

    timeout = aiohttp.ClientTimeout(total=60)

    print(f"Метод: {payload.get('method')}")

    selection_id = require_env("SBIS_SELECTION_ID")

    async with aiohttp.ClientSession(
        timeout=timeout,
    ) as session:
        async with session.post(
            url,
            headers=headers,
            json=payload,
        ) as response:
            response_text = await response.text()

            print(f"HTTP status: {response.status}")
            print(f"Response URL: {response.url}")

            if response.status >= 400:
                print("Ответ сервера:")
                print(response_text)

                raise RuntimeError(
                    f"СБИС вернул HTTP {response.status}"
                )

    try:
        response_data = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise ValueError(
            "СБИС вернул успешный HTTP-ответ, "
            "но тело ответа не является JSON"
        ) from error

    if not isinstance(response_data, dict):
        raise ValueError(
            "Корневое значение ответа СБИС "
            "должно быть JSON-объектом"
        )

    return response_data


def set_record_value(
    record: dict[str, Any],
    field_name: str,
    value: Any,
) -> None:
    """
    Изменить значение поля во внутреннем record-формате СБИС.

    Что делает:
    - ищет поле в массиве схемы "s" по имени "n";
    - находит соответствующий индекс;
    - заменяет значение в массиве "d";
    - не зависит от конкретной позиции поля в record.

    Аргументы:
        record:
            Record СБИС с массивами "d" и "s".

        field_name:
            Имя поля из schema[index]["n"].

        value:
            Новое значение поля.

    Исключения:
        ValueError:
            Если record имеет неправильный формат;
            если указанное поле не найдено.
    """
    values = record.get("d")
    schema = record.get("s")

    if not isinstance(values, list):
        raise ValueError(
            'Record не содержит корректный массив "d"'
        )

    if not isinstance(schema, list):
        raise ValueError(
            'Record не содержит корректный массив "s"'
        )

    for index, field_schema in enumerate(schema):
        if not isinstance(field_schema, dict):
            continue

        if field_schema.get("n") != field_name:
            continue

        if index >= len(values):
            raise ValueError(
                f'Поле "{field_name}" отсутствует в массиве d'
            )

        values[index] = value
        return

    raise ValueError(
        f'Поле "{field_name}" не найдено в record'
    )


