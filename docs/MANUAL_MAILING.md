# Ручная почтовая очередь

Ручной сценарий не загружает выборку СБИС и не использует таймер. Источник —
CSV в UTF-8 с разделителем `;`:

```csv
email;contact_name
ivan@example.ru;Иван Петрович
info@example.ru;
```

`email` обязателен. `contact_name` используется целиком в приветствии; пустое
значение даёт нейтральное приветствие. Порядок CSV сохраняется, регистр email
нормализуется, повторные адреса в одном файле пропускаются. Любая некорректная
строка останавливает импорт до изменения базы.

## Очистка прежних рассылок

Команда сначала создаёт эксклюзивный SQLite-backup, затем удаляет только
кампании, очереди, сообщения, события и состояние почтового контроллера.
Клиенты, контакты и выборки СБИС сохраняются. Существующий backup не
перезаписывается.

```console
python -m src.mailing.manual reset \
  --backup /var/backups/projectsbis/project-before-manual-YYYYMMDD-HHMMSS.db \
  --confirm-reset-mailing-data
```

После reset отправка остаётся на ручной паузе. Возобновлять её следует только
после запуска здорового Postfix feedback worker.

## Подготовка и preview

```console
python -m src.mailing.manual prepare \
  --csv /tmp/recipients.csv \
  --campaign-name manual-YYYYMMDD \
  --template etrn

python -m src.mailing.manual preview --campaign-id ID --limit 10
```

Доступные шаблоны: `etrn`, `new_companies`. Для `etrn` preview и отправка
проверяют наличие двух обязательных PDF.

## Три контрольных письма

Команда требует включённый `ETRN_TEST_RECIPIENTS_ENABLED` и ровно три адреса в
`ETRN_TEST_RECIPIENTS`. Она отправляет по одному полноценному письму на каждый
адрес, записывает попытки как `is_test=1` и не меняет статус ручной очереди.

```console
python -m src.mailing.manual test \
  --template etrn \
  --contact-name "Иван Петрович" \
  --confirm-test-send
```

## Ручная реальная отправка

Перед ней должны быть активны Postfix feedback worker и heartbeat, а ручная
пауза контроллера должна быть снята осознанно.

```console
python -m src.mailing.control status
python -m src.mailing.control resume
python -m src.mailing.manual send \
  --campaign-id ID \
  --limit 10 \
  --confirm-real-send
```

Команда обрабатывает только `pending` выбранной кампании. Таймер для ручной
очереди не предусмотрен.
