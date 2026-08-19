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
- отправлять реальные письма через собственный SMTP-сервер на VPS;
- выполнять безопасный SMTP-тест только на явно заданные тестовые адреса;
- использовать фирменный HTML-шаблон «Атлантис» с CID-логотипом;
- персонализировать приветствие по имени и отчеству руководителя;
- создавать `mail_messages` до фактической отправки;
- генерировать отдельный `tracking_token` для каждой учётной попытки отправки;
- завершать попытку отдельной операцией `complete_mail_message()`;
- сохранять событие `sent` / `failed` в `mail_events`;
- хранить события писем в `mail_events`;
- повторно ставить failed-получателей в очередь вручную;
- ограничивать количество повторных попыток отправки;
- публиковать BIMI-логотип домена `projectsbis.ru`;
- готовить HTTP-tracking для `opened`, `clicked`, `unsubscribed`.

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
│   │   ├── smtp_provider.py
│   │   ├── assets/
│   │   │   └── atlantis_email_logo.png
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

### Загрузка одной организации по ИНН

```powershell
python -m src.client_loader --inn 251118147906 --enrich-all
```
### Загрузка организаций из файла со списком ИНН
```powershell
python -m src.client_loader --inn-file .\lists\list.txt --enrich-all
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

Каждая учётная попытка создаётся **до обращения к SMTP**, поэтому ещё до
формирования финального HTML уже существуют `mail_messages.id` и
`tracking_token`.

Повторная отправка после ошибки создаёт новую запись и не затирает
предыдущую историю.

Хранятся:

- `recipient_id`;
- `provider`;
- `provider_message_id`;
- `status`;
- `sent_at`;
- `tracking_token`.

Для `tracking_token` используется уникальный индекс:

```text
idx_mail_messages_tracking_token
```

Текущий жизненный цикл:

```text
create_mail_message()
    ↓
mail_messages.status = pending
tracking_token создан
    ↓
build_mail_message(..., tracking_token=...)
    ↓
SMTP / Mock provider
    ↓
complete_mail_message()
    ↓
mail_messages.status = sent / failed
mail_events += sent / failed
```

### `mail_events`

История событий конкретного `mail_messages`.

Уже используется:

- `sent`;
- `failed`.

Запланированы:

- `delivered`;
- `opened`;
- `clicked`;
- `bounced`;
- `unsubscribed`.

`opened`, `clicked` и `unsubscribed` будут приходить с публичного tracking-сервиса.
`delivered` / `bounced` планируется определять по Postfix / SMTP-логам или DSN.

Важно: `SMTP success=True` означает только, что локальный Postfix принял письмо
от Python-клиента. Это ещё не гарантирует доставку на удалённый MX.

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

### Экспорт клиентов из конкретного списка ИНН


```powershell
python -m src.sql_export --query source_full_clients --param source_type=inn_file --param source_value=test_inns.txt
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

### SMTP-инфраструктура

Исходящая почта работает через собственный VPS.

```text
Домен:        projectsbis.ru
SMTP host:    mail.projectsbis.ru
IPv4:         2.26.51.175
Submission:   587 / STARTTLS
```

На VPS настроены:

- Postfix;
- Dovecot SASL;
- OpenDKIM;
- Let's Encrypt TLS;
- SPF;
- DKIM;
- DMARC;
- Nginx для BIMI и будущего tracking API.

Проверено на реальных тестовых письмах:

```text
SPF  = PASS
DKIM = PASS
```

DMARC сейчас:

```text
v=DMARC1; p=quarantine; pct=100
```

### Серверный режим работы

Целевая схема проекта — **полностью автономная работа на VPS**.

Рабочий Windows-ПК используется для разработки, ручной диагностики и обновления
кода через Git, но ежедневная бизнес-логика должна выполняться на сервере без
участия пользователя.

Схема:

```text
systemd timer на VPS
        ↓
ProjectSbis
        ↓
получить новые организации из выборки 5984
        ↓
ContractorCard.Read / обогащение
        ↓
извлечь e-mail и данные руководителя
        ↓
обновить server-side project.db
        ↓
сформировать mail_recipients
        ↓
создать mail_messages + tracking_token
        ↓
отправить письма
        ↓
Postfix + DKIM
        ↓
MX получателя
```

Таким образом, основной `sender.py`, `client_loader.py`, SQLite-БД и будущий
tracking-компонент работают на одном VPS.

Рабочий ПК не является обязательной частью production-процесса.

На VPS размещаются:

- Git checkout проекта;
- `.env` с production-секретами;
- постоянная `data/project.db`;
- код загрузки и обогащения клиентов;
- `sender.py`;
- SMTP-инфраструктура Postfix / Dovecot / OpenDKIM;
- Nginx;
- BIMI-файл;
- tracking endpoint;
- systemd service + systemd timer для ежедневного запуска.

