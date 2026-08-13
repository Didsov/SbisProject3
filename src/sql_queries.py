"""
Автоматическая загрузка SQL-запросов ProjectSbis.

Назначение модуля:
- искать SQL-файлы в каталоге проекта `sql/`;
- использовать имя файла без расширения как имя запроса;
- загружать текст SQL по имени;
- предоставлять список доступных запросов для CLI-экспортера.

Пример:

    sql/get_all_clients.sql

будет доступен как:

    get_all_clients

и может быть запущен так:

    python -m src.sql_export --query get_all_clients

Функции:
- get_sql_directory() — вернуть путь к каталогу sql;
- get_available_queries() — получить список доступных запросов;
- get_sql_query() — загрузить SQL по имени.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQL_DIRECTORY = PROJECT_ROOT / "sql"


def get_sql_directory() -> Path:
    """
    Получить путь к каталогу SQL-запросов.

    Возвращает:
        Путь к директории проекта `sql/`.
    """
    return SQL_DIRECTORY


def get_available_queries() -> list[str]:
    """
    Получить список всех доступных SQL-запросов.

    Что делает:
    - проверяет наличие каталога `sql/`;
    - ищет в нём файлы с расширением `.sql`;
    - использует имя файла без расширения как имя запроса;
    - сортирует имена по алфавиту.

    Возвращает:
        Список имён запросов.

    Пример:
        Для файлов:

            sql/get_all_clients.sql
            sql/selection_summary.sql

        функция вернёт:

            [
                "get_all_clients",
                "selection_summary",
            ]
    """
    sql_directory = get_sql_directory()

    if not sql_directory.exists():
        return []

    return sorted(
        path.stem
        for path in sql_directory.glob("*.sql")
        if path.is_file()
    )


def get_sql_query(name: str) -> str:
    """
    Загрузить SQL-запрос по имени.

    Аргументы:
        name:
            Имя SQL-файла без расширения.

            Например:

                get_all_clients

            соответствует файлу:

                sql/get_all_clients.sql

    Возвращает:
        Текст SQL-запроса.

    Исключения:
        ValueError:
            Если имя запроса пустое.

        KeyError:
            Если SQL-файл не найден.

        ValueError:
            Если найденный SQL-файл пуст.
    """
    query_name = name.strip()

    if not query_name:
        raise ValueError(
            "Имя SQL-запроса не может быть пустым"
        )

    sql_directory = get_sql_directory()

    sql_path = sql_directory / f"{query_name}.sql"

    if not sql_path.is_file():
        available_queries = get_available_queries()

        if available_queries:
            available_text = ", ".join(
                available_queries
            )
        else:
            available_text = "нет доступных запросов"

        raise KeyError(
            f"SQL-запрос {query_name!r} не найден. "
            f"Ожидаемый файл: {sql_path}. "
            f"Доступные запросы: {available_text}"
        )

    sql = sql_path.read_text(
        encoding="utf-8"
    ).strip()

    if not sql:
        raise ValueError(
            f"SQL-файл пуст: {sql_path}"
        )

    return sql