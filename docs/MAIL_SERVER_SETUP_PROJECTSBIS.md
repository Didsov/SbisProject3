# Настройка собственного почтового сервера для ProjectSbis

## 1. Что мы строим

Цель этой инструкции — настроить собственный SMTP-сервер на Ubuntu так, чтобы приложение ProjectSbis могло отправлять письма с адреса:

```text
info@projectsbis.ru
```

через собственный VPS:

```text
mail.projectsbis.ru
```

Схема работы:

```text
ProjectSbis на Windows
        │
        │ SMTP Submission
        │ порт 587
        │ STARTTLS
        │ SMTP AUTH
        ▼
mail.projectsbis.ru
VPS 2.26.51.175
        │
        ├── Postfix       — принимает письмо от нашего приложения и отправляет его дальше
        ├── Dovecot       — проверяет SMTP-логин и пароль
        ├── OpenDKIM      — подписывает письмо DKIM-подписью
        └── Let's Encrypt — даёт нормальный TLS-сертификат
        │
        ▼
SMTP-сервер получателя
Яндекс / Gmail / Mail.ru / другие
```

Эта схема позволяет не зависеть от сторонних сервисов рассылок и отправлять почту напрямую со своего VPS.

Важно: собственный SMTP-сервер не гарантирует попадание во «Входящие». Доставляемость зависит также от репутации IP, содержания письма, частоты отправки, жалоб, bounce-rate и политики почтовых провайдеров.

---

## 2. Что нужно до начала настройки

Нам нужны:

- домен `projectsbis.ru`;
- доступ к DNS-зоне домена;
- VPS с Ubuntu 24.04;
- публичный IPv4;
- возможность изменить PTR у VPS;
- открытый исходящий TCP-порт 25;
- возможность принимать соединения на 587 и временно на 80;
- root/sudo-доступ к серверу.

В нашем случае:

```text
Домен: projectsbis.ru
Почтовый hostname: mail.projectsbis.ru
VPS IP: 2.26.51.175
ОС: Ubuntu 24.04
Отправитель: info@projectsbis.ru
SMTP user: projectsbis
```

---

## 3. Почему нужен отдельный hostname `mail.projectsbis.ru`

Почтовый сервер должен иметь нормальное DNS-имя.

Мы используем:

```text
mail.projectsbis.ru
```

Это имя связывается с IP VPS:

```text
mail.projectsbis.ru → 2.26.51.175
```

Это важно по нескольким причинам:

1. Postfix представляется другим SMTP-серверам этим именем.
2. TLS-сертификат выпускается именно на `mail.projectsbis.ru`.
3. PTR-запись должна указывать обратно на это же имя.
4. Почтовые провайдеры проверяют согласованность DNS и reverse DNS.

---

## 4. Настройка A-записи

В DNS домена создаём:

```text
Тип: A
Имя: mail
Значение: 2.26.51.175
```

После этого:

```text
mail.projectsbis.ru → 2.26.51.175
```

Проверка из PowerShell:

```powershell
Resolve-DnsName mail.projectsbis.ru -Type A
```

Ожидаемый IP:

```text
2.26.51.175
```

Если сайт `projectsbis.ru` уже находится на другом сервере, записи `@` и `www` менять не нужно. Почтовый сервер может находиться на отдельном VPS.

---

## 5. PTR / Reverse DNS

Обычная A-запись отвечает на вопрос:

```text
Какой IP у mail.projectsbis.ru?
```

PTR отвечает наоборот:

```text
Какое имя у IP 2.26.51.175?
```

У провайдера VPS устанавливаем:

```text
2.26.51.175 → mail.projectsbis.ru
```

В результате должна получиться согласованная пара:

```text
mail.projectsbis.ru → 2.26.51.175
2.26.51.175 → mail.projectsbis.ru
```

Это желательно для собственного SMTP: принимающие серверы часто проверяют reverse DNS.

Проверка:

```powershell
Resolve-DnsName 175.51.26.2.in-addr.arpa -Type PTR
```

