# ProjectSbis — инструкция по текущей версии

## 1. Назначение

Текущая версия проекта умеет:

- получать клиентов из пользовательской выборки СБИС через `CRMClients.ListClientsOnline`;
- проходить пагинацию выборки;
- сохранять клиентов в SQLite;
- связывать одного клиента с одной или несколькими выборками через `client_selections`;
- определять клиентов, которым ещё не выполнялся `ContractorCard.Read`;
- получать подробную карточку клиента;
- извлекать реквизиты организации, руководителя, телефоны и e-mail;
- сохранять обогащённые данные в БД;
- выдерживать паузу между запросами `ContractorCard.Read`;
- при HTTP 429 ждать снятия блокировки и повторять тот же запрос;
- экспортировать результаты SQL-запросов в оформленный XLSX;
- автоматически подхватывать избранные `.sql` из каталога `sql/`;
- создавать в Excel лист `Информация` с параметрами экспорта;
- выполнять SQL-экспорт в строго read-only режиме;
- запускаться из PowerShell или через конфигурации VS Code;
- поддерживать клиентов как по `spp_uuid`, так и по `contractor_id` (`ИдО`);
- использовать выборку `5984` как источник новых организаций для ежедневной рассылки;
- создавать почтовые кампании и связывать их с шаблонами;
- формировать очередь `mail_recipients`;
- выполнять безопасный `dry-run` без изменения БД;
- выполнять `mock-send` с фиктивным провайдером;
- сохранять историю попыток отправки в `mail_messages`;
- хранить события писем в `mail_events`;
- повторно ставить failed-получателей в очередь вручную;
- ограничивать количество повторных попыток отправки.

Основной модуль загрузки клиентов:

```text
src/client_loader.py
```

Основной модуль экспорта:

```text
src/sql_export.py
```

Реестр SQL-запросов:

```text
src/sql_queries.py
```

Основные модули рассылки:

```text
src/mailing/dry_run.py
src/mailing/sender.py
src/mailing/templates/registry.py
src/mailing/templates/new_companies.py
```

Каталог избранных SQL:

```text
sql/
```

---

## 2. Рекомендуемая структура проекта

```text
ProjectSbis/
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── PROJECT_WORKFLOW.md
├── SQL_CHEATSHEET.md
├── config/
│   └── request.json
├── data/
│   ├── project.db
│   └── raw/
├── exports/
├── sql/
│   ├── all_full_clients.sql
│   ├── selection_full_clients.sql
│   └── selections_summary.sql
├── src/
│   ├── __init__.py
│   ├── client_loader.py
│   ├── config.py
│   ├── database.py
│   ├── sql_export.py
│   ├── sql_queries.py
│   ├── mailing/
│   │   ├── __init__.py
│   │   ├── dry_run.py
│   │   ├── sender.py
│   │   └── templates/
│   │       ├── __init__.py
│   │       ├── registry.py
│   │       └── new_companies.py
│   └── sbis/
│       ├── __init__.py
│       ├── card_parser.py
│       ├── client.py
│       ├── client_enrichment.py
│       ├── client_list.py
│       ├── contractor_card.py
│       ├── pagination.py
│       └── records.py
└── .vscode/
    └── launch.json
```

`data/project.db`, `.env`, выгруженные XLSX и прочие локальные данные не должны попадать в Git.

---

## 3. Подготовка окружения

Открыть PowerShell в корне проекта:

```powershell
cd D:\GIT\ProjectSbis
```

Активировать виртуальное окружение:

```powershell
.\.venv\Scripts\Activate.ps1
```

Установить зависимости:

```powershell
pip install -r requirements.txt
```

Для Excel-экспорта необходим:

```text
openpyxl
```

---

## 4. Настройки `.env`

Секретные параметры хранятся локально в `.env`.

Например:

```env
SBIS_BROWSER_COOKIE=...
```

`.env` не должен попадать в Git.

Номер выборки СБИС передаётся через CLI и в `.env` не хранится.

---

## 5. Загрузка клиентов

