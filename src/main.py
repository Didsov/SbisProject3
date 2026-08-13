from __future__ import annotations

import asyncio
import json
from datetime import datetime

from src.config import (
    RAW_DATA_DIR,
    load_environment,
)
from src.sbis import send_raw_request


async def run() -> None:
    """Отправить запрос и сохранить сырой ответ СБИС."""

    load_environment()

    print("Отправляю запрос в СБИС...")

    response = await send_raw_request()

    RAW_DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    output_path = (
        RAW_DATA_DIR
        / f"sbis_response_{timestamp}.json"
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            response,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print("Ответ получен.")
    print(f"Сохранён: {output_path}")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()