Ожидаем:

```text
mail.projectsbis.ru
```

---

## 6. Настройка hostname Ubuntu

На сервере:

```bash
sudo hostnamectl set-hostname mail.projectsbis.ru
```

Проверяем:

```bash
hostname
hostname -f
```

Обычно получаем:

```text
mail
mail.projectsbis.ru
```

После этого приглашение терминала может стать:

```text
root@mail:~#
```

Это нормально: `root` — пользователь, `mail` — короткое имя сервера.

---

## 7. Установка Postfix

Postfix — основной SMTP-сервер.

Он будет:

- принимать письмо от нашего Python-приложения;
- добавлять его в очередь;
- передавать письмо SMTP-серверу получателя;
- вести лог доставки;
- работать вместе с OpenDKIM.

Установка:

```bash
sudo apt update
sudo apt install postfix
```

При установке выбираем:

```text
Internet Site
```

В поле `System mail name`:

```text
projectsbis.ru
```

Проверяем:

```bash
cat /etc/mailname
```

Ожидаем:

```text
projectsbis.ru
```

Проверяем основные параметры:

```bash
postconf myhostname myorigin
```

Пример:

```text
myhostname = mail.projectsbis.ru
myorigin = /etc/mailname
```

Проверяем порт 25:

```bash
sudo ss -ltnp | grep ':25'
```

Postfix должен слушать TCP/25.

---

## 8. Зачем нужен порт 25

Порт 25 — это server-to-server SMTP.

Например:

```text
mail.projectsbis.ru
        │
        │ TCP 25
        ▼
mx.yandex.ru
```

Наше приложение не должно использовать порт 25 для авторизованной отправки с рабочего ПК.

Для приложения позже используется:

```text
587 + STARTTLS + SMTP AUTH
```

Порт 25 оставляем для общения Postfix с внешними MX-серверами.

---

## 9. Проверка защиты от Open Relay

Очень важно не превратить сервер в open relay.

Open relay — это SMTP-сервер, через который любой человек из интернета может отправлять письма кому угодно.

Такой сервер очень быстро:

- попадёт в blacklist;
- начнёт использоваться спамерами;
- потеряет репутацию IP.

Проверяем:

```bash
postconf mynetworks mydestination smtpd_relay_restrictions inet_interfaces
```

Рабочая конфигурация:

```text
mynetworks = 127.0.0.0/8 [::ffff:127.0.0.0]/104 [::1]/128

mydestination = $myhostname, projectsbis.ru, mail.projectsbis.ru, localhost.projectsbis.ru, localhost

smtpd_relay_restrictions = permit_mynetworks permit_sasl_authenticated defer_unauth_destination

inet_interfaces = all
```

Ключевой смысл:

```text
permit_sasl_authenticated
```

— relay разрешён авторизованным SMTP-клиентам.

Нельзя ставить что-то вроде:

```text
mynetworks = 0.0.0.0/0
```

---

## 10. SPF

### Что такое SPF

SPF — DNS-запись, в которой владелец домена указывает, какие серверы имеют право отправлять почту от имени домена.

В нашем случае мы говорим:

```text
IP 2.26.51.175 имеет право отправлять почту от projectsbis.ru
```

Добавляем TXT-запись:

```text
Имя: @
Тип: TXT
Значение: v=spf1 ip4:2.26.51.175 -all
```

Расшифровка:

```text
v=spf1
```

— версия SPF.

```text
ip4:2.26.51.175
```

— этот IPv4 разрешён.

```text
-all
```

— остальные серверы не разрешены.

Проверка:

```powershell
Resolve-DnsName projectsbis.ru -Type TXT -Server ns1.reg.ru
```

Ожидаем:

```text
v=spf1 ip4:2.26.51.175 -all
```

Важно: у домена должна быть только одна SPF-запись. Если SPF уже существует, его нужно объединять, а не создавать второй TXT с `v=spf1`.

---

## 11. DKIM: зачем он нужен

SPF подтверждает сервер-отправитель.

DKIM подтверждает само письмо.

