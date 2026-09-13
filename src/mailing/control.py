"""Persistent pacing and provider circuit breakers for the existing mail queue."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import json
import logging
import os
import time
from email.utils import make_msgid

from src import database as db
from src.daily_lock import daily_run_lock, DailyRunAlreadyRunningError

log = logging.getLogger(__name__)
DEFAULTS = dict(global_interval=23, provider_interval=23, consecutive=3,
                window_size=20, window_spam=5, max_unanswered=5,
                heartbeat_timeout=60, daily_limit=1000)
MX_SUFFIXES = {"mailru": ["mail.ru"], "gmail": ["google.com", "googlemail.com"],
               "yandex": ["yandex.net", "yandex.ru"]}


def initialize_control_tables(c):
    # Only additive migrations; old messages and statuses retain their meaning.
    for statement in (
        """CREATE TABLE IF NOT EXISTS mail_control (
            id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL,
            last_handoff REAL, active_message INTEGER, heartbeat REAL,
            paused INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS mail_provider_periods (
            id INTEGER PRIMARY KEY, provider TEXT NOT NULL, started REAL NOT NULL,
            ended REAL, state TEXT NOT NULL DEFAULT 'active', reason TEXT,
            paused_at REAL, consecutive INTEGER DEFAULT 0, spam_count INTEGER DEFAULT 0,
            diagnostic TEXT)""",
        """CREATE TABLE IF NOT EXISTS mail_providers (
            provider TEXT PRIMARY KEY, period_id INTEGER NOT NULL, last_handoff REAL)""",
        """CREATE TABLE IF NOT EXISTS mail_mx_cache (
            domain TEXT PRIMARY KEY, provider TEXT, expires REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS mail_delivery_events (
            event_key TEXT PRIMARY KEY, message_id INTEGER NOT NULL,
            recipient TEXT NOT NULL, queue_id TEXT NOT NULL, event_time REAL NOT NULL,
            status TEXT NOT NULL, reason TEXT, dsn TEXT, relay TEXT, detail TEXT,
            raw_line TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_delivery_message ON mail_delivery_events(message_id, event_time, event_key)",
        """CREATE TABLE IF NOT EXISTS mail_queue_ids (
            queue_id TEXT NOT NULL, provider_message_id TEXT NOT NULL,
            event_time REAL NOT NULL, PRIMARY KEY(queue_id, event_time))""",
    ):
        c.execute(statement)
    c.execute("INSERT OR IGNORE INTO mail_control(id,settings) VALUES(1,?)",
              (json.dumps(DEFAULTS),))
    columns = {r[1] for r in c.execute("PRAGMA table_info(mail_messages)")}
    for name, kind in (("mx_provider", "TEXT"), ("observation_period", "INTEGER"),
                       ("handoff_at", "REAL"), ("result_reason", "TEXT")):
        if name not in columns:
            c.execute(f"ALTER TABLE mail_messages ADD COLUMN {name} {kind}")
    if 'mx_provider' not in {r[1] for r in c.execute('PRAGMA table_info(mail_recipients)')}:
        c.execute('ALTER TABLE mail_recipients ADD COLUMN mx_provider TEXT')
    c.execute('CREATE INDEX IF NOT EXISTS idx_mail_observation ON mail_messages(observation_period)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_mail_mx_provider ON mail_messages(mx_provider,delivery_status)')


def settings(c):
    return {**DEFAULTS, **json.loads(c.execute(
        "SELECT settings FROM mail_control WHERE id=1").fetchone()[0])}


def ensure_provider(c, provider, now):
    row = c.execute("SELECT period_id FROM mail_providers WHERE provider=?", (provider,)).fetchone()
    if row:
        return row[0]
    period = c.execute("INSERT INTO mail_provider_periods(provider,started) VALUES(?,?)",
                       (provider, now)).lastrowid
    c.execute("INSERT INTO mail_providers(provider,period_id) VALUES(?,?)", (provider, period))
    return period


def provider_for(email, *, now=None, resolver=None):
    now = time.time() if now is None else now
    domain = email.rsplit('@', 1)[-1].strip().lower().encode('idna').decode('ascii')
    with closing(db.get_connection()) as c:
        cached = c.execute("SELECT provider FROM mail_mx_cache WHERE domain=? AND expires>?",
                           (domain, now)).fetchone()
    if cached:
        return cached[0]
    mapping = json.loads(os.getenv('MAIL_MX_PROVIDERS', json.dumps(MX_SUFFIXES)))
    try:
        if resolver is None:
            import dns.resolver
            answer = dns.resolver.resolve(domain, 'MX', lifetime=5)
            hosts = [str(r.exchange).rstrip('.').lower() for r in answer]
            ttl = min(int(answer.rrset.ttl), 3600)
        else:
            hosts = [h.rstrip('.').lower() for h in resolver(domain)]
            ttl = 3600
        if not hosts or any(not h for h in hosts):
            raise ValueError('No usable MX')
        groups = set()
        for host in hosts:
            matches = {p for p, suffixes in mapping.items() for suffix in suffixes
                       if host == suffix.rstrip('.').lower()
                       or host.endswith('.' + suffix.rstrip('.').lower())}
            if len(matches) > 1:
                raise ValueError('Ambiguous MX mapping')
            groups.add(next(iter(matches)) if matches else 'mx:' + host)
        # Mixed unknown MX sets have their own stable group, never a guessed provider.
        known = groups.intersection(mapping)
        if len(known) > 1 or (known and len(groups) > 1):
            raise ValueError('Mixed MX providers')
        provider = next(iter(known)) if known else '|'.join(sorted(groups))
    except Exception:
        provider, ttl = None, 60
    with closing(db.get_connection()) as c, c:
        c.execute("INSERT OR REPLACE INTO mail_mx_cache VALUES(?,?,?)",
                  (domain, provider, now + max(ttl, 1)))
    return provider


def evaluate(c, provider, now):
    period = ensure_provider(c, provider, now)
    state = c.execute("SELECT * FROM mail_provider_periods WHERE id=?", (period,)).fetchone()
    if state['state'] != 'active':
        return state['state']
    cfg = settings(c)
    # One final result per message; event time + content hash is a stable tie break.
    rows = c.execute("""SELECT e.* FROM mail_delivery_events e
        JOIN mail_messages m ON m.id=e.message_id
        WHERE m.observation_period=? AND e.status IN ('delivered','bounced')
          AND NOT EXISTS (SELECT 1 FROM mail_delivery_events earlier
            WHERE earlier.message_id=e.message_id AND earlier.status IN ('delivered','bounced')
            AND (earlier.event_time<e.event_time OR
              (earlier.event_time=e.event_time AND earlier.event_key<e.event_key)))
        ORDER BY e.event_time,e.event_key""", (period,)).fetchall()
    window, consecutive = [], 0
    for row in rows:
        spam = row['reason'] == 'spam_rejected'
        consecutive = consecutive + 1 if spam else 0
        window = (window + [spam])[-cfg['window_size']:]
        if consecutive >= cfg['consecutive'] or sum(window) >= cfg['window_spam']:
            reason = 'consecutive_spam' if consecutive >= cfg['consecutive'] else 'window_spam'
            c.execute("""UPDATE mail_provider_periods SET state='paused_spam',reason=?,
                paused_at=?,consecutive=?,spam_count=?,diagnostic=? WHERE id=?""",
                (reason, now, consecutive, sum(window), row['detail'], period))
            log.error('MAILING paused_spam provider=%s reason=%s consecutive=%s spam_count=%s',
                      provider, reason, consecutive, sum(window))
            return 'paused_spam'
    c.execute("UPDATE mail_provider_periods SET consecutive=?,spam_count=? WHERE id=?",
              (consecutive, sum(window), period))
    return 'active'


def blocked(c, provider, now):
    cfg = settings(c)
    state = c.execute("SELECT * FROM mail_control WHERE id=1").fetchone()
    if state['paused']:
        return 'manual_pause', 0
    if state['active_message'] is not None:
        return 'uncertain_handoff', 0
    if state['heartbeat'] is None or not 0 <= now-state['heartbeat'] <= cfg['heartbeat_timeout']:
        return 'log_heartbeat_stale', 0
    if evaluate(c, provider, now) != 'active':
        return 'provider_paused', 0
    if c.execute("""SELECT 1 FROM mail_messages WHERE mx_provider=? AND
        delivery_status='deferred' LIMIT 1""", (provider,)).fetchone():
        return 'deferred', 0
    unanswered = c.execute("""SELECT COUNT(*) FROM mail_messages m WHERE mx_provider=?
        AND handoff_at IS NOT NULL AND status IN ('pending','sent')
        AND NOT EXISTS(SELECT 1 FROM mail_delivery_events e WHERE e.message_id=m.id)""",
        (provider,)).fetchone()[0]
    if unanswered >= cfg['max_unanswered']:
        return 'waiting_mx', 0
    count = c.execute("""SELECT COUNT(*) FROM mail_messages WHERE is_test=0 AND provider='smtp'
        AND (handoff_at>=? OR (handoff_at IS NULL AND sent_at>=datetime(?,'unixepoch')))""",
        (now - now % 86400, now - now % 86400)).fetchone()[0]
    if count >= cfg['daily_limit']:
        return 'daily_limit', 0
    last = c.execute("SELECT last_handoff FROM mail_providers WHERE provider=?", (provider,)).fetchone()[0]
    delay = max(0, (state['last_handoff'] or 0)+cfg['global_interval']-now,
                (last or 0)+cfg['provider_interval']-now)
    return ('interval', delay) if delay else (None, 0)


async def dispatch(recipient, build, transport, *, run_id=None, smtp_email=None,
                   is_test_recipient=False, clock=time.time, sleep=asyncio.sleep):
    """Check under the project's OS lock; keep it through SMTP and persistence.

    A durable active_message survives a crash. Never automatically resend an
    ambiguous SMTP handoff. Log ingestion uses independent short transactions.
    """
    provider = await asyncio.to_thread(provider_for, smtp_email or recipient['email'])
    if not provider:
        return None, 'mx_lookup_failed'
    while True:
        try:
            with daily_run_lock(db.DATABASE_FILE.with_suffix('.mail-send.lock')):
                with closing(db.get_connection()) as c, c:
                    c.execute('BEGIN IMMEDIATE')
                    c.execute('UPDATE mail_recipients SET mx_provider=? WHERE id=?',
                              (provider, recipient['recipient_id']))
                    ensure_provider(c, provider, clock())
                    reason, delay = blocked(c, provider, clock())
                    row = c.execute("SELECT status FROM mail_recipients WHERE id=?",
                                    (recipient['recipient_id'],)).fetchone()
                    attempted = c.execute("""SELECT 1 FROM mail_messages WHERE recipient_id=?
                        AND is_test=0 AND status IN ('pending','sent') LIMIT 1""",
                        (recipient['recipient_id'],)).fetchone()
                    if row is None or row[0] != 'pending' or attempted:
                        return None, 'already_claimed'
                if reason and reason != 'interval':
                    return None, reason
                if not reason:
                    record = db.create_mail_message(recipient_id=recipient['recipient_id'],
                        provider='smtp', run_id=run_id, smtp_recipient_email=smtp_email,
                        is_test_recipient=is_test_recipient)
                    message = build(record['tracking_token'])
                    message.provider_message_id = make_msgid(domain='projectsbis.ru')
                    with closing(db.get_connection()) as c, c:
                        c.execute('BEGIN IMMEDIATE')
                        # Results can arrive while the message is being built.
                        reason, _ = blocked(c, provider, clock())
                        if reason:
                            c.execute('DELETE FROM mail_messages WHERE id=?', (record['message_id'],))
                            return None, reason
                        period = ensure_provider(c, provider, clock())
                        c.execute("""UPDATE mail_messages SET mx_provider=?,observation_period=?,
                            handoff_at=?,provider_message_id=? WHERE id=?""",
                            (provider, period, clock(), message.provider_message_id, record['message_id']))
                        c.execute('UPDATE mail_control SET active_message=?,last_handoff=? WHERE id=1',
                                  (record['message_id'], clock()))
                        c.execute('UPDATE mail_providers SET last_handoff=? WHERE provider=?', (clock(), provider))
                    result = await transport.send(message)
                    if getattr(result, 'uncertain', False):
                        log.error('MAILING uncertain_handoff message_id=%s', record['message_id'])
                        return None, 'uncertain_handoff'
                    db.complete_mail_message(message_id=record['message_id'],
                        provider_message_id=result.provider_message_id or message.provider_message_id,
                        success=result.success, error=result.error)
                    with closing(db.get_connection()) as c, c:
                        c.execute('UPDATE mail_control SET active_message=NULL,last_handoff=? WHERE id=1', (clock(),))
                        c.execute('UPDATE mail_providers SET last_handoff=? WHERE provider=?', (clock(), provider))
                    return result, 'sent' if result.success else 'failed'
        except DailyRunAlreadyRunningError:
            return None, 'sender_busy'
        if delay:
            await sleep(min(delay, 1))


def status():
    with closing(db.get_connection()) as c:
        control = dict(c.execute('SELECT * FROM mail_control WHERE id=1').fetchone())
        control['settings'] = settings(c)
        control['providers'] = [dict(r) for r in c.execute("""SELECT p.*,
            (SELECT COUNT(*) FROM mail_recipients r WHERE r.mx_provider=p.provider
             AND r.status IN ('pending','deferred') AND NOT EXISTS(
                SELECT 1 FROM mail_messages m WHERE m.recipient_id=r.id AND m.is_test=0
                AND m.status IN ('pending','sent'))) AS not_handed_off,
            (SELECT COUNT(*) FROM mail_messages m WHERE m.mx_provider=p.provider
             AND m.delivery_status='deferred') AS deferred_deliveries,
            (SELECT COUNT(*) FROM mail_messages m WHERE m.mx_provider=p.provider
             AND m.handoff_at IS NOT NULL AND m.status IN ('pending','sent')
             AND m.delivery_status NOT IN ('delivered','bounced')) AS awaiting_final
            FROM mail_provider_periods p JOIN mail_providers current ON current.period_id=p.id""")]
        control['not_handed_off'] = c.execute("""SELECT COUNT(*) FROM mail_recipients r
            WHERE r.status IN ('pending','deferred') AND NOT EXISTS(SELECT 1 FROM mail_messages m
                WHERE m.recipient_id=r.id AND m.is_test=0 AND m.status IN ('pending','sent'))""").fetchone()[0]
        control['unresolved_provider'] = c.execute("SELECT COUNT(*) FROM mail_recipients WHERE mx_provider IS NULL AND status='pending'").fetchone()[0]
        control['heartbeat_age'] = None if control['heartbeat'] is None else time.time()-control['heartbeat']
        return control


def report_summary():
    snapshot = status()
    lines = [f"Очередь Python: {snapshot['not_handed_off']}"]
    for p in snapshot['providers']:
        lines.append(f"{p['provider']}: {p['state']}; причина={p['reason'] or '-'}; "
                     f"серия={p['consecutive']}; отказы в окне={p['spam_count']}; "
                     f"передано без итогового ответа={p['awaiting_final']}")
    lines.append('Остановка Python не отзывает уже принятые Postfix письма.')
    return '\n'.join(lines)


def manage(action, provider=None, changes=None):
    with daily_run_lock(db.DATABASE_FILE.with_suffix('.mail-send.lock')):
        with closing(db.get_connection()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            now = time.time()
            if action == 'configure':
                cfg = settings(c)
                for key, value in changes.items():
                    if key not in DEFAULTS or not isinstance(value, int) or value < 1:
                        raise ValueError('Unknown setting or non-positive integer')
                    cfg[key] = value
                if cfg['window_spam'] > cfg['window_size']:
                    raise ValueError('window_spam exceeds window_size')
                c.execute('UPDATE mail_control SET settings=? WHERE id=1', (json.dumps(cfg),))
            elif provider:
                period = ensure_provider(c, provider, now)
                if action == 'pause':
                    c.execute("UPDATE mail_provider_periods SET state='paused_manual',reason='operator',paused_at=? WHERE id=?", (now, period))
                elif action == 'resume':
                    c.execute('UPDATE mail_provider_periods SET ended=? WHERE id=?', (now, period))
                    new = c.execute('INSERT INTO mail_provider_periods(provider,started) VALUES(?,?)', (provider, now)).lastrowid
                    c.execute('UPDATE mail_providers SET period_id=? WHERE provider=?', (new, provider))
            elif action in ('pause', 'resume'):
                c.execute('UPDATE mail_control SET paused=? WHERE id=1', (int(action == 'pause'),))
            elif action == 'acknowledge-handoff':
                # Operator reconciles with Postfix first; existing pending message
                # remains claimed, so this operation cannot resend it.
                c.execute('''UPDATE mail_providers SET last_handoff=? WHERE provider=(
                    SELECT mx_provider FROM mail_messages WHERE id=(
                    SELECT active_message FROM mail_control WHERE id=1))''', (now,))
                c.execute('UPDATE mail_control SET active_message=NULL,last_handoff=? WHERE id=1', (now,))


def main():
    parser = argparse.ArgumentParser(description='Управление очередью SMTP')
    parser.add_argument('action', choices=['status','pause','resume','configure','acknowledge-handoff'])
    parser.add_argument('--provider')
    parser.add_argument('--set', action='append', default=[], metavar='KEY=INTEGER')
    args = parser.parse_args()
    db.initialize_database()
    if args.action != 'status':
        manage(args.action, args.provider, dict((k, int(v)) for k, v in (s.split('=', 1) for s in args.set)))
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    print('Остановка Python не отзывает уже принятые Postfix письма.')


if __name__ == '__main__':
    main()
