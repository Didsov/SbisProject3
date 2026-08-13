"""
Работа с пагинацией ответов СБИС.

Назначение файла:
- извлекать nextPosition из metadata result["m"];
- определять, существует ли следующая страница;
- изолировать логику курсора пагинации от HTTP-клиента;
- не разбирать внутреннее содержимое курсора без необходимости.

Функции:
- get_next_position() — извлекает nextPosition из metadata;
- has_next_position() — проверяет, существует ли следующая позиция.

Особенности формата СБИС:
- metadata result["m"] после декодирования представляет собой dict;
- nextPosition обычно приходит как список;
- элемент списка может содержать непрозрачную строку курсора;
- в конце списка СБИС может вернуть [None];
- сам курсор не нужно парсить: его следует передавать дальше как есть.
"""

from __future__ import annotations

from typing import Any


def get_next_position(
    metadata: dict[str, Any],
) -> Any:
    """
    Получить nextPosition из декодированных metadata пагинации.

    Что делает:
    - принимает декодированный result["m"];
    - возвращает значение поля nextPosition без изменений.

    Аргументы:
        metadata:
            Декодированный словарь metadata из result["m"].

    Возвращает:
        Значение nextPosition.

        Обычно это:
        - list со строкой курсора;
        - [None] в конце списка;
        - None, если поле отсутствует.

    Важно:
        Функция специально не разбирает внутреннюю структуру курсора.
        nextPosition считается непрозрачным значением СБИС.
    """
    return metadata.get("nextPosition")


def has_next_position(
    next_position: Any,
) -> bool:
    """
    Определить, существует ли следующая страница СБИС.

    Что делает:
    - проверяет значение nextPosition;
    - учитывает None;
    - учитывает пустой список;
    - учитывает список, содержащий только None;
    - считает остальные значения существующим курсором.

    Аргументы:
        next_position:
            Значение поля nextPosition из metadata.

    Возвращает:
        True:
            Если следующая позиция существует.

        False:
            Если следующей позиции нет.

    Примеры окончания пагинации:
        None
        []
        [None]

    Пример существующей позиции:
        [
            '{"d":[...], "s":[...], "_type":"record"}'
        ]
    """
    if next_position is None:
        return False

    if isinstance(next_position, list):
        return any(
            item is not None
            for item in next_position
        )

    return True