`data/project.db` **не хранится в Git**, но production-экземпляр БД должен
постоянно находиться на VPS и переживать `git pull`, перезапуски и обновления кода.

Для отправки с VPS можно сохранить текущий механизм SMTP:

```text
ProjectSbis на VPS
        ↓
mail.projectsbis.ru:587
STARTTLS + AUTH
        ↓
Postfix
```

То есть существующую реализацию `SMTPMailProvider` менять для первого server
deployment не требуется. Позже при желании можно упростить локальную передачу
письма Postfix, но это не является необходимым.

### `.env` для SMTP

Пример без секретов:

```env
MAIL_FROM_EMAIL=info@projectsbis.ru
MAIL_FROM_NAME=Атлантис
MAIL_SMTP_HOST=mail.projectsbis.ru
MAIL_SMTP_PORT=587
MAIL_SMTP_USERNAME=projectsbis
MAIL_SMTP_PASSWORD=...
```

`.env` не должен попадать в Git.

### Dry-run

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --dry-run
```

Dry-run:

- получает только `pending`;
- формирует TEXT + HTML;
- не отправляет;
- не создаёт `mail_messages`;
- не изменяет `mail_recipients`.

### Mock-send

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --mock-send
```

Mock-send:

- создаёт `mail_messages` **до** отправки;
- генерирует `tracking_token`;
- формирует письмо уже с этим token;
- использует `MockMailProvider`;
- после результата вызывает `complete_mail_message()`;
- создаёт событие `sent` или `failed`.

### SMTP test

Безопасная тестовая отправка:

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --smtp-send --test-email "user@example.com"
```

Можно передать несколько адресов через запятую.

В этом режиме:

- реальный адрес клиента не используется;
- письма уходят только на тестовые адреса;
- `mail_messages` не создаются;
- `mail_recipients` не изменяются;
- тема получает префикс `[TEST]`.

### Реальная SMTP-отправка

Реальная отправка клиентам требует явного подтверждения CLI.

```powershell
python -m src.mailing.sender --campaign-id 1 --limit 1 --smtp-send --confirm-real-send
```

Без `--confirm-real-send` реальная SMTP-отправка клиентам должна быть заблокирована.

### Шаблон «Атлантис»

Основной шаблон:

```text
src/mailing/templates/new_companies.py
```

Особенности:

- фирменный HTML-дизайн «Атлантис»;
- CID-логотип из `src/mailing/assets/atlantis_email_logo.png`;
- обращение по имени и отчеству директора;
- fallback:
  - имя + отчество → `Михаил Петрович, добрый день!`;
  - только имя → `Михаил, добрый день!`;
  - без имени → `Добрый день!`;
- организация остаётся в нижнем клиентском контексте;
- CTA: `Подобрать решение`;
- CTA пока использует `mailto:info@projectsbis.ru`.

Приём входящей почты на `info@projectsbis.ru` ещё не настроен как полноценный
пользовательский почтовый ящик. Исходящая отправка работает.

### Tracking token

В `mail_messages` добавлено:

```text
tracking_token TEXT
```

И уникальный индекс:

```text
idx_mail_messages_tracking_token
```

Для существующей локальной БД миграция уже выполнена вручную через `ALTER TABLE`.

**Важно:** схема создания новых БД в `database.py` также должна содержать
`tracking_token` и индекс. Миграция существующей БД и схема новых БД — разные вещи.

Основные операции:

```python
create_mail_message(...)
complete_mail_message(...)
```

`confirm_mail_send()` считается старой схемой и не должен использоваться новым
потоком отправки.

### Tracking pixel

Шаблон уже умеет формировать URL:

```text
https://mail.projectsbis.ru/t/o/<tracking_token>.gif
```

Пиксель вставляется только если `tracking_token` передан в шаблон.

Следующий серверный этап:

```text
GET /t/o/<token>.gif
```

должен:

1. принять token;
2. записать событие открытия на VPS;
3. вернуть прозрачный GIF 1×1;
4. не раскрывать email / ИНН / client_id в URL.

Поскольку production-`project.db` будет находиться на том же VPS, tracking
endpoint сможет напрямую находить `mail_messages` по `tracking_token` и добавлять
события в `mail_events`.

Отдельная промежуточная `tracking.db` в целевой server-only архитектуре не нужна.

Для безопасной одновременной работы фонового задания и tracking endpoint следует
использовать короткие SQLite-транзакции; при развёртывании на сервере отдельно
проверим режим WAL и таймаут ожидания блокировки.

### BIMI

Опубликован BIMI-логотип:

```text
https://mail.projectsbis.ru/bimi/atlantis.svg
```

DNS:

```text
default._bimi.projectsbis.ru
v=BIMI1; l=https://mail.projectsbis.ru/bimi/atlantis.svg;
```

SVG:

- SVG Tiny P/S;
- квадратный `viewBox`;
- прошёл проверку `jing`;
- внешний BIMI Inspector видит запись и логотип.

Отображение BIMI-аватарки зависит от конкретного почтовика, его кеша, репутации
домена и возможных требований к VMC/CMC. Отсутствие аватарки сразу после настройки
не означает ошибку DNS.

### Метрики

Целевая модель:

```text
sent
delivered
opened
clicked
bounced
unsubscribed
```

Источник:

```text
sent / failed       → sender.py
delivered / bounced → Postfix / SMTP logs / DSN
opened              → tracking pixel
clicked             → tracking redirect
unsubscribed        → tracking unsubscribe endpoint
```

`opened` нельзя считать гарантированным прочтением: почтовики могут загружать
изображения через proxy / prefetch.

Для бизнес-аналитики `clicked` обычно надёжнее, чем `opened`.

---

## 15. Целевой production-процесс на VPS

Ежедневный запуск должен выполняться автоматически через `systemd timer`.

Целевая последовательность одной итерации:

```text
1. Обновить состав выборки 5984.
2. Найти новых / ещё не обогащённых клиентов.
3. Выполнить ContractorCard.Read.
4. Сохранить реквизиты, руководителя и контакты.
5. Добавить новые подходящие e-mail в кампанию.
6. Получить pending-получателей.
7. Создать mail_messages до SMTP.
8. Сформировать персонализированные письма с tracking_token.
9. Отправить через SMTP.
10. Записать sent / failed в mail_events.
11. Tracking endpoint независимо принимает opened / clicked / unsubscribed.
12. Отдельная задача разбирает delivered / bounced по Postfix / DSN.
```

На первом этапе deployment не нужно пытаться объединить всё в один огромный
скрипт. Сначала разворачиваем текущий проект на VPS и убеждаемся, что существующие
CLI-команды там работают вручную. После этого добавляем одну orchestration-команду
для полного дневного цикла и привязываем её к `systemd timer`.

Рекомендуемая production-структура:

```text
/opt/projectsbis/
    repository/

