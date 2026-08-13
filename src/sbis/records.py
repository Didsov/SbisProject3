"""
Универсальное декодирование внутреннего record-формата СБИС.

Назначение файла:
- преобразовывать структуры СБИС вида d/s в обычные Python-объекты;
- сопоставлять значения из массива d с именами полей из массива s;
- декодировать обычные record;
- декодировать recordset в список словарей;
- рекурсивно обрабатывать вложенные структуры СБИС;
- использовать один общий декодер для ответов разных методов СБИС.

Функции:
- decode_sbis_value() — рекурсивно декодирует произвольное значение СБИС;
- record_to_dict() — преобразует один record СБИС в обычный словарь;
- rows_to_dicts() — преобразует строки recordset или result.d в список словарей.

Основной принцип формата СБИС:
- поле "d" содержит значения;
- поле "s" содержит описание полей;
- значение d[index] соответствует описанию s[index];
- имя результирующего поля берётся из s[index]["n"].

Пример record:

    {
        "d": [
            false,
            50
        ],
        "s": [
            {
                "n": "isExpired",
                "t": "Логическое"
            },
            {
                "n": "ReadRows",
                "t": "Число целое"
            }
        ],
        "_type": "record"
    }

После декодирования:

    {
        "isExpired": False,
        "ReadRows": 50
    }

Также поддерживаются вложенные структуры:

- record
    Преобразуется в dict.

- recordset
    Преобразуется в list[dict].

- обычный list
    Каждый элемент декодируется рекурсивно.

- обычный dict
    Каждое значение декодируется рекурсивно.

Если структура содержит данные d, но не содержит схемы s,
она не может быть однозначно декодирована и сохраняется
в исходном виде.
"""

from __future__ import annotations

from typing import Any

def decode_sbis_value(value: Any) -> Any:
    """
    Рекурсивно декодировать произвольное значение из ответа СБИС.

    Что делает:
    - определяет тип переданного значения;
    - преобразует вложенный record в обычный словарь;
    - преобразует recordset в список словарей;
    - рекурсивно проходит обычные списки;
    - рекурсивно проходит обычные словари;
    - оставляет строки, числа, bool, None и другие простые значения
      без изменений.

    Аргументы:
        value:
            Любое значение, полученное из ответа СБИС.

    Возвращает:
        Декодированное Python-значение.

        Возможные варианты:
        - dict;
        - list;
        - str;
        - int;
        - float;
        - bool;
        - None;
        - другое исходное значение.

    Особенности:
        Если объект похож на внутреннюю структуру СБИС,
        но не содержит схемы "s", он не декодируется как record
        или recordset, потому что без схемы невозможно корректно
        восстановить имена полей.
    """
    if isinstance(value, list):
        return [
            decode_sbis_value(item)
            for item in value
        ]

    if not isinstance(value, dict):
        return value

    sbis_type = value.get("_type")
    data = value.get("d")
    schema = value.get("s")

    if (
        sbis_type == "record"
        and isinstance(data, list)
        and isinstance(schema, list)
    ):
        return record_to_dict(value)

    if (
        sbis_type == "recordset"
        and isinstance(data, list)
        and isinstance(schema, list)
    ):
        return rows_to_dicts(
            data,
            schema,
        )

    return {
        key: decode_sbis_value(item)
        for key, item in value.items()
    }


def record_to_dict(record: dict[str, Any]) -> dict[str, Any]:
    
    """
    Преобразовать один record СБИС из формата d/s в обычный словарь.

    Что делает:
    - получает массив значений из поля "d";
    - получает описание полей из массива "s";
    - сопоставляет значения и имена по одинаковым индексам;
    - возвращает обычный Python-словарь.

    Аргументы:
        record:
            Record СБИС, содержащий поля "d" и "s".

    Возвращает:
        Словарь вида:
            {
                "ИмяПоля": значение,
                ...
            }

    Исключения:
        TypeError:
            Если record не является словарём.

        ValueError:
            Если поля "d" или "s" отсутствуют;
            если "d" или "s" имеют неправильный тип;
            если количество значений и описаний полей различается;
            если элемент схемы не содержит корректного имени "n".
    """
    if not isinstance(record, dict):
        raise TypeError(
            "record должен быть словарём"
        )

    if "d" not in record:
        raise ValueError(
            'record не содержит поле "d"'
        )

    if "s" not in record:
        raise ValueError(
            'record не содержит поле "s"'
        )

    values = record["d"]
    schema = record["s"]

    if not isinstance(values, list):
        raise ValueError(
            'поле "d" должно быть массивом'
        )

    if not isinstance(schema, list):
        raise ValueError(
            'поле "s" должно быть массивом'
        )

    # d и s являются параллельными массивами:
    # значение d[index] описывается элементом s[index].
    if len(values) != len(schema):
        raise ValueError(
            "Количество значений в d не совпадает "
            "с количеством полей в s: "
            f"d={len(values)}, s={len(schema)}"
        )

    decoded: dict[str, Any] = {}

    for index, field_schema in enumerate(schema):
        if not isinstance(field_schema, dict):
            raise ValueError(
                f"Элемент s[{index}] должен быть объектом"
            )

        field_name = field_schema.get("n")

        if not isinstance(field_name, str) or not field_name:
            raise ValueError(
                f'Элемент s[{index}] не содержит корректное поле "n"'
            )

        decoded[field_name] = decode_sbis_value(
            values[index]
        )

    return decoded




def rows_to_dicts(
    rows: list[Any],
    schema: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Преобразовать набор строк СБИС из позиционного формата в список словарей.

    Что делает:
    - принимает строки из result["d"];
    - принимает общую схему полей из result["s"];
    - для каждой строки сопоставляет значение с именем поля по индексу;
    - возвращает список обычных Python-словарей.

    Аргументы:
        rows:
            Список строк из result["d"].

        schema:
            Общая схема полей из result["s"].

    Возвращает:
        Список словарей вида:
            [
                {
                    "Название": "...",
                    "ИНН": "...",
                    "SppUuid": "...",
                    ...
                },
                ...
            ]

    Исключения:
        ValueError:
            Если строка не является массивом;
            если количество значений строки не совпадает
            с количеством полей схемы;
            если элемент схемы не содержит корректного имени "n".
    """
    decoded_rows: list[dict[str, Any]] = []

    for row_index, row in enumerate(rows):
        if not isinstance(row, list):
            raise ValueError(
                f"Строка result.d[{row_index}] должна быть массивом"
            )

        if len(row) != len(schema):
            raise ValueError(
                f"Количество значений в строке result.d[{row_index}] "
                f"не совпадает с количеством полей схемы: "
                f"row={len(row)}, schema={len(schema)}"
            )

        decoded_row: dict[str, Any] = {}

        for field_index, field_schema in enumerate(schema):
            if not isinstance(field_schema, dict):
                raise ValueError(
                    f"Элемент result.s[{field_index}] должен быть объектом"
                )

            field_name = field_schema.get("n")

            if not isinstance(field_name, str) or not field_name:
                raise ValueError(
                    f'Элемент result.s[{field_index}] '
                    'не содержит корректное поле "n"'
                )

            decoded_row[field_name] = decode_sbis_value(
                row[field_index]
            )

        decoded_rows.append(decoded_row)

    return decoded_rows