### Только получить состав выборки

```powershell
python -m src.client_loader --selection 41307
```

### Проверочный запуск на 10 карточках

```powershell
python -m src.client_loader --selection 41307 --enrich-limit 10
```

### Полное обогащение выборки

```powershell
python -m src.client_loader --selection 41307 --enrich-all
```

---

## 6. Последовательный сбор 41307 и 42420

```powershell
python -m src.client_loader --selection 41307 --enrich-all

if ($LASTEXITCODE -eq 0) {
    python -m src.client_loader --selection 42420 --enrich-all
}
```

В одну строку:

```powershell
python -m src.client_loader --selection 41307 --enrich-all; if ($LASTEXITCODE -eq 0) { python -m src.client_loader --selection 42420 --enrich-all }
```

---

## 7. Лимит ContractorCard.Read

СБИС ограничивает частоту `ContractorCard.Read`.

Проект использует паузу:

```text
3 секунды
```

между успешными запросами.

Если сервер возвращает:

```text
HTTP 429
```

`get_contractor_card()` ждёт примерно 65 секунд и повторяет запрос того же клиента.

---

## 8. Структура данных

### `clients`

Общая таблица клиентов.

Проект поддерживает два типа идентификаторов клиента:

```text
spp_uuid
contractor_id
```

`contractor_id` используется для карточек, где `SppUuid` отсутствует и `ContractorCard.Read`
нужно вызывать через `ИдО`.

У клиента может быть:

- только `spp_uuid`;
- только `contractor_id`;
- оба идентификатора одновременно.

Флаг:

```text
enriched
```

показывает, была ли подробная карточка успешно получена и сохранена.

### `client_selections`

Связь клиента с выборками СБИС.

Один клиент может принадлежать нескольким выборкам.

### `client_contacts`

Телефоны и e-mail клиентов.

### `mail_campaigns`

Почтовые кампании.

Основные поля:

- `name` — уникальное имя кампании;
- `selection_id` — выборка СБИС, являющаяся источником клиентов;
- `template_name` — имя почтового шаблона;
- `status` — состояние кампании.

Текущая кампания:

```text
id = 1
name = new_companies_daily
selection_id = 5984
template_name = new_companies
```

### `mail_recipients`

Получатели конкретной кампании.

Хранят:

- `campaign_id`;
- `client_id`;
- `email`;
- `status`.

Текущие основные статусы:

```text
pending
sent
failed
delivered
opened
clicked
bounced
unsubscribed
```

### `mail_messages`

История попыток отправки.

Каждая попытка создаёт отдельную запись, поэтому повторная отправка после ошибки
не затирает предыдущую историю.

Хранятся:

- `recipient_id`;
- `provider`;
- `provider_message_id`;
- `status`;
- `sent_at`.

### `mail_events`

История событий почтового провайдера:

- delivery;
- open;
- click;
- bounce;
- unsubscribe;
- и другие события, которые позднее будут приниматься через webhook.

---

## 9. Что извлекается из ContractorCard.Read

Основные данные:

- название организации;
- ИНН;
- КПП;
- ОГРН;
- `SppUuid`.

Для обычных карточек `ContractorCard.Read` вызывается через `ContractorUUID`.
Для выборок, где `SppUuid` отсутствует, используется `contractor_id` и параметр `ИдО`.

Выборка `5984` работает именно по второму варианту.

Руководитель:

- фамилия;
- имя;
- отчество;
- ИНН;
- должность.

Контакты:

- телефоны;
- e-mail.

Если карточка является подразделением и присутствует `head_data`, данные руководителя и персонализированные контакты берутся из головной организации, а реквизиты текущей карточки остаются собственными.

---

## 10. SQL-экспорт в Excel

Основной модуль:

```text
src/sql_export.py
```

SQL-файлы хранятся в:

```text
sql/
```

`src/sql_queries.py` автоматически находит все `.sql` файлы в этом каталоге.

### Посмотреть доступные SQL-запросы

```powershell
python -m src.sql_export --list
```

### Экспортировать запрос по имени