OpenDKIM создаёт криптографическую подпись письма приватным ключом.

Получатель:

1. получает письмо;
2. видит DKIM selector и домен;
3. получает публичный ключ из DNS;
4. проверяет подпись.

Если письмо не изменено и ключ правильный:

```text
dkim=pass
```

Приватный DKIM-ключ остаётся только на сервере.

Публичный ключ публикуется в DNS.

---

## 12. Установка OpenDKIM

```bash
sudo apt install opendkim opendkim-tools
```

Проверка:

```bash
opendkim -V
```

Нужна поддержка:

```text
rsa-sha256
```

---

## 13. Генерация DKIM-ключей

Создаём каталог:

```bash
sudo mkdir -p /etc/opendkim/keys/projectsbis.ru
cd /etc/opendkim/keys/projectsbis.ru
```

Генерируем RSA-ключ 2048 бит:

```bash
sudo opendkim-genkey   -b 2048   -d projectsbis.ru   -D .   -s default   -v
```

Будут созданы:

```text
default.private
default.txt
```

Назначение:

```text
default.private
```

— секретный приватный ключ.

```text
default.txt
```

— готовая подсказка для DNS TXT-записи.

Никогда не публикуем и не отправляем:

```text
default.private
```

---

## 14. Публикация DKIM в DNS

Смотрим:

```bash
cat default.txt
```

Там будет примерно:

```text
default._domainkey IN TXT (
    "v=DKIM1; h=sha256; k=rsa; "
    "p=MIIBI..."
)
```

В DNS создаём:

```text
Тип: TXT
Имя: default._domainkey
```

Значение нужно склеить в одну строку:

```text
v=DKIM1; h=sha256; k=rsa; p=ПУБЛИЧНЫЙ_КЛЮЧ
```

Не вставляем:

```text
IN TXT
(
)
"
```

Проверка:

```powershell
Resolve-DnsName default._domainkey.projectsbis.ru -Type TXT -Server ns1.reg.ru
```

DNS может показать длинный ключ несколькими кусками `Strings`. Это нормально: логически они объединяются.

---

## 15. Конфигурация OpenDKIM

Открываем:

```bash
sudo nano /etc/opendkim.conf
```

Используем:

```conf
Syslog                  yes
SyslogSuccess           yes
Canonicalization        relaxed/simple
Mode                    sv
SubDomains              no
OversignHeaders         From

Socket                  inet:8891@localhost

KeyTable                /etc/opendkim/KeyTable
SigningTable            refile:/etc/opendkim/SigningTable
InternalHosts           /etc/opendkim/TrustedHosts
```

Что это означает:

```text
Mode sv
```

OpenDKIM может подписывать и проверять.

```text
Socket inet:8891@localhost
```

OpenDKIM слушает локальный порт 8891.

Postfix будет передавать письмо туда перед отправкой.

```text
KeyTable
```

указывает, какой приватный ключ соответствует домену/selector.

```text
SigningTable
```

указывает, какие отправители нужно подписывать.

```text
InternalHosts
```

определяет доверенные источники писем для подписи.

---

## 16. KeyTable

Создаём:

```bash
sudo nano /etc/opendkim/KeyTable
```

Содержимое:

```text
default._domainkey.projectsbis.ru projectsbis.ru:default:/etc/opendkim/keys/projectsbis.ru/default.private
```

Логика:

```text
DKIM DNS name
→ domain
→ selector
→ private key
```

---

## 17. SigningTable

```bash
sudo nano /etc/opendkim/SigningTable
```

Содержимое:

```text
*@projectsbis.ru default._domainkey.projectsbis.ru
```

То есть любое письмо с адресом:

```text
...@projectsbis.ru
```

подписывается ключом:

```text
default._domainkey.projectsbis.ru
```

---

## 18. InternalHosts

```bash
sudo nano /etc/opendkim/TrustedHosts
```

Содержимое:

```text
127.0.0.1
localhost
mail.projectsbis.ru
```