/var/lib/projectsbis/
    project.db

/etc/projectsbis/
    projectsbis.env

/etc/systemd/system/
    projectsbis-daily.service
    projectsbis-daily.timer
    projectsbis-tracking.service
```

Production-БД и `.env` не должны находиться внутри Git checkout, если это может
привести к случайному удалению или замене при deployment. Код должен получать их
пути через конфигурацию / переменные окружения.

---

## 16. Быстрая проверка синтаксиса

```powershell
python -m py_compile src\client_loader.py
python -m py_compile src\database.py
python -m py_compile src\sql_export.py
python -m py_compile src\sql_queries.py
python -m py_compile src\sbis\contractor_card.py
python -m py_compile src\sbis\card_parser.py
python -m py_compile src\mailing\dry_run.py
python -m py_compile src\mailing\sender.py
python -m py_compile src\mailing\smtp_provider.py
python -m py_compile src\mailing\templates\registry.py
python -m py_compile src\mailing\templates\new_companies.py
```

Если вывода нет — синтаксис корректен.

---

## 17. Git

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
git commit -m "Add SMTP mailing and tracking foundation"
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
git commit -m "Add SMTP mailing and tracking foundation"
git push origin main
```

---

## 18. Что должно попадать в Git

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
*.db
*.sqlite
*.sqlite3
```

---

## 19. Текущие важные особенности

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
- `--mock-send` создаёт полноценную учётную попытку с `tracking_token`;
- SMTP test отправляет реальные письма только на тестовые адреса и не меняет БД;
- production sender запускается непосредственно на VPS; текущий SMTPProvider можно оставить на 587/STARTTLS;
- Postfix, DKIM, TLS, Nginx и BIMI работают на `mail.projectsbis.ru`;
- каждая учётная попытка создаётся до SMTP через `create_mail_message()`;
- результат фиксируется через `complete_mail_message()`;
- `mail_events` уже получает `sent` / `failed`;
- `tracking_token` не содержит email, ИНН или client_id;
- HTML-шаблон уже умеет строить URL tracking pixel;
- публичный tracking endpoint ещё не реализован;
- production `project.db` хранится на VPS как постоянные данные и не попадает в Git;
- tracking endpoint работает с той же production `project.db` и записывает события в `mail_events`;
- полноценный входящий ящик `info@projectsbis.ru` ещё не настроен;
- BIMI технически опубликован, но показ аватарки определяется почтовым сервисом;
- failed-получателя можно вручную вернуть в `pending`;
- количество попыток отправки ограничено через `MAX_MAIL_SEND_ATTEMPTS`;
- следующий этап: запушить текущий код в GitHub и развернуть весь ProjectSbis на VPS с production-БД, tracking endpoint и ежедневным systemd timer.