```powershell
python -m src.sql_export --query all_full_clients
```

### Экспортировать запрос с параметром

```powershell
python -m src.sql_export --query selection_full_clients --param selection_id=41307
```

### Выполнить SQL напрямую из файла

```powershell
python -m src.sql_export --file sql\all_full_clients.sql
```

### Указать собственный путь XLSX

```powershell
python -m src.sql_export --query all_full_clients --output exports\clients.xlsx
```

По умолчанию XLSX создаётся в `exports/` с датой и временем в имени.

---

## 11. Возможности Excel-экспорта

Экспортёр:

- использует SQL aliases как заголовки Excel;
- создаёт оформленную Excel Table;
- включает фильтры через саму Excel Table;
- закрепляет первую строку;
- автоматически подбирает ширину колонок;
- создаёт лист `Информация`;
- записывает в него имя запроса, дату, количество строк, параметры и SQL;
- открывает SQLite в режиме `mode=ro`;
- запрещает изменяющие SQL-команды.

`sql_export` предназначен только для чтения данных.

---

## 12. Основные избранные SQL

### `all_full_clients`

Все уникальные клиенты всех выборок со всеми основными данными:

- название;
- ИНН;
- КПП;
- ФИО директора;
- ИНН директора;
- должность;
- почты;
- телефоны.

Запуск:

```powershell
python -m src.sql_export --query all_full_clients
```

### `selection_full_clients`

Полный список клиентов конкретной выборки.

Запуск:

```powershell
python -m src.sql_export --query selection_full_clients --param selection_id=41307
```

### `selections_summary`

Сводка по выборкам:

- количество клиентов;
- обогащённых;
- необогащённых.

Запуск:

```powershell
python -m src.sql_export --query selections_summary
```

---

## 13. Запуск через VS Code

Конфигурация:

```text
.vscode/launch.json
```

Режимы загрузки клиентов:

```text
SBIS: загрузить выборку
SBIS: выборка + 10 карточек
SBIS: выборка + все карточки
```

Режимы SQL:

```text
SQL: список избранных запросов
SQL: экспорт по имени
```

Для запуска:

```text
Ctrl+Shift+D
```

или:

```text
F5
```

`SQL: экспорт по имени` запрашивает имя SQL без `.sql`.

Пример:

```text
all_full_clients
```

---

## 14. Почтовая рассылка

### Текущая бизнес-кампания

Источник новых организаций:

```text
selection_id = 5984
```

Текущая кампания:

```text
campaign_id = 1
name = new_companies_daily
template_name = new_companies
```

После полного обогащения выборки `5984` в кампанию было добавлено 52 e-mail получателя.

### Наполнить получателей кампании

```powershell
python -c "from src.database import populate_mail_recipients; print(populate_mail_recipients(campaign_id=1))"
```

### Посмотреть статистику кампании

```powershell
python -c "from src.database import get_mail_campaign_stats; print(get_mail_campaign_stats(1))"
```

### Dry-run

Dry-run:

- получает только `pending`-получателей;
- формирует тему, TEXT и HTML;
- не отправляет письмо;
- не создаёт `mail_messages`;
- не меняет `mail_recipients`.

Запуск:

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --dry-run
```

### Mock-send

Mock-send:

- использует `MockMailProvider`;
- создаёт фиктивный `provider_message_id`;
- сохраняет попытку в `mail_messages`;
- при успехе переводит получателя в `sent`;
- при ошибке переводит получателя в `failed`;
- реальных сетевых запросов к почтовому провайдеру не выполняет.

Запуск:

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --mock-send
```

### Шаблоны

Шаблон кампании хранится отдельно от sender:

```text
src/mailing/templates/new_companies.py
```

Связь имени шаблона с модулем задаётся через:

```text
src/mailing/templates/registry.py
```

`sender.py` получает `template_name` из `mail_campaigns`, после чего выбирает
нужный шаблон через registry.

### Защита от повторной отправки

Очередь выбирает только:

```text
status = pending
```