Важно: файл у нас называется `TrustedHosts`, но директива в `/etc/opendkim.conf`:

```text
InternalHosts
```

Попытка использовать директиву:

```text
TrustedHosts
```

приводила к `configuration error`.

---

## 19. Права на приватный DKIM-ключ

OpenDKIM запускается не от root.

Поэтому он должен иметь право читать:

```text
default.private
```

Настраиваем:

```bash
sudo chown opendkim:opendkim /etc/opendkim/keys/projectsbis.ru/default.private
sudo chmod 600 /etc/opendkim/keys/projectsbis.ru/default.private
```

Проверка:

```bash
ls -l /etc/opendkim/keys/projectsbis.ru/default.private
```

Ожидаем:

```text
-rw------- 1 opendkim opendkim ... default.private
```

Без этого OpenDKIM выдаёт:

```text
Permission denied
```

и не сможет подписать письмо.

---

## 20. Проверка DKIM DNS + ключа

```bash
sudo opendkim-testkey -d projectsbis.ru -s default -vvv
```

Хороший результат:

```text
key OK
```

Возможна строка:

```text
key not secure
```

Она относится к DNSSEC, а не к корректности DKIM-ключа.

Если в конце:

```text
key OK
```

публичный и приватный ключ совпадают.

---

## 21. Подключение OpenDKIM к Postfix

Настраиваем milter:

```bash
sudo postconf -e 'milter_default_action = accept'
sudo postconf -e 'milter_protocol = 6'
sudo postconf -e 'smtpd_milters = inet:localhost:8891'
sudo postconf -e 'non_smtpd_milters = inet:localhost:8891'
```

Логика:

```text
Postfix
  │
  ├─ письмо
  ▼
OpenDKIM :8891
  │
  ├─ добавляет DKIM-Signature
  ▼
Postfix
  │
  ▼
получатель
```

Перезапуск:

```bash
sudo systemctl restart opendkim
sudo systemctl restart postfix
```

Проверка OpenDKIM:

```bash
sudo systemctl status opendkim --no-pager
```

Проверка сокета:

```bash
sudo ss -ltnp | grep 8891
```

Ожидаем:

```text
127.0.0.1:8891
```

Проверяем Postfix:

```bash
postconf smtpd_milters non_smtpd_milters milter_default_action milter_protocol
```

---

## 22. DMARC

### Что такое DMARC

DMARC связывает SPF/DKIM с адресом в поле:

```text
From:
```

и задаёт политику обработки писем, которые не проходят проверку.

Для начальной настройки используем безопасный мониторинговый вариант:

```text
v=DMARC1; p=none
```

В DNS:

```text
Тип: TXT
Имя: _dmarc
Значение: v=DMARC1; p=none
```

Проверка:

```powershell
Resolve-DnsName _dmarc.projectsbis.ru -Type TXT -Server ns1.reg.ru
```

На первом этапе `p=none` означает: не просим получателей блокировать письма, а просто объявляем DMARC-политику.

После стабильной работы можно рассмотреть `p=quarantine` или `p=reject`, но только после проверки всех источников почты домена.

---

## 23. Первая тестовая отправка

Проверяем письмо напрямую через локальный Postfix:

```bash
printf 'From: info@projectsbis.ru\nTo: YOUR_MAIL@example.com\nSubject: SMTP test\n\nTest\n' | /usr/sbin/sendmail -f info@projectsbis.ru -t
```

Ключ:

```text
-f info@projectsbis.ru
```

задаёт SMTP envelope sender.

Без него письмо, отправленное от root, может получить:

```text
Return-Path: root@projectsbis.ru
```

С `-f` получаем:

```text
From: info@projectsbis.ru
Return-Path: info@projectsbis.ru
```

---

## 24. Проверка `mail.log`

```bash
sudo tail -n 50 /var/log/mail.log
```

Ищем:

```text
DKIM-Signature field added (s=default, d=projectsbis.ru)
```

Это означает, что OpenDKIM подписал письмо.

Потом:

```text
status=sent
```

