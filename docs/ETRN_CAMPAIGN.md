# Кампания ЭТрН

Отдельная массовая кампания семейства `etrn` запускается независимо от daily
workflow и выборки 5984. Техническое значение `selection_id=0` означает, что
источник аудитории — файл ИНН, а не выборка СБИС.

## Команды

Подготовить аудиторию:

```powershell
python -m src.mailing.etrn prepare --inn-file .\lists\etrn.txt
```

Проверить очередь и сформировать письма без SMTP и без изменения статусов:

```powershell
python -m src.mailing.etrn send --dry-run
```

Показать статистику:

```powershell
python -m src.mailing.etrn stats
```

Реальная отправка существует только за явным флагом и не должна запускаться во
время разработки:

```powershell
python -m src.mailing.etrn send --confirm-real-send
```

## Prepare

`prepare` читает уникальные ИНН из UTF-8/UTF-8-SIG файла и выполняет отдельные
этапы:

```text
INN list
→ clients по точному ИНН
→ client_contacts
→ ContractorCard.Read только при отсутствии email
→ strip + lowercase + базовая валидация
→ проверка permanent bounce
→ dedup family=etrn + normalized_email
→ mail_recipients(status=pending)
```

Поиск контактов не совмещён с SMTP. Обогащение использует существующие
`get_contractor_card()`, `parse_contractor_card()` и `save_enriched_client()`.
Если у клиента два разных валидных адреса, создаются два получателя. Если один
адрес встречается у разных ИНН, уникальный индекс семейства `etrn` пропускает
только первый.

Причины пропуска сохраняются в общей таблице `mail_audience_events`:
`client_not_found`, `no_email`, `enrichment_failed`, `invalid_email`,
`duplicate_etrn`, `bounced`. Таблица является общим журналом подготовки, а не
параллельным реестром отправленных ЭТрН-писем. Факт отправки остаётся в
`mail_messages`/`mail_events`.
Итоговый snapshot подготовки сохраняется событием `prepare_summary` в этом же
журнале. `prepare` не создаёт `mail_run` и поэтому не выглядит в админке как
завершённая почтовая рассылка.

Пример итогов:

```text
ИНН во входном файле:            4200
Найдено клиентов в БД:           4031
Клиентов с готовыми email:       3892
Потребовался поиск:              310
Email найден после поиска:       174
Без email:                       139
Уникальных email:                3760
Уже получали/дубли ЭТрН:         82
Invalid/bounced:                 50
К отправке:                      3760
```

## Возобновляемый worker

Очередь хранится в `mail_recipients`. Успешные адреса получают `sent`,
временные SMTP-ошибки — `deferred` с `next_attempt_at`, окончательные ошибки —
`failed`. `attempt_count` и `last_error` позволяют продолжить после остановки.
Каждая SMTP-попытка создаёт обычный `mail_messages` и `mail_events`, поэтому
Postfix delivery tracking, open/click tracking и админка работают без отдельного
механизма.

На одного получателя допускается до трёх попыток. По умолчанию повторные
попытки становятся доступны через 60 и 300 секунд. Ошибки timeout, connection,
SMTP 4xx и temporary/deferred считаются временными. SMTP 5xx и остальные
окончательные ошибки не ретраятся.

Worker выбирает из одной очереди кампании только `pending` и созревшие
`deferred` записи запросом с `ORDER BY mail_recipients.id LIMIT 500`. Для каждой
выборки атомарно создаётся новый `mail_runs`; выбранное количество сохраняется в
`recipients_added`, а последовательный номер — в `mail_runs.batch_number`.
Статусы `sent` и `failed` в следующую выборку не попадают.

После каждого завершённого run при наличии остатка очереди выбирается случайный
cooldown 2400–3000 секунд. Абсолютное UTC-время продолжения хранится в
`mail_campaigns.next_send_at`. Worker ждёт до этого времени и создаёт следующий
run. После рестарта незавершённый run закрывается как `failed`, уже отправленные
адреса остаются `sent`, а worker выдерживает сохранённый остаток cooldown и
продолжает оставшуюся очередь. Старое поле `batch_sent_count` больше не участвует
в ограничении партий.

Логи имеют вид:

```text
ETRN_BATCH_START
batch=1
run_id=42
size=500
[1/500] info@example.ru -> sent
ETRN_BATCH_COMPLETE
batch=1
run_id=42
recipients=500
sent=482
failed=18
deferred=0
ETRN_COOLDOWN_SET
duration=2714s
until=2026-08-31T12:34:56+00:00
```

## Jitter и конфигурация

Переменные окружения и значения по умолчанию:

| Переменная | Значение |
|---|---:|
| `ETRN_BATCH_SIZE` | `500` |
| `ETRN_MESSAGE_DELAY_MIN_SECONDS` | `6` |
| `ETRN_MESSAGE_DELAY_MAX_SECONDS` | `8` |
| `ETRN_COOLDOWN_MIN_SECONDS` | `2400` |
| `ETRN_COOLDOWN_MAX_SECONDS` | `3000` |
| `ETRN_RETRY_FIRST_SECONDS` | `60` |
| `ETRN_RETRY_SECOND_SECONDS` | `300` |

Даже если `ETRN_BATCH_SIZE` больше 500, worker применяет предел 500.

## Шаблон и персонализация

Шаблон находится в `src/mailing/templates/etrn.py` и зарегистрирован в общем
registry. Он формирует HTML на table layout с inline CSS, preheader, plain-text
fallback и CID-логотипом. При корректных имени и отчестве используется
`Добрый день, Имя Отчество!`; иначе — `Добрый день!`.

До рендера письма создаётся `mail_messages.tracking_token`. Open pixel и CTA
используют существующие `/t/o/...` и `/t/c/.../cta_email`; CTA перенаправляется
на `CONTACT_WHATSAPP_URL`. Телефон и email берутся из общей конфигурации.

## Обязательные PDF

Оба файла должны находиться в `assets/mailing/etrn/`:

1. `Чек-лист подключения бизнеса к электронным транспортным накладным (ЭТрН).pdf`
2. `КП Saby TMS Электронные перевозочные документы ЭТрН (основное).pdf`

Проверка выполняется до SMTP и создания `mail_messages`. При отсутствии любого
файла worker завершается fail-closed. PDF не кодируются в Python и добавляются
через стандартный `EmailMessage.add_attachment` как `application/pdf`; библиотека
формирует RFC-совместимый UTF-8 filename.

## Статистика и существующая инфраструктура

`stats` показывает `prepared`, `pending`, `sent`, `failed`, `delivered`,
`bounced`, `opens`, `clicks` и счётчики причин пропуска. Реальные запуски
создаются в `mail_runs`, поэтому отображаются в существующей админке. Доставка
продолжает синхронизироваться существующим Postfix parser. Новые systemd units и
production timers не создаются.