Кроме этого `confirm_mail_send()` повторно проверяет статус непосредственно
перед фиксацией результата и не разрешает повторно обработать уже отправленного
получателя.

### Retry

Получателей со статусом `failed` можно получать отдельно:

```python
get_failed_mail_recipients(...)
```

Вернуть конкретного получателя в очередь:

```python
retry_failed_mail_recipient(...)
```

История предыдущих попыток остаётся в `mail_messages`.

Максимальное количество попыток задаётся константой:

```python
MAX_MAIL_SEND_ATTEMPTS = 3
```

После достижения лимита повторный retry запрещается.

### Текущее состояние интеграции

Реальный почтовый провайдер пока не подключён.

Сейчас используется только:

```text
MockMailProvider
```

Следующий этап после готовности тестового домена:

1. настроить домен у выбранного почтового провайдера;
2. добавить необходимые DNS-записи;
3. реализовать реальный provider-класс;
4. отправить одно тестовое письмо на собственный адрес;
5. проверить delivery;
6. подключить webhook-события в `mail_events`.

---

## 15. Быстрая проверка синтаксиса

```powershell
python -m py_compile src\client_loader.py
python -m py_compile src\database.py
python -m py_compile src\sql_export.py
python -m py_compile src\sql_queries.py
python -m py_compile src\sbis\contractor_card.py
python -m py_compile src\sbis\card_parser.py
python -m py_compile src\mailing\dry_run.py
python -m py_compile src\mailing\sender.py
python -m py_compile src\mailing\templates\registry.py
python -m py_compile src\mailing\templates\new_companies.py
```

Если вывода нет — синтаксис корректен.

---

## 16. Git

Перед коммитом:

```powershell
git status
```

Добавить изменения:

```powershell
git add .
```

Ещё раз проверить:

```powershell
git status
```

Коммит текущего этапа:

```powershell
git commit -m "Add mailing campaign workflow and retry handling"
```

Push:

```powershell
git push origin main
```

Полная последовательность:

```powershell
git status
git add .
git status
git commit -m "Add mailing campaign workflow and retry handling"
git push origin main
```

---

## 17. Что должно попадать в Git

Нужно хранить:

```text
src/
sql/
.vscode/launch.json
PROJECT_WORKFLOW.md
SQL_CHEATSHEET.md
requirements.txt
config/
.env.example
.gitignore
```

Не нужно хранить:

```text
.env
.venv/
data/project.db
data/raw/*
exports/*.xlsx
__pycache__/
*.pyc
```

---

## 18. Текущие важные особенности

- `enriched` хранится глобально на клиенте, а не отдельно по каждой выборке.
- Уже обогащённый клиент повторно через `ContractorCard.Read` не запрашивается.
- `client_selections` используется для фильтрации очереди по выборке.
- `ContractorCard.Read` выполняется последовательно.
- Клиенты поддерживают два идентификатора: `spp_uuid` и `contractor_id`.
- Для выборки `5984` подробная карточка запрашивается через `ИдО`.
- пагинация `CRMClients.ListClientsOnline` использует непрозрачный `nextPosition`;
- `nextPosition` нельзя самостоятельно разбирать или изменять;
- `Position` передаётся как вложенная SBIS-запись с полем `Cursor`;
- SQL-экспорт работает только в read-only режиме;
- новые избранные SQL добавляются созданием нового `.sql` файла в каталоге `sql/`;
- кампания `new_companies_daily` использует выборку `5984`;
- шаблон кампании выбирается через `mail_campaigns.template_name`;
- `--dry-run` ничего не отправляет и не изменяет БД;
- `--mock-send` сохраняет фиктивную попытку в БД;
- каждая попытка отправки хранится отдельно в `mail_messages`;
- повторная обработка уже отправленного recipient запрещена;
- failed-получателя можно вручную вернуть в `pending`;
- количество попыток отправки ограничено через `MAX_MAIL_SEND_ATTEMPTS`;
- реальный почтовый провайдер и webhook-события ещё не подключены;
- следующий этап зависит от готовности тестового домена.