Это означает, что внешний SMTP-сервер принял письмо.

Важно: `status=sent` не означает, что пользователь обязательно увидит его во «Входящих». Это означает успешную передачу серверу получателя.

---

## 25. Проверка на стороне получателя

В исходниках письма Яндекс у нас показал:

```text
spf=pass
dkim=pass
```

Также:

```text
From: info@projectsbis.ru
Return-Path: info@projectsbis.ru
```

Это подтверждает, что базовая аутентификация домена работает.

---

## 26. Почему нельзя подключать Python к открытому relay на 25

ProjectSbis запускается на Windows.

Нам нужно, чтобы только наше приложение могло отправлять письма через сервер.

Плохое решение:

```text
разрешить relay для любого IP
```

Правильное:

```text
SMTP Submission
порт 587
STARTTLS
SMTP AUTH
```

Пользователь вводит логин/пароль, сервер проверяет их и только потом разрешает отправку.

Для проверки учётных данных используем Dovecot.

---

## 27. Установка Dovecot

```bash
sudo apt install dovecot-core
```

Проверка:

```bash
dovecot --version
```

Проверяем, что Postfix умеет использовать Dovecot SASL:

```bash
postconf -a
```

Ожидаем:

```text
dovecot
```

---

## 28. Auth socket Dovecot → Postfix

Postfix должен передавать логин и пароль Dovecot.

Для этого используется Unix socket:

```text
/var/spool/postfix/private/auth
```

Открываем:

```bash
sudo nano /etc/dovecot/conf.d/10-master.conf
```

Настраиваем:

```conf
service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
}
```

Перезапускаем:

```bash
sudo systemctl restart dovecot
```

Проверяем:

```bash
ls -l /var/spool/postfix/private/auth
```

Ожидаем:

```text
srw-rw---- 1 postfix postfix ... /var/spool/postfix/private/auth
```

---

## 29. SMTP AUTH в Postfix

```bash
sudo postconf -e 'smtpd_sasl_type = dovecot'
sudo postconf -e 'smtpd_sasl_path = private/auth'
sudo postconf -e 'smtpd_sasl_auth_enable = yes'
```

Проверка:

```bash
postconf smtpd_sasl_type smtpd_sasl_path smtpd_sasl_auth_enable
```

Ожидаем:

```text
smtpd_sasl_type = dovecot
smtpd_sasl_path = private/auth
smtpd_sasl_auth_enable = yes
```

---

## 30. Зачем нужен TLS на 587

SMTP AUTH передаёт учётные данные.

Нельзя отправлять пароль по обычному незашифрованному соединению.

Поэтому клиент подключается к TCP 587, затем выполняет `STARTTLS`, и только внутри TLS-сессии делает SMTP AUTH.

Для этого нужен нормальный сертификат.

---

## 31. Let's Encrypt

Проверяем, свободен ли порт 80:

```bash
sudo ss -ltnp | grep ':80'
```

Устанавливаем Certbot:

```bash
sudo apt install certbot
```

Проверка:

```bash
certbot --version
```

Выпускаем сертификат:

```bash
sudo certbot certonly --standalone -d mail.projectsbis.ru
```

Certbot временно слушает порт 80 для проверки владения доменом.

После успеха:

```text
/etc/letsencrypt/live/mail.projectsbis.ru/fullchain.pem
/etc/letsencrypt/live/mail.projectsbis.ru/privkey.pem
```

Certbot также создаёт задачу автоматического продления.

---

## 32. Подключение Let's Encrypt к Postfix

```bash
sudo postconf -e 'smtpd_tls_cert_file = /etc/letsencrypt/live/mail.projectsbis.ru/fullchain.pem'
sudo postconf -e 'smtpd_tls_key_file = /etc/letsencrypt/live/mail.projectsbis.ru/privkey.pem'
```

Проверяем:

```bash
postconf smtpd_tls_cert_file smtpd_tls_key_file smtpd_tls_security_level
```

На порту 25 глобальный режим может оставаться:

```text
smtpd_tls_security_level = may
```

Для 587 TLS потребуем отдельно.

---

## 33. Включение Submission на 587

Открываем:

```bash
sudo nano /etc/postfix/master.cf
```

Нужен сервис:

```text
submission
```

Это порт:

```text
587
```

Не путать с:

```text
submissions
```

который обычно соответствует 465.

Настраиваем:

```conf
submission inet n       -       y       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_sasl_type=dovecot
  -o smtpd_sasl_path=private/auth
  -o smtpd_sasl_security_options=noanonymous
  -o smtpd_client_restrictions=permit_sasl_authenticated,reject
```

Что здесь важно:

```text
smtpd_tls_security_level=encrypt
```

без STARTTLS клиент не сможет нормально использовать submission.

```text
smtpd_sasl_auth_enable=yes
```

включает логин/пароль.

```text
permit_sasl_authenticated,reject
```

разрешает клиенту работу только после успешной SMTP-аутентификации.

Проверка синтаксиса:

```bash
sudo postfix check
```

Если вывода нет — синтаксис корректен.

Перезапуск:

```bash
sudo systemctl restart postfix
```

Проверяем:

```bash
sudo ss -ltnp | grep ':587'
```

Ожидаем:

```text
0.0.0.0:587
[::]:587
```

---

## 34. Проверка TLS на 587

```bash
openssl s_client   -connect mail.projectsbis.ru:587   -starttls smtp   -servername mail.projectsbis.ru
```

Рабочий результат:

```text
subject=CN = mail.projectsbis.ru
Verification: OK
Verify return code: 0 (ok)
```

У нас соединение установилось с TLSv1.3.

После handshake вводим:

```text
EHLO test
```

Ожидаем среди возможностей:

```text
250-AUTH PLAIN
```

---

## 35. Создание отдельного SMTP-пользователя

Не используем root для авторизации приложения.

Создаём:

```bash
sudo adduser projectsbis
```

Задаём длинный случайный пароль.

Остальные поля можно оставить пустыми.

Проверка:

```bash
getent passwd projectsbis
```

Потом проверяем Dovecot:

```bash
sudo doveadm auth test projectsbis
```

Вводим пароль.

Ожидаем:

```text
passdb: projectsbis auth succeeded
```

---

## 36. Проверка SMTP AUTH

Подключаемся:

```bash
openssl s_client   -connect mail.projectsbis.ru:587   -starttls smtp   -servername mail.projectsbis.ru   -crlf
```

После:

```text
EHLO test
```

видим:

```text
250-AUTH PLAIN
```

AUTH PLAIN использует строку:

```text
NUL + username + NUL + password
```

закодированную в Base64.

Для ручного теста:

```bash
printf '\0projectsbis\0PASSWORD' | base64 -w0
```

Полученную Base64-строку вводим уже **внутри SMTP-сессии**:

```text
AUTH PLAIN BASE64_STRING
```

Успех:

```text
235 2.7.0 Authentication successful
```

Важно: Base64 — не шифрование. Если строка AUTH попала куда-то публично, пароль нужно сменить:

```bash
sudo passwd projectsbis
```

---

## 37. Настройки ProjectSbis

В `.env`:

```env
MAIL_FROM_EMAIL=info@projectsbis.ru
MAIL_FROM_NAME=ProjectSbis

MAIL_SMTP_HOST=mail.projectsbis.ru
MAIL_SMTP_PORT=587

MAIL_SMTP_USERNAME=projectsbis
MAIL_SMTP_PASSWORD=СЕКРЕТНЫЙ_ПАРОЛЬ
```

`.env` должен находиться в `.gitignore`.

Никогда не коммитим SMTP-пароль в Git.

Схема Python-клиента:

```text
smtplib
   │
   ├─ connect mail.projectsbis.ru:587
   ├─ EHLO
   ├─ STARTTLS
   ├─ EHLO
   ├─ LOGIN/AUTH
   ├─ MAIL FROM info@projectsbis.ru
   ├─ RCPT TO ...
   └─ DATA
```

