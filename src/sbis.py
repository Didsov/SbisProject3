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


async def send_raw_request() -> dict[str, Any]:
    """
    Выполнить один JSON-RPC запрос к СБИС.

    Что делает:
    - получает URL и cookie из .env;
    - загружает шаблон запроса;
    - подставляет ID выборки;
    - выполняет HTTP POST;
    - выводит основную диагностическую информацию;
    - преобразует тело ответа в JSON.

    Возвращает:
        Сырой JSON-ответ СБИС.

    Исключения:
        RuntimeError:
            Если СБИС вернул HTTP-код ошибки.

        ValueError:
            Если ответ не является корректным JSON-объектом.
    """
    cookie = require_env("SBIS_BROWSER_COOKIE")
    url = get_sbis_url()

    template = load_request()
    payload = template

    # Cookie браузера используется для воспроизведения
    # авторизованного запроса веб-интерфейса СБИС.
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
    }

    timeout = aiohttp.ClientTimeout(total=60)

    print(f"Метод: {payload.get('method')}")

    selection_id = require_env("SBIS_SELECTION_ID")
    print(f"Выборка СБИС: {selection_id}")

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