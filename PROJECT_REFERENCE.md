# ProjectSbis — практический справочник и runbook

Документ описывает текущее состояние ProjectSbis на основе фактического кода,
`docs/PROJECT_WORKFLOW_v5.md` и подтверждённых runtime-проверок на production VPS.
Если текст старой документации расходится с кодом, источником истины считается код.

> Этот справочник содержит команды, которые могут обращаться к СБИС, изменять
> SQLite и отправлять реальные письма. Перед production-командами всегда проверяйте
> текущий каталог, `.env`, путь к БД, ID кампании, лимит и режим запуска.

## Оглавление

1. [Назначение проекта](#1-назначение-проекта)
2. [Основные подсистемы](#2-основные-подсистемы)
3. [Структура проекта](#3-структура-проекта)
4. [Конфигурация](#4-конфигурация)
5. [Работа с клиентами](#5-работа-с-клиентами)
6. [База данных](#6-база-данных)
7. [Почтовые кампании](#7-почтовые-кампании)
8. [Отправка писем](#8-отправка-писем)
9. [Шаблоны писем](#9-шаблоны-писем)
10. [Open tracking](#10-open-tracking)
11. [Click tracking](#11-click-tracking)
12. [Метрики рассылки](#12-метрики-рассылки)
13. [Retry и ошибки](#13-retry-и-ошибки)
14. [SQL и экспорт](#14-sql-и-экспорт)
15. [Production VPS](#15-production-vps)
16. [Типовые сценарии](#16-типовые-сценарии)
17. [Что автоматизировано, а что ещё нет](#17-что-автоматизировано-а-что-ещё-нет)
18. [Известные особенности и расхождения](#18-известные-особенности-и-расхождения)

## 1. Назначение проекта

ProjectSbis получает организации из СБИС, сохраняет их в SQLite, обогащает
реквизитами и контактами, формирует почтовые очереди и отправляет письма через
собственный SMTP-сервер. Отдельный HTTP-сервис фиксирует открытия и переходы по
ссылкам.

Фактическая рабочая цепочка:

```text
CRMClients.ListClientsOnline или поиск по ИНН
    → clients
    → client_selections / client_sources
    → ContractorCard.Read
    → реквизиты, директор, client_contacts
    → mail_campaigns
    → ручной populate_mail_recipients()
    → mail_recipients со статусом pending
    → sender
    → mail_messages + tracking_token
    → SMTP / Postfix
    → tracking pixel и tracked-ссылки
    → mail_events
    → get_mail_campaign_stats()
```

Ежедневный сценарий объединён в `src.daily_run`, а каждый запуск фиксируется
отдельной строкой `mail_runs` и завершается итоговым отчётом.
Tracking-сервис работает отдельно и должен быть постоянно запущен на VPS.

## 2. Основные подсистемы

| Подсистема | Назначение | Основные файлы и точки входа |
|---|---|---|
| Загрузка выборки | Получить все страницы пользовательской выборки СБИС | `src/client_loader.py`, `src/sbis/client_list.py`, `src/sbis/client.py` |
| Поиск по ИНН | Найти точный `SppUuid` через `Contractor.SearchSuggest` | `src/client_loader.py`, `src/sbis/company_search.py` |
| Обогащение | Получить `ContractorCard.Read`, извлечь реквизиты, директора и контакты | `src/sbis/contractor_card.py`, `src/sbis/card_parser.py` |
| Декодирование СБИС | Преобразовать внутренние структуры `d/s` в dict/list | `src/sbis/records.py`, `src/sbis/pagination.py` |
| Контроль авторизации СБИС | Проверить browser cookie, остановить daily run и отправить один alert на эпизод | `src/sbis/auth.py`, `auth_state.py`, `auth_monitor.py`, `cookie_manager.py` |
| SQLite | Схема, миграции, клиенты, кампании, сообщения, события и метрики | `src/database.py` |
| Источники клиентов | Связать клиента с выборкой или входным списком ИНН | `client_selections`, `client_sources` в `src/database.py` |
| Почтовая очередь | Создать кампанию и наполнить получателей | `get_or_create_mail_campaign()`, `populate_mail_recipients()` |
| Sender | Dry-run, mock, безопасный SMTP test, tracking-test и реальная отправка | `src/mailing/sender.py` |
| SMTP | STARTTLS, SMTP AUTH, MIME и CID-логотип | `src/mailing/smtp_provider.py` |
| Шаблоны | Subject, plain text, HTML, персонализация и tracking URL | `src/mailing/templates/registry.py`, `new_companies.py` |
| Tracking | HTTP pixel, click redirect и запись событий | `src/tracking/app.py`, `record_mail_open()`, `record_mail_click()` |
| Статистика | Статусы очереди и engagement-метрики | `get_mail_campaign_stats()`, `src/mailing/dry_run.py` |
| SQL/XLSX | Read-only SQL и оформленный экспорт в Excel | `src/sql_export.py`, `src/sql_queries.py`, `sql/*.sql` |
| Конфигурация | `.env`, пути, СБИС и SMTP | `src/config.py`, `config/request.json` |

## 3. Структура проекта

```text
ProjectSbis/
├── .env                         # локальные секреты, не хранится в Git
├── requirements.txt             # aiohttp, python-dotenv, openpyxl
├── PROJECT_REFERENCE.md         # этот runbook
├── config/
│   └── request.json             # шаблон CRMClients.ListClientsOnline
├── data/
│   └── project.db               # локальная SQLite, не хранится в Git
├── docs/
│   ├── PROJECT_WORKFLOW_v5.md   # накопленная инструкция, частично устарела
│   ├── SQL_CHEATSHEET.md        # диагностические SELECT
│   └── MAIL_SERVER_SETUP_PROJECTSBIS.md
├── exports/                     # создаваемые XLSX, не хранится в Git
├── lists/                       # локальные списки ИНН, не хранится в Git
├── sql/
│   ├── all_full_clients.sql
│   ├── get_all_clients.sql
│   ├── selection_full_clients.sql
│   ├── selections_summary.sql
│   └── source_full_clients.sql
├── deploy/
│   ├── projectsbis-daily.service
│   ├── projectsbis-daily.timer
│   ├── projectsbis-admin.service
│   ├── projectsbis-sbis-auth-check.service
│   └── projectsbis-sbis-auth-check.timer
└── src/
    ├── client_loader.py         # рабочий CLI загрузки и обогащения
    ├── config.py                # конфигурация и пути
    ├── database.py              # SQLite и бизнес-операции
    ├── sql_export.py            # SQL → XLSX
    ├── sql_queries.py           # реестр sql/*.sql
    ├── mailing/
    │   ├── sender.py            # режимы отправки
    │   ├── dry_run.py           # просмотр очереди и базовой статистики
    │   ├── smtp_provider.py     # реальный SMTP
    │   ├── assets/              # CID/BIMI-ресурсы
    │   └── templates/
    │       ├── registry.py
    │       └── new_companies.py
    ├── sbis/
    │   ├── auth.py              # безопасная HTTP-проверка browser cookie
    │   ├── auth_alert.py        # отдельное служебное SMTP-уведомление
    │   ├── auth_monitor.py      # единая state machine hourly/daily
    │   ├── auth_state.py        # атомарный JSON state + OS-level lock
    │   ├── cookie_manager.py    # проверка и безопасная замена cookie
    │   ├── client.py
    │   ├── client_list.py
    │   ├── company_search.py
    │   ├── contractor_card.py
    │   ├── card_parser.py
    │   ├── pagination.py
    │   └── records.py
    └── tracking/
        └── app.py               # aiohttp на 127.0.0.1:8080
```

`src/main.py` не является рабочей основной точкой входа: это устаревший модуль.
Для загрузки используйте только `python -m src.client_loader`.

## 4. Конфигурация

### 4.1. Откуда загружается окружение

`src/config.py` загружает настройки при импорте. Приоритет:

1. Переменные, уже заданные процессу.
2. `/etc/projectsbis/projectsbis.env`, если файл существует.
3. Локальный `.env` в корне репозитория.

Загрузка выполняется с `override=False`: существующее окружение не
перезаписывается содержимым файла.

### 4.2. Пути

| Назначение | Локально | Production VPS |
|---|---|---|
| Репозиторий | `D:\GIT\ProjectSbis` | `/opt/projectsbis/repository` |
| Python | `python` после активации `.venv` | `/opt/projectsbis/repository/.venv/bin/python` |
| БД | `D:\GIT\ProjectSbis\data\project.db` | `/var/lib/projectsbis/project.db` |
| SBIS auth-state | `D:\GIT\ProjectSbis\data\sbis_auth_state.json` | `/var/lib/projectsbis/sbis_auth_state.json` |
| ENV | `D:\GIT\ProjectSbis\.env` | `/etc/projectsbis/projectsbis.env` |
| Шаблон запроса СБИС | `config/request.json` | `/opt/projectsbis/repository/config/request.json` |
| XLSX | `exports/` | `exports/` выбранного checkout либо `--output` |
| Tracking logs | консоль локального процесса | `journalctl -u projectsbis-tracking.service` |
| SMTP/Postfix logs | обычно не используются локально | `/var/log/mail.log` или journal Postfix |

На VPS обязательно задайте `PROJECT_DB_PATH=/var/lib/projectsbis/project.db`.
Без него код использует `data/project.db` внутри checkout.

### 4.3. Переменные окружения

| Переменная | Обязательность | Назначение |
|---|---|---|
| `PROJECT_DB_PATH` | рекомендована на VPS | Путь к SQLite |
| `SBIS_BROWSER_COOKIE` | обязательна для live-запросов СБИС | Cookie авторизованной браузерной сессии |
| `SBIS_AUTH_ALERT_EMAILS` | нужна для auth-alert | Отдельный список служебных получателей через запятую; fallback на `DAILY_REPORT_EMAILS` отсутствует |
| `SBIS_AUTH_STATE_PATH` | необязательна | Общий JSON state hourly/daily; Linux default `/var/lib/projectsbis/sbis_auth_state.json` |
| `SBIS_URL` | необязательна | RPC endpoint, по умолчанию `https://online.sbis.ru/service/` |
| `SBIS_SELECTION_ID` | фактически нужна загрузке выборки | Сейчас дополнительно проверяется в `src/sbis/client.py`, хотя выборка уже передаётся через CLI |
| `MAIL_FROM_EMAIL` | имеет default | Адрес отправителя |
| `MAIL_FROM_NAME` | имеет default | Имя отправителя |
| `MAIL_SMTP_HOST` | имеет default | SMTP host |
| `MAIL_SMTP_PORT` | имеет default `587` | Submission-порт |
| `MAIL_SMTP_USERNAME` | обязательна для SMTP | SMTP AUTH login |
| `MAIL_SMTP_PASSWORD` | обязательна для SMTP | SMTP AUTH password |
| `TEST_MAIL_EMAIL` | нужна для `--tracking-test` | Адрес tracking-теста |

Пример без секретов:

```env
PROJECT_DB_PATH=/var/lib/projectsbis/project.db
SBIS_BROWSER_COOKIE=...
SBIS_AUTH_ALERT_EMAILS=suglobovdima@ya.ru
SBIS_AUTH_STATE_PATH=/var/lib/projectsbis/sbis_auth_state.json
SBIS_SELECTION_ID=5984
SBIS_URL=https://online.sbis.ru/service/

MAIL_FROM_EMAIL=info@atlantis.ooo
MAIL_FROM_NAME=Атлантис
MAIL_SMTP_HOST=mail.projectsbis.ru
MAIL_SMTP_PORT=587
MAIL_SMTP_USERNAME=...
MAIL_SMTP_PASSWORD=...
TEST_MAIL_EMAIL=...
```

Не добавляйте `.env`, cookie, SMTP-пароль или production-БД в Git.

## 5. Работа с клиентами

Все команды выполняются из корня репозитория. CLI требует ровно один источник:
`--selection`, `--inn` или `--inn-file`.

### 5.1. Аргументы `src.client_loader`

| Аргумент | Значение |
|---|---|
| `--selection ID` | Получить текущий состав пользовательской выборки СБИС |
| `--inn INN` | Найти одну организацию по точному ИНН |
| `--inn-file PATH` | Прочитать ИНН из UTF-8/UTF-8-SIG файла, один на строку |
| `--enrich-limit N` | Обогатить не более `N` необогащённых клиентов выборки |
| `--enrich-all` | Обогатить всю очередь выборки или все найденные входные ИНН |
| `--skip-collection` | Не обновлять состав выборки из СБИС, а обогатить уже сохранённых клиентов `--selection` |

`--enrich-all` имеет приоритет над `--enrich-limit`. В режимах `--inn` и
`--inn-file` ограничение `--enrich-limit` не используется: обогащение выполняется
только с `--enrich-all`.

### 5.2. Загрузка выборки СБИС

Только синхронизировать состав выборки:

```powershell
python -m src.client_loader --selection 5984
```

Синхронизировать и проверить десять карточек:

```powershell
python -m src.client_loader --selection 5984 --enrich-limit 10
```

Синхронизировать и обогатить всю оставшуюся очередь:

```powershell
python -m src.client_loader --selection 5984 --enrich-all
```

Обогатить уже сохранённых клиентов выборки без повторного сбора её состава:

```powershell
python -m src.client_loader --selection 5984 --skip-collection --enrich-all
```

Для ограниченного продолжения замените `--enrich-all` на `--enrich-limit N`.
Параметр `--skip-collection` работает только с `--selection` и требует один из
режимов обогащения.

На VPS:

```bash
cd /opt/projectsbis/repository
.venv/bin/python -m src.client_loader --selection 5984 --enrich-all
```

Ожидаемый результат:

- все страницы выборки загружены через непрозрачный `nextPosition`;
- клиенты вставлены или обновлены;
- связи с выборкой записаны в `client_selections`;
- необогащённые клиенты последовательно обработаны;
- контакты заменены актуальным снимком карточки;
- `enriched` установлен в `1` после успешного сохранения.

`ContractorCard.Read` выполняется последовательно с паузой 3 секунды. При HTTP
429 код ждёт 65 секунд и повторяет тот же запрос без максимального числа повторов.
При потере соединения или сетевом тайм-ауте CLI повторяет сетевую операцию через 60 секунд,
затем через 120 секунд. После третьей неудачи он завершается без traceback; уже
записанные карточки остаются в БД. Другая необработанная ошибка прерывает весь
текущий запуск.

### 5.3. Одна организация по ИНН

```powershell
python -m src.client_loader --inn 251118147906 --enrich-all
```

Поиск выполняется через `Contractor.SearchSuggest`, затем найденный клиент
записывается в `clients`, а источник — в `client_sources` с типом `manual_inn`.
Связь с `client_selections` не создаётся.

### 5.4. Список ИНН из файла

```powershell
python -m src.client_loader --inn-file .\lists\clients.txt --enrich-all
```

Пустые строки игнорируются, одинаковые ИНН удаляются с сохранением исходного
порядка. Допустимы только строки из 10 или 12 цифр. Источник сохраняется как
`inn_file`, а значением становится имя файла без полного пути.

На VPS:

```bash
.venv/bin/python -m src.client_loader \
    --inn-file ./lists/clients.txt \
    --enrich-all
```

### 5.5. Проверка результата

Быстрая сводка по выборкам через XLSX:

```powershell
python -m src.sql_export --query selections_summary
```

Полная выгрузка конкретной выборки:

```powershell
python -m src.sql_export `
    --query selection_full_clients `
    --param selection_id=5984
```

Клиенты из файла ИНН:

```powershell
python -m src.sql_export `
    --query source_full_clients `
    --param source_type=inn_file `
    --param source_value=clients.txt
```

## 6. База данных

Основная БД — SQLite. `initialize_database()` создаёт таблицы, выполняет встроенные
проверки колонок и гарантирует наличие основной кампании. Отдельного инструмента
версий миграций нет.

### 6.1. Таблицы

| Таблица | Назначение |
|---|---|
| `clients` | Одна запись организации: идентификаторы СБИС, ИНН, реквизиты, директор, `enriched` |
| `client_contacts` | Телефоны и email клиента; актуальный снимок после обогащения |
| `client_selections` | Связь клиента с одной или несколькими выборками СБИС |
| `client_sources` | Источник ручного поиска: `manual_inn` или `inn_file` |
| `mail_campaigns` | Кампания, её выборка, шаблон и статус |
| `mail_recipients` | Конкретный email конкретного клиента в кампании и состояние очереди |
| `mail_messages` | Отдельная учётная попытка отправки, provider, status, token и `is_test` |
| `mail_events` | События попытки: `sent`, `failed`, `opened`, `clicked` и данные события |

### 6.2. Основные связи

```text
clients
├──< client_contacts
├──< client_selections
├──< client_sources
└──< mail_recipients >── mail_campaigns

mail_recipients
└──< mail_messages
    └──< mail_events
```

Разница сущностей:

- **Клиент** — организация в `clients`.
- **Получатель** — email клиента внутри конкретной кампании.
- **Попытка отправки** — одна запись `mail_messages`; retry создаёт новую попытку.
- **Событие** — факт, связанный с попыткой: отправка, ошибка, открытие или клик.

В текущем соединении SQLite код не включает `PRAGMA foreign_keys=ON`, WAL или
`busy_timeout`. Объявленные `ON DELETE CASCADE` без включённых foreign keys не
гарантируются. Для параллельной работы sender и tracking нужны короткие операции;
на production отдельно контролируйте блокировки SQLite.

## 7. Почтовые кампании

### 7.1. Основная кампания

`initialize_database()` вызывает `ensure_default_mail_campaign()` и обеспечивает:

```text
name = new_companies_daily
selection_id = 5984
template_name = new_companies
```

Обычно это `campaign_id=1`, но ID нельзя считать гарантированным для любой БД.
Перед запуском проверяйте фактическую запись. Статус кампании хранится в БД, но
текущий sender его не проверяет.

Инициализация локальной БД и default-кампании:

```powershell
python -c "from src.database import initialize_database; initialize_database(); print('DB и default campaign готовы')"
```

Получить список кампаний без изменения БД:

```powershell
python -c "import sqlite3; from src.config import DATABASE_FILE; c=sqlite3.connect(DATABASE_FILE.resolve().as_uri() + '?mode=ro', uri=True); print(c.execute('SELECT id, name, selection_id, template_name, status FROM mail_campaigns ORDER BY id').fetchall()); c.close()"
```

Функция `get_or_create_mail_campaign()` умеет создать произвольную кампанию по
имени и выборке, но отдельного CLI нет. Она не назначает шаблон новой кампании;
sender не сможет использовать кампанию без заполненного `template_name`.

### 7.2. Наполнение получателей

Автоматического вызова нет. После загрузки и обогащения выборки функцию нужно
запустить вручную.

PowerShell:

```powershell
$CampaignId = 1
python -c "import sys; from src.database import populate_mail_recipients; print('ADDED:', populate_mail_recipients(int(sys.argv[1])))" $CampaignId
```

VPS:

```bash
cd /opt/projectsbis/repository
CAMPAIGN_ID=1
.venv/bin/python -c "import sys; from src.database import populate_mail_recipients; print('ADDED:', populate_mail_recipients(int(sys.argv[1])))" "$CAMPAIGN_ID"
```

Результат `ADDED: N` — число новых строк `mail_recipients`.

### 7.3. Защита от повторной рассылки

Внутри кампании действует точная уникальность:

```text
(campaign_id, client_id, email)
```

Она не гарантирует уникальность одного email для разных `client_id` и чувствительна
к регистру/пробелам в хранимом значении.

Между кампаниями `populate_mail_recipients()` не добавляет email, если уже
существует сообщение со следующими признаками:

```text
LOWER(TRIM(old_email)) = LOWER(TRIM(new_email))
mail_messages.status = sent
mail_messages.is_test = 0
```

Проверка выполняется во время наполнения очереди, а не непосредственно перед SMTP.
Если две кампании были наполнены до первой отправки, sender повторно защиту не
проверит.

Production-проверка выполнена успешно:

```text
campaign 2 = production_test_4
→ 4 получателя
→ 4 реальные отправки

campaign 3 = production_test_4_repeat
→ ADDED: 0
```

Это подтверждает штатный последовательный сценарий: сначала успешная боевая
отправка, затем наполнение следующей кампании.

Важно: условие не проверяет provider. Успешный `--mock-send` создаёт `is_test=0`
и поэтому тоже считается уже обработанной отправкой для статистики и dedup.

## 8. Отправка писем

Точка входа:

```text
python -m src.mailing.sender
```

### 8.1. Аргументы sender

| Аргумент | Назначение |
|---|---|
| `--campaign-id ID` | Обязательный ID кампании |
| `--limit N` | Максимум pending-получателей; default `1` |
| `--dry-run` | Сформировать письмо без отправки и изменений БД |
| `--mock-send` | Имитировать успех и записать его в БД |
| `--smtp-send` | Использовать реальный SMTP |
| `--test-email LIST` | Отправить SMTP только на адреса из списка через запятую |
| `--tracking-test` | Отправить письмо с token на `TEST_MAIL_EMAIL` |
| `--confirm-real-send` | Разрешить SMTP на реальные адреса кампании |

Нужно выбрать ровно один режим: `--dry-run`, `--mock-send`, `--smtp-send` или
`--tracking-test`.

### 8.2. Поведение режимов

| Режим | Реальная отправка | `mail_messages` | `is_test` | Статус получателя | Влияние на статистику/dedup |
|---|---:|---:|---:|---|---|
| `--dry-run` | Нет | Нет | — | Не меняется | Нет |
| `--mock-send` | Нет | Да | `0` | `sent` при mock-успехе | **Да, считается боевым** |
| `--smtp-send --test-email ...` | Только тестовым адресам | Нет | — | Не меняется | Нет; отправка не аудируется в БД |
| `--tracking-test` | На `TEST_MAIL_EMAIL` | Да | `1` | Не меняется | Исключается из боевых метрик/dedup |
| `--smtp-send --confirm-real-send` | Да, клиентам кампании | Да | `0` | `sent` или `failed` | Да |

### 8.3. Dry-run

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --dry-run
```

Выводит адрес и тему. HTML больше не печатается в CLI. БД не изменяется.

### 8.4. Mock-send

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --mock-send
```

> **Внимание:** это не безвредный preview. Текущий код создаёт полноценное
> нетестовое сообщение, переводит реального получателя в `sent`, включает запись
> в метрики и межкампанейную защиту. Для просмотра используйте `--dry-run`.

### 8.5. Безопасный SMTP test

```powershell
python -m src.mailing.sender `
    --campaign-id 1 `
    --limit 1 `
    --smtp-send `
    --test-email "test1@example.com,test2@example.com"
```

Для каждого выбранного pending-получателя письмо отправляется каждому адресу из
`--test-email`. При `--limit 2` и четырёх тестовых адресах получится до восьми
реальных SMTP-отправок. Режим не создаёт `mail_messages`, поэтому подтверждение
ищите в консоли и SMTP/Postfix-логах.

### 8.6. Tracking-test

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --tracking-test
```

Требует `TEST_MAIL_EMAIL`. Создаёт token и `mail_messages.is_test=1`, отправляет
письмо на тестовый адрес, но оставляет реального получателя в `pending`.

### 8.7. Реальная отправка

> **ОПАСНО: следующая команда отправляет письма реальным клиентам и изменяет
> production-БД. Сначала выполните populate, dry-run, проверку pending, ID кампании
> и небольшой `--limit`.**

PowerShell:

```powershell
$CampaignId = 1
$Limit = 4
python -m src.mailing.sender `
    --campaign-id $CampaignId `
    --limit $Limit `
    --smtp-send `
    --confirm-real-send
```

VPS:

```bash
cd /opt/projectsbis/repository
CAMPAIGN_ID=1
LIMIT=4
.venv/bin/python -m src.mailing.sender \
    --campaign-id "$CAMPAIGN_ID" \
    --limit "$LIMIT" \
    --smtp-send \
    --confirm-real-send
```

Порядок учёта:

```text
create_mail_message(status=pending, token)
→ build_mail_message(token)
→ SMTP
→ complete_mail_message(sent/failed)
→ mail_events += sent/failed
```

SMTP success означает, что сервер принял письмо. Удалённую доставку проверяйте
по Postfix-логам, DSN и фактическому почтовому ящику.

## 9. Шаблоны писем

`mail_campaigns.template_name` передаётся в
`src/mailing/templates/registry.py::get_mail_template()`. Сейчас зарегистрирован
один шаблон:

```text
new_companies → src/mailing/templates/new_companies.py
```

Шаблон формирует:

- `build_subject()` — тему;
- `build_text_body()` — plain-text часть;
- `build_html_body()` — HTML;
- `build_greeting()` — обращение по имени и отчеству;
- `build_click_url()` — tracked-ссылку при наличии token.

HTML использует CID `atlantis-logo`; PNG прикладывается в
`src/mailing/smtp_provider.py`. Tracking token добавляется только для учётных
mock/real SMTP и tracking-test. У dry-run и `--test-email` token отсутствует.

Фактические контактные действия:

| `click_key` | Назначение |
|---|---|
| `cta_email` | `mailto:` на `info@projectsbis.ru` |
| `phone` | телефонный вызов |
| `whatsapp` | WhatsApp с подготовленным текстом |
| `telegram` | Telegram |
| `max` | MAX |

`TRACKING_BASE_URL` и конечные контактные URL сейчас жёстко заданы в шаблоне и
продублированы в `src/tracking/app.py`; при изменении проверяйте оба файла.
Plain-text ссылки не проходят через click tracking.

## 10. Open tracking

Полная цепочка:

```text
HTML-письмо
→ https://mail.projectsbis.ru/t/o/<tracking_token>.gif
→ Nginx
→ 127.0.0.1:8080
→ projectsbis-tracking.service
→ aiohttp handle_open_tracking()
→ record_mail_open()
→ mail_events(event_type=opened)
→ прозрачный GIF 1×1, HTTP 200
```

Production-цепочка подтверждена runtime-тестом: публичный URL вернул GIF с HTTP
200, а событие `opened` появилось в production SQLite.

Проверить сервис на VPS:

```bash
sudo systemctl status projectsbis-tracking.service --no-pager
sudo journalctl -u projectsbis-tracking.service -n 100 --no-pager
```

Проверить endpoint реальным GET-запросом:

```bash
TOKEN='replace-with-real-tracking-token'
curl -sS -D - \
    -o /tmp/projectsbis-open.gif \
    "https://mail.projectsbis.ru/t/o/$TOKEN.gif"
file /tmp/projectsbis-open.gif
```

Ожидается HTTP 200, `Content-Type: image/gif` и GIF 1×1. Неизвестный token тоже
возвращает тот же GIF, но событие в БД не создаётся.

Проверить события конкретного token в production SQLite:

```bash
TRACKING_TOKEN="$TOKEN" .venv/bin/python -c "import os, sqlite3; from src.config import DATABASE_FILE; uri=DATABASE_FILE.resolve().as_uri() + '?mode=ro'; c=sqlite3.connect(uri, uri=True); rows=c.execute('SELECT me.event_type, me.event_at, me.event_data FROM mail_events AS me INNER JOIN mail_messages AS mm ON mm.id=me.message_id WHERE mm.tracking_token=? ORDER BY me.id', (os.environ['TRACKING_TOKEN'],)).fetchall(); print(rows); c.close()"
```

Каждая загрузка pixel создаёт отдельное событие. Почтовые proxy, preview и
антиспам-системы могут загрузить изображение без ручного открытия пользователем.

## 11. Click tracking

Цепочка:

```text
ссылка в HTML
→ /t/c/<tracking_token>/<click_key>
→ Nginx
→ aiohttp handle_click_tracking()
→ record_mail_click()
→ mail_events(event_type=clicked)
→ HTTP 302
→ адрес из CLICK_TARGETS
```

Допустимые `click_key`: `cta_email`, `phone`, `whatsapp`, `telegram`, `max`.
Другой ключ возвращает 404. URL назначения не принимается от пользователя,
поэтому endpoint не является открытым redirect.

`event_data` хранится как компактный JSON:

```json
{"click_key":"whatsapp"}
```

Проверка перехода на WhatsApp без автоматического следования redirect:

```bash
TOKEN='replace-with-real-tracking-token'
curl -sS -D - \
    -o /dev/null \
    "https://mail.projectsbis.ru/t/c/$TOKEN/whatsapp"
```

Ожидается HTTP 302 и `Location` на разрешённый WhatsApp URL. Production runtime-
тест подтвердил цепочку до конечного WhatsApp и запись `clicked` в SQLite.

Каждый запрос записывает отдельный клик. Антиспам-сканеры ссылок могут создавать
события без осознанного перехода пользователя.

## 12. Метрики рассылки

`get_mail_campaign_stats(campaign_id)` возвращает:

| Поле | Значение |
|---|---|
| `messages_sent` | Число `mail_messages.status=sent` с `is_test=0` |
| `opens_total` | Все события `opened` |
| `opened_unique` | Число уникальных `mail_messages.id` хотя бы с одним open |
| `open_rate` | `opened_unique / messages_sent × 100` |
| `clicks_total` | Все события `clicked` |
| `clicked_unique` | Число уникальных сообщений хотя бы с одним click |
| `click_rate` | `clicked_unique / messages_sent × 100` |
| `click_to_open_rate` | `clicked_unique / opened_unique × 100` |
| `click_phone` | Уникальные сообщения с кликом `phone` |
| `click_whatsapp` | Уникальные сообщения с кликом `whatsapp` |
| `click_telegram` | Уникальные сообщения с кликом `telegram` |
| `click_max` | Уникальные сообщения с кликом `max` |
| `click_cta_email` | Уникальные сообщения с кликом `cta_email` |

Также возвращаются количество получателей и их статусы: `pending`, `sent`,
`delivered`, `opened`, `clicked`, `bounced`, `failed`, `unsubscribed`.
Tracking-события сейчас не меняют статус `mail_recipients` на `opened/clicked`,
поэтому event-метрики нужно смотреть в полях `opens_*`, `clicks_*` и rates.

Полная статистика в консоль:

```powershell
$CampaignId = 1
python -c "import sys; from pprint import pprint; from src.database import get_mail_campaign_stats; pprint(get_mail_campaign_stats(int(sys.argv[1])), sort_dicts=False)" $CampaignId
```

На VPS:

```bash
CAMPAIGN_ID=1
.venv/bin/python -c "import sys; from pprint import pprint; from src.database import get_mail_campaign_stats; pprint(get_mail_campaign_stats(int(sys.argv[1])), sort_dicts=False)" "$CAMPAIGN_ID"
```

`src.mailing.dry_run` выводит только статусы получателей и первые pending-записи;
расширенные engagement-поля он пока не печатает.

Тесты с `is_test=1` исключаются. Успешный `mock-send` имеет `is_test=0`, поэтому
входит в `messages_sent`, rates и межкампанейную защиту.

## 13. Retry и ошибки

Неуспешная учётная отправка создаёт:

```text
mail_messages.status = failed
mail_recipients.status = failed
mail_events.event_type = failed
mail_events.event_data = текст ошибки
```

`MAX_MAIL_SEND_ATTEMPTS = 3`. Retry разрешён только для получателя в состоянии
`failed`, если число его `mail_messages` меньше трёх. Счётчик включает тестовые
попытки, потому что фильтра по `is_test` в нём нет.

Посмотреть failed-получателей:

```powershell
$CampaignId = 1
python -c "import sys; from pprint import pprint; from src.database import get_failed_mail_recipients; pprint(get_failed_mail_recipients(int(sys.argv[1])))" $CampaignId
```

Команда выводит данные получателей, включая email и ИНН.

Вернуть одного получателя в `pending`:

```powershell
$RecipientId = 42
python -c "import sys; from src.database import retry_failed_mail_recipient; retry_failed_mail_recipient(int(sys.argv[1])); print('RETRY: pending')" $RecipientId
```

На VPS:

```bash
RECIPIENT_ID=42
.venv/bin/python -c "import sys; from src.database import retry_failed_mail_recipient; retry_failed_mail_recipient(int(sys.argv[1])); print('RETRY: pending')" "$RECIPIENT_ID"
```

После этого sender снова увидит запись в pending-очереди. Автоматического retry,
backoff, CLI-команды retry или обработки failed внутри daily workflow пока нет.

## 14. SQL и экспорт

Экспортёр разрешает только read-only `SELECT`/`WITH`, проверяет запрещённые
команды и дополнительно открывает SQLite через URI `mode=ro`.

### 14.1. Аргументы

| Аргумент | Назначение |
|---|---|
| `--list` | Показать имена `sql/*.sql` |
| `--query NAME` | Запустить именованный запрос |
| `--file PATH` | Запустить внешний SQL-файл |
| `--param key=value` | Именованный SQLite-параметр; можно повторять |
| `--output PATH` | Явный путь итогового XLSX |
| `--sheet NAME` | Название листа, default `Результат` |

`--file` и `--query` взаимоисключающие.

### 14.2. Команды

Список запросов:

```powershell
python -m src.sql_export --list
```

Доступны:

```text
all_full_clients
get_all_clients
selection_full_clients
selections_summary
source_full_clients
```

Экспорт всех клиентов выборок:

```powershell
python -m src.sql_export --query all_full_clients
```

Экспорт одной выборки:

```powershell
python -m src.sql_export `
    --query selection_full_clients `
    --param selection_id=5984
```

Экспорт выборки по списку инн :

```powershell
python -m src.sql_export `
    --query source_full_clients `
    --param source_type=manual_inn `
    --param source_value=251118147906`
```

Запуск внешнего SQL и собственный путь:

```powershell
python -m src.sql_export `
    --file .\data\test.sql `
    --output .\exports\test-result.xlsx `
    --sheet "Проверка"
```

Если `--output` не указан, результат сохраняется как
`exports/<source>_<timestamp>.xlsx`. Книга содержит лист результата и лист
`Информация` с SQL, параметрами, временем и количеством строк.

## 15. Production VPS

### 15.1. Основные пути

```text
Repository: /opt/projectsbis/repository
Python:     /opt/projectsbis/repository/.venv/bin/python
Database:   /var/lib/projectsbis/project.db
ENV:        /etc/projectsbis/projectsbis.env
Tracking:   projectsbis-tracking.service
Local HTTP: 127.0.0.1:8080
Public URL: https://mail.projectsbis.ru
```

### 15.2. Обновление кода

```bash
cd /opt/projectsbis/repository
git status --short --branch
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
```

Не продолжайте `git pull`, если в production checkout есть неожиданные локальные
изменения. Production-БД и env находятся вне репозитория и не должны затрагиваться
Git-обновлением.

После изменения tracking-кода:

```bash
sudo systemctl restart projectsbis-tracking.service
sudo systemctl status projectsbis-tracking.service --no-pager
```

`restart` кратковременно прерывает tracking; используйте его только после
успешного обновления и проверки кода.

### 15.3. Tracking и Nginx

```bash
sudo systemctl is-active projectsbis-tracking.service
sudo systemctl status projectsbis-tracking.service --no-pager
sudo journalctl -u projectsbis-tracking.service -n 100 --no-pager

sudo nginx -t
sudo systemctl status nginx --no-pager
sudo journalctl -u nginx -n 100 --no-pager
```

Nginx проксирует публичные `/t/o/...` и `/t/c/...` на `127.0.0.1:8080`.
Unit-файл и Nginx-конфигурация подтверждены на VPS, но не хранятся в текущем Git-
репозитории.

### 15.4. SMTP/Postfix

```bash
sudo systemctl status postfix --no-pager
sudo journalctl -u postfix -n 100 --no-pager
sudo tail -n 100 /var/log/mail.log
```

Проверяйте Message-ID из вывода sender, ответ локального Postfix и дальнейшую
доставку на удалённый MX. `sent` в ProjectSbis означает принятие сообщения SMTP-
провайдером, а не подтверждённое чтение.

### 15.5. Что запускать где

| Операция | Windows | Production VPS |
|---|---:|---:|
| Разработка и чтение кода | Да | Диагностика checkout |
| Локальный SQL/XLSX | Да | Да, если нужен production-снимок |
| Dry-run шаблона | Да | Да |
| Mock-send | Только осознанно | Не рекомендуется из-за влияния на БД |
| Сбор production-выборки | Не для server-only workflow | Да |
| Наполнение production-кампании | Нет | Да |
| Tracking-test | Возможно с тестовой конфигурацией | Да |
| Реальная SMTP-рассылка | Не рекомендуется | Да |
| Tracking HTTP-service | Для локальной отладки | Постоянно на VPS |

### 15.6. Управление ежедневным systemd timer

Unit-файлы хранятся в репозитории:

```text
deploy/projectsbis-daily.service
deploy/projectsbis-daily.timer
```

Timer запускает production-команду `src.daily_run` ежедневно в `09:00` по
системному часовому поясу VPS `Asia/Vladivostok`. `Persistent=true` означает,
что пропущенный во время выключения сервера календарный запуск будет выполнен
после следующей активации timer. Параллельный второй `daily_run` останавливается
OS-level блокировкой до создания `mail_runs` и отправки писем.

Установка unit-файлов:

```bash
cd /opt/projectsbis/repository
sudo cp deploy/projectsbis-daily.service /etc/systemd/system/
sudo cp deploy/projectsbis-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

Проверка синтаксиса перед включением:

```bash
sudo systemd-analyze verify \
    /etc/systemd/system/projectsbis-daily.service \
    /etc/systemd/system/projectsbis-daily.timer
```

Включение автоматического запуска:

```bash
sudo systemctl enable --now projectsbis-daily.timer
```

Проверка расписания, состояния и журналов:

```bash
systemctl list-timers projectsbis-daily.timer
systemctl is-enabled projectsbis-daily.timer
systemctl is-active projectsbis-daily.timer
systemctl status projectsbis-daily.timer --no-pager
systemctl status projectsbis-daily.service --no-pager
journalctl -u projectsbis-daily.service -n 100 --no-pager
```

Ручной запуск выполняет настоящую production-рассылку согласно `ExecStart`:

```bash
sudo systemctl start projectsbis-daily.service
```

Отключение автоматического запуска:

```bash
sudo systemctl disable --now projectsbis-daily.timer
```

Наличие unit-файлов в Git само по себе не означает, что timer установлен или
включён на VPS. Это подтверждается только командами `is-enabled`, `is-active`
и `list-timers` на production-сервере.

### 15.7. Управление read-only админкой через systemd

Unit-файл админки хранится в репозитории:

```text
deploy/projectsbis-admin.service
```

Сервис постоянно запускает `python -m src.admin.app` из production checkout.
Само приложение привязано только к `127.0.0.1:8081`; unit не меняет адрес
прослушивания и не публикует админку наружу.

Установка unit-файла:

```bash
cd /opt/projectsbis/repository
sudo cp deploy/projectsbis-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Проверка синтаксиса установленного unit-файла:

```bash
sudo systemd-analyze verify /etc/systemd/system/projectsbis-admin.service
```

Включение автозапуска и немедленный запуск:

```bash
sudo systemctl enable --now projectsbis-admin.service
```

Отдельные команды запуска, перезапуска и остановки:

```bash
sudo systemctl start projectsbis-admin.service
sudo systemctl restart projectsbis-admin.service
sudo systemctl stop projectsbis-admin.service
```

Проверка состояния и журналов:

```bash
systemctl is-enabled projectsbis-admin.service
systemctl is-active projectsbis-admin.service
systemctl status projectsbis-admin.service --no-pager
journalctl -u projectsbis-admin.service -n 100 --no-pager
journalctl -u projectsbis-admin.service -f
```

Проверка локального адреса после запуска:

```bash
curl -I http://127.0.0.1:8081/admin
ss -ltnp | grep '127.0.0.1:8081'
```

Отключение автозапуска с остановкой сервиса:

```bash
sudo systemctl disable --now projectsbis-admin.service
```

Наличие unit-файла и этих команд в Git не означает, что сервис уже установлен
или запущен на VPS. Это проверяется только через `systemctl` на сервере.

### 15.8. Почасовой контроль SBIS_BROWSER_COOKIE

`Contractor.SearchSuggest` использует браузерную cookie СБИС. Для раннего
обнаружения HTTP 401/403 есть отдельная безопасная проверка, которая не читает
клиентов из БД и не изменяет БД:

```bash
cd /opt/projectsbis/repository
sudo .venv/bin/python -m src.sbis.cookie_manager --check
```

Exit codes:

- `0` — cookie подтверждена как valid;
- `2` — invalid auth: HTTP 401/403, auth redirect или JSON-RPC auth error;
- `1` — timeout, network/server, HTTP 5xx либо ошибка общего state.

Режим с одноразовым служебным уведомлением:

```bash
sudo .venv/bin/python -m src.sbis.cookie_manager --check --alert
```

Alert отправляется только на `SBIS_AUTH_ALERT_EMAILS`. Значение
`DAILY_REPORT_EMAILS` не используется как fallback. Timeout и HTTP 5xx не
считаются истёкшей cookie и не вызывают auth-alert.

#### State machine без повторных писем

Hourly-check и preflight `src.daily_run` используют один state-файл:

```text
/var/lib/projectsbis/sbis_auth_state.json
```

Логика одного эпизода:

```text
HEALTHY + invalid → ALERTED + одна alert-попытка
ALERTED + invalid → ALERTED, повторное письмо подавлено
ALERTED + valid   → HEALTHY
network / 5xx     → статус HEALTHY/ALERTED не меняется
```

Переход `HEALTHY → ALERTED` фиксируется до SMTP-вызова. Поэтому даже ошибка
отправки alert не разрешает повторные письма каждый час. Новый alert станет
возможен только после хотя бы одной успешной проверки `valid`.

State содержит только:

```json
{
  "status": "healthy|alerted",
  "last_check_at": "...",
  "last_invalid_at": "...",
  "last_valid_at": "...",
  "last_http_status": 401
}
```

Cookie и HTTP-заголовки в state не сохраняются. JSON публикуется атомарно,
read-modify-write защищён OS-level advisory lock. Отсутствующий или повреждённый
state безопасно восстанавливается; первая invalid-проверка после этого снова
может отправить alert.

`src.daily_run` проверяет cookie до `initialize_database()`, создания
`mail_runs`, загрузки selection и SMTP-рассылки. Если hourly-check уже перевёл
state в `ALERTED`, daily run остановится с auth failure, но письмо не продублирует.

#### Безопасная замена cookie

```bash
cd /opt/projectsbis/repository
sudo .venv/bin/python -m src.sbis.cookie_manager
```

Новая cookie вводится через `getpass` и сначала проверяется. При invalid env не
изменяется. При valid менеджер атомарно заменяет только
`SBIS_BROWSER_COOKIE`, сохраняет остальные строки, права и владельца env,
создаёт backup и переводит auth-state в `HEALTHY`.

#### Установка hourly timer

Unit-файлы:

```text
deploy/projectsbis-sbis-auth-check.service
deploy/projectsbis-sbis-auth-check.timer
```

Установка и проверка синтаксиса:

```bash
cd /opt/projectsbis/repository
sudo cp deploy/projectsbis-sbis-auth-check.service /etc/systemd/system/
sudo cp deploy/projectsbis-sbis-auth-check.timer /etc/systemd/system/

sudo systemd-analyze verify \
    /etc/systemd/system/projectsbis-sbis-auth-check.service \
    /etc/systemd/system/projectsbis-sbis-auth-check.timer

sudo systemctl daemon-reload
```

Включение:

```bash
sudo systemctl enable --now projectsbis-sbis-auth-check.timer
```

Проверка расписания, состояния и логов:

```bash
systemctl list-timers projectsbis-sbis-auth-check.timer
systemctl is-enabled projectsbis-sbis-auth-check.timer
systemctl is-active projectsbis-sbis-auth-check.timer
systemctl status projectsbis-sbis-auth-check.timer --no-pager
systemctl status projectsbis-sbis-auth-check.service --no-pager
sudo journalctl -u projectsbis-sbis-auth-check.service -n 100 --no-pager
```

Ручной запуск service использует `--check --alert` и при новом invalid-эпизоде
может отправить служебное письмо:

```bash
sudo systemctl start projectsbis-sbis-auth-check.service
```

Отключение:

```bash
sudo systemctl disable --now projectsbis-sbis-auth-check.timer
```

`OnCalendar=hourly` запускает проверку каждый час. `Persistent=true` выполняет
одну пропущенную проверку после следующего запуска системы. Наличие unit-файлов
в Git не подтверждает, что timer установлен или включён на production VPS.

## 16. Типовые сценарии

### 16.1. Получить и обогатить выборку 5984

Условия: production env доступен, cookie действительна, путь БД проверен.

```bash
cd /opt/projectsbis/repository
.venv/bin/python -m src.client_loader --selection 5984 --enrich-all
```

Ожидается синхронизация выборки, обработка очереди и сообщение об оставшемся числе
необогащённых клиентов. Проверка — `selections_summary` или SQL к production-БД.

### 16.2. Обработать один ИНН

```powershell
python -m src.client_loader --inn 251118147906 --enrich-all
```

Ожидается `SppUuid`, `clients.id` и сообщение `Карточка сохранена`.

### 16.3. Обработать файл ИНН

```powershell
python -m src.client_loader --inn-file .\lists\clients.txt --enrich-all
```

Проверка — экспорт `source_full_clients` с `source_type=inn_file` и точным именем
файла в `source_value`.

### 16.4. Создать получателей кампании

Условия: выборка уже синхронизирована и обогащена; ID кампании проверен.

```bash
CAMPAIGN_ID=1
.venv/bin/python -c "import sys; from src.database import populate_mail_recipients; print('ADDED:', populate_mail_recipients(int(sys.argv[1])))" "$CAMPAIGN_ID"
.venv/bin/python -m src.mailing.dry_run --campaign-id "$CAMPAIGN_ID" --limit 10
```

Ожидается `ADDED: N`, затем количество pending и первые получатели.

### 16.5. Безопасно проверить шаблон

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --dry-run
```

Реальной отправки и изменений БД нет.

### 16.6. Отправить безопасный SMTP test

```bash
.venv/bin/python -m src.mailing.sender \
    --campaign-id 1 \
    --limit 1 \
    --smtp-send \
    --test-email "test1@example.com,test2@example.com"
```

Ожидается SMTP result для каждого тестового адреса. Проверять по консоли,
Postfix-логам и входящим ящикам; БД этот режим не аудирует.

### 16.7. Отправить tracking-test

```bash
.venv/bin/python -m src.mailing.sender \
    --campaign-id 1 \
    --limit 1 \
    --tracking-test
```

Ожидается тестовое письмо с token и `mail_messages.is_test=1`. После открытия или
клика проверить `mail_events` по token.

### 16.8. Выполнить настоящую SMTP-рассылку

> **ОПАСНО: реальная отправка клиентам.**

```bash
CAMPAIGN_ID=1
LIMIT=4
.venv/bin/python -m src.mailing.sender \
    --campaign-id "$CAMPAIGN_ID" \
    --limit "$LIMIT" \
    --smtp-send \
    --confirm-real-send
```

Сначала выполните dry-run. После запуска проверьте sender output, статистику
кампании и Postfix-логи.

### 16.9. Проверить открытие

```bash
TOKEN='replace-with-real-tracking-token'
curl -sS -D - -o /tmp/projectsbis-open.gif \
    "https://mail.projectsbis.ru/t/o/$TOKEN.gif"
```

Ожидается HTTP 200 и новое событие `opened` для token.

### 16.10. Проверить клик

```bash
TOKEN='replace-with-real-tracking-token'
curl -sS -D - -o /dev/null \
    "https://mail.projectsbis.ru/t/c/$TOKEN/whatsapp"
```

Ожидается HTTP 302, WhatsApp в `Location` и событие `clicked` с
`{"click_key":"whatsapp"}`.

### 16.11. Посмотреть статистику кампании

```bash
CAMPAIGN_ID=1
.venv/bin/python -c "import sys; from pprint import pprint; from src.database import get_mail_campaign_stats; pprint(get_mail_campaign_stats(int(sys.argv[1])), sort_dicts=False)" "$CAMPAIGN_ID"
```

### 16.12. Повторить failed-отправку

Сначала получить failed и выбрать `recipient_id`, затем вернуть его в pending:

```bash
CAMPAIGN_ID=1
.venv/bin/python -c "import sys; from pprint import pprint; from src.database import get_failed_mail_recipients; pprint(get_failed_mail_recipients(int(sys.argv[1])))" "$CAMPAIGN_ID"

RECIPIENT_ID=42
.venv/bin/python -c "import sys; from src.database import retry_failed_mail_recipient; retry_failed_mail_recipient(int(sys.argv[1])); print('RETRY: pending')" "$RECIPIENT_ID"
```

После этого повторно запустить sender с небольшим `--limit`.

### 16.13. Экспортировать данные в XLSX

```powershell
python -m src.sql_export `
    --query selection_full_clients `
    --param selection_id=5984 `
    --output .\exports\selection-5984.xlsx
```

Ожидается путь сохранённого файла и количество строк.

### 16.14. Сброс тестовой выборки

```powershell
python -m src.mailing.reset_test_selection --yes
```
```powershell
cd /opt/projectsbis/repository

.venv/bin/python -m src.mailing.reset_test_selection --yes
```

### 16.15. Отправка репорта по отчетам 

```powershell
python -m src.mailing.daily_report \
    --campaign-id 2
```
```powershell
cd /opt/projectsbis/repository

.venv/bin/python -m src.mailing.daily_report \
    --campaign-id 2
```


### 16.16 Тест полного цикла на сервере

Сначала reset:
```
cd /opt/projectsbis/repository
.venv/bin/python -m src.mailing.reset_test_selection --yes
```

После него тестовые компании остаются, но вся старая история рассылки для 990001 исчезает.

Потом одна команда полного теста:
```
.venv/bin/python -m src.daily_run \
    --selection 990001 \
    --skip-load \
    --send \
    --confirm-real-send
```
## 17. Что автоматизировано, а что ещё нет

Статусы оценивают не только наличие функции, но и включение в рабочий процесс.

| Функция | Статус | Комментарий |
|---|:---:|---|
| Загрузка выборки СБИС | ⚠️ | Входит в `src.daily_run`; установка и включение timer на VPS требуют проверки |
| Поиск по одному/списку ИНН | ✅ | Рабочий CLI |
| Обогащение | ✅ | Рабочий последовательный этап |
| Инициализация default-кампании | ✅ | Выполняется из `initialize_database()` |
| Наполнение `mail_recipients` | ⚠️ | Функция готова, вызывается вручную |
| Exact dedup внутри кампании | ✅ | Для точной тройки campaign/client/email |
| Уникальность email внутри кампании | ⚠️ | Один email у разных клиентов не исключается |
| Защита между кампаниями | ✅ | Реализована и проверена production-тестом 4 → 0 |
| Dry-run | ✅ | Не меняет БД |
| SMTP test на заданные адреса | ✅ | Не меняет БД, требует внешней проверки результата |
| Реальная SMTP-отправка | ✅ | Есть явное CLI-подтверждение; production-тест пройден |
| Open tracking | ✅ | Реализован и end-to-end проверен на VPS |
| Click tracking | ✅ | Реализован и проверен до WhatsApp |
| Расчёт engagement-метрик | ✅ | Доступен через функцию БД |
| Полный CLI-отчёт метрик | ⚠️ | `dry_run` не выводит расширенные поля |
| Retry failed | ⚠️ | Ручная функция, автоматического цикла нет |
| Ограничение retry | ✅ | Максимум три учётные попытки |
| Изоляция `tracking-test` | ✅ | `is_test=1`, статус получателя не меняется |
| Изоляция `mock-send` | ❌ | Mock считается нетестовой отправкой |
| Tracking systemd service | ✅ | Развёрнут на VPS, но unit не хранится в репозитории |
| Daily orchestration | ✅ | Единая команда `python -m src.daily_run` |
| Daily итоговый отчёт | ✅ | Отправляется в конце полного production workflow |
| Scheduler/systemd timer daily | ⚠️ | Unit-файлы есть в `deploy/`; установка и включение на VPS пока не подтверждены |
| SBIS auth preflight | ✅ | Выполняется до БД, selection, `mail_runs` и рассылки |
| Hourly SBIS auth timer | ⚠️ | Код и unit-файлы готовы; установка и включение на VPS требуют подтверждения |
| Auth-alert dedup | ✅ | Общий атомарный state hourly/daily разрешает одну alert-попытку на invalid-эпизод |
| Delivered/bounced ingestion | ✅ | `src.daily_run` синхронизирует статусы по журналу Postfix |
| Unsubscribe | ❌ | Endpoint и suppression-механизм отсутствуют |

Текущий daily-оркестратор последовательно выполняет:

```text
SBIS browser cookie preflight
→ valid: state HEALTHY, продолжение
→ invalid: state ALERTED, не более одного auth-alert, остановка
→ initialize_database
→ создание mail_run
→ синхронизация selection 5984
→ обогащение
→ populate_mail_recipients
→ отправка pending
→ синхронизация delivered/bounced/deferred по Postfix
→ daily report
→ завершение mail_run с итоговыми счётчиками
```

Tracking HTTP-service не следует запускать внутри daily-задачи: это отдельный
постоянно работающий процесс.

## 18. Известные особенности и расхождения

- `docs/PROJECT_WORKFLOW_v5.md` называет public tracking будущим этапом. Фактически
  open/click endpoints реализованы и end-to-end проверены на production VPS.
- Daily-оркестратор и unit-файлы systemd находятся в репозитории. Их установка,
  включение и следующий запуск timer должны подтверждаться отдельно на VPS.
- `src/main.py` импортирует отсутствующую функцию конфигурации и использует старый
  интерфейс `get_contractor_card()`. Не используйте его.
- `src/sbis/client_enrichment.py` и отдельный
  `src/sbis/build_contractor_card_payload.py` не входят в рабочий поток.
- CLI-выборка записывается в JSON payload, но `src/sbis/client.py` дополнительно
  требует переменную `SBIS_SELECTION_ID`, значение которой затем не использует.
- `get_all_clients.sql` может создавать комбинации email × phone. Для одной строки
  на клиента используйте `all_full_clients` или `selection_full_clients`.
- `client_selections` подтверждает наличие клиента, но синхронизация не удаляет
  старую связь, если клиент исчез из выборки СБИС.
- `enriched` глобален для клиента, а не для отдельной выборки.
- Campaign status хранится, но sender его не проверяет.
- `record_mail_open()` и `record_mail_click()` записывают каждый запрос отдельным
  событием; rate limit и фильтрации proxy/bot сейчас нет.
- Полноценного входящего ящика, unsubscribe, delivered/bounced ingestion и
  автоматических тестов в репозитории нет.

Перед каждым production-запуском проверяйте Git revision, env-файл, путь SQLite,
ID кампании, pending-очередь, лимит и выбранный режим sender.