Postfix затем самостоятельно отправит письмо на MX-сервер получателя.

---

## 38. Что пока НЕ настроено

Текущая конфигурация ориентирована прежде всего на исходящую почту.

Мы можем отправлять:

```text
info@projectsbis.ru
```

Но полноценный входящий mailbox `info` пока не создавали.

Сейчас `projectsbis.ru` находится в `mydestination`, поэтому Postfix считает этот домен локальным.

Но Unix-пользователя:

```text
info
```

мы не создавали.

Следовательно, входящие письма на `info@projectsbis.ru` пока не являются нормальным рабочим почтовым ящиком.

---

## 39. Что нужно будет сделать для входящей почты

Если хотим принимать:

- ответы клиентов;
- bounce;
- delivery status;
- обычные письма на `info@projectsbis.ru`;

нужно отдельно настроить MX, например:

```text
projectsbis.ru MX 10 mail.projectsbis.ru
```

а затем решить, где реально хранить почту:

- Unix mailbox;
- Dovecot IMAP;
- виртуальные mailbox;
- alias/forward;
- внешний почтовый сервис.

Это отдельный этап.

---

## 40. UTF-8 письма

Команда:

```bash
echo "Русский текст" | mail ...
```

может создать некорректные MIME-заголовки.

В тестах русский текст отображался как:

```text
Ð¢ÐµÑ...
```

Для Python это решается через стандартный модуль:

```python
email.message.EmailMessage
```

Он корректно создаёт UTF-8 MIME-заголовки.

Поэтому реальные письма будем формировать не shell-командой `mail`, а Python `EmailMessage`.

---

## 41. Автообновление TLS

Certbot автоматически продлевает сертификат.

Но после обновления сертификата Postfix должен перечитать его.

Поэтому желательно добавить deploy hook:

```text
после успешного renewal
→ reload Postfix
```

Это стоит сделать до долгой эксплуатации сервера.

---

## 42. IPv6

В логах Postfix была строка:

```text
connect to mx.yandex.ru[IPv6]:25: Network is unreachable
```

После этого Postfix автоматически попробовал IPv4 и успешно отправил письмо.

Это не помешало доставке.

Если IPv6 на VPS не настроен, позже можно либо:

- нормально настроить IPv6;
- либо явно ограничить Postfix IPv4.

---

## 43. Проверка всей системы после перезагрузки

```bash
hostname -f
```

Ожидаем:

```text
mail.projectsbis.ru
```

Проверяем сервисы:

```bash
sudo systemctl status postfix --no-pager
sudo systemctl status opendkim --no-pager
sudo systemctl status dovecot --no-pager
```

Порты:

```bash
sudo ss -ltnp | grep ':25'
sudo ss -ltnp | grep ':587'
sudo ss -ltnp | grep ':8891'
```

DKIM:

```bash
sudo opendkim-testkey -d projectsbis.ru -s default -vvv
```

Postfix/OpenDKIM:

```bash
postconf smtpd_milters
postconf non_smtpd_milters
```

SMTP AUTH:

```bash
postconf smtpd_sasl_type
postconf smtpd_sasl_path
postconf smtpd_sasl_auth_enable
```

---

## 44. Проверка DNS с Windows

A:

```powershell
Resolve-DnsName mail.projectsbis.ru -Type A
```

SPF:

```powershell
Resolve-DnsName projectsbis.ru -Type TXT -Server ns1.reg.ru
```

DKIM:

```powershell
Resolve-DnsName default._domainkey.projectsbis.ru -Type TXT -Server ns1.reg.ru
```

DMARC:

```powershell
Resolve-DnsName _dmarc.projectsbis.ru -Type TXT -Server ns1.reg.ru
```

Порт submission:

```powershell
Test-NetConnection mail.projectsbis.ru -Port 587
```

---

## 45. Итоговая конфигурация

```text
Domain:
projectsbis.ru

SMTP hostname:
mail.projectsbis.ru

IPv4:
2.26.51.175

SMTP server:
Postfix

DKIM:
OpenDKIM

SMTP AUTH:
Dovecot SASL

Submission:
TCP 587

Encryption:
STARTTLS

Certificate:
Let's Encrypt

Sender:
info@projectsbis.ru

SMTP login:
projectsbis
```

Проверено на реальной доставке в Яндекс:

```text
spf=pass
dkim=pass
```

DKIM:

```text
d=projectsbis.ru
s=default
```

Envelope:

```text
From: info@projectsbis.ru
Return-Path: info@projectsbis.ru
```

---

## 46. Краткая цепочка прохождения реального письма

```text
1. Python подключается к mail.projectsbis.ru:587
2. Сервер отвечает SMTP banner.
3. Python выполняет EHLO.
4. Python включает STARTTLS.
5. Проверяется TLS-сертификат mail.projectsbis.ru.
6. Python проходит SMTP AUTH пользователем projectsbis.
7. Python передаёт письмо с From: info@projectsbis.ru.
8. Postfix принимает письмо в очередь.
9. OpenDKIM добавляет DKIM-Signature.
10. Postfix ищет MX домена получателя.
11. Postfix соединяется с MX получателя по TCP/25.
12. Получатель проверяет PTR.
13. Получатель проверяет SPF.
14. Получатель проверяет DKIM.
15. Получатель применяет DMARC.
16. Если письмо принято, Postfix получает SMTP 250.
17. В mail.log появляется status=sent.
```

---

## 47. Важные команды диагностики

Последние строки почтового лога:

```bash
sudo tail -n 100 /var/log/mail.log
```

Следить за логом в реальном времени:

```bash
sudo tail -f /var/log/mail.log
```

Очередь Postfix:

```bash
postqueue -p
```

или:

```bash
mailq
```

Проверка конфигурации Postfix:

```bash
sudo postfix check
```

Эффективная конфигурация Postfix:

```bash
postconf -n
```

Эффективная конфигурация Dovecot:

```bash
sudo dovecot -n
```

Статус OpenDKIM:

```bash
sudo systemctl status opendkim
```

Перезапуск:

```bash
sudo systemctl restart postfix
sudo systemctl restart opendkim
sudo systemctl restart dovecot
```

---

## 48. Что сохранить для восстановления

При переносе сервера особенно важны:

```text
/etc/postfix/
/etc/opendkim.conf
/etc/opendkim/
/etc/dovecot/
/etc/letsencrypt/
```

Отдельно критичен:

```text
/etc/opendkim/keys/projectsbis.ru/default.private
```

Если приватный ключ потерян, можно создать новый DKIM-ключ, но тогда придётся заменить публичную DKIM TXT-запись в DNS.

Также нужно сохранить информацию о DNS-записях:

```text
A mail
PTR
SPF
DKIM
DMARC
```

SMTP-пароль лучше хранить в password manager, а не в документации.

---

## 49. Безопасность

Не публиковать:

```text
SMTP password
DKIM private key
.env
AUTH PLAIN base64
```

Base64 не скрывает пароль.

Порт 587 должен использовать TLS.

Не разрешать relay всему интернету.

Не добавлять внешние IP в `mynetworks` без необходимости.

Не использовать root как SMTP-логин приложения.

Для ProjectSbis используем отдельного пользователя:

```text
projectsbis
```

и отдельный sender:

```text
info@projectsbis.ru
```

---

## 50. Что делать следующим этапом

После этой инфраструктурной настройки логичный следующий шаг:

```text
ProjectSbis
→ SMTPMailProvider
→ smtplib
→ STARTTLS
→ SMTP AUTH
→ первое тестовое письмо на собственный адрес
```

До реальной массовой отправки дополнительно стоит сделать:

- suppression-list;
- unsubscribe;
- обработку hard bounce;
- нормализацию и дедупликацию email;
- ограничение скорости отправки;
- отдельный журнал SMTP-ошибок;
- мониторинг очереди Postfix;
- обработку ответов и bounce;
- постепенную отправку с нового IP, а не сразу большой объём.
