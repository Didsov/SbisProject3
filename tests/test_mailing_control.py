from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from src import database as db
from src.mailing import control, etrn, postfix_delivery as logs
from src.mailing.sender import MailMessage
from src.mailing.smtp_provider import SMTPSendResult


class ControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(db, 'DATABASE_FILE', self.root / 'test.db')
        self.patch.start()
        db.initialize_database()
        self.now = time.time()
        self.heartbeat()
        self.campaign = etrn.ensure_etrn_campaign()
        self.sent = []
        self.lines = []
        self.provider_patch = patch.object(control, 'provider_for', return_value='mailru')
        self.provider_patch.start()

    def tearDown(self):
        self.provider_patch.stop()
        self.patch.stop()
        # Older database helpers rely on connection finalizers.
        import gc
        gc.collect()
        self.temp.cleanup()

    def heartbeat(self):
        with closing(db.get_connection()) as c, c:
            c.execute('UPDATE mail_control SET heartbeat=?', (self.now,))

    def recipient(self):
        with closing(db.get_connection()) as c, c:
            n = c.execute('SELECT COUNT(*) FROM clients').fetchone()[0] + 1
            client = c.execute('INSERT INTO clients(spp_uuid,inn,name) VALUES(?,?,?)',
                               (str(n), str(n), 'Test')).lastrowid
        etrn._queue_email(self.campaign, client, f'u{n}@example.test')
        return etrn._eligible_recipients(self.campaign, 1000)[-1]

    async def sleep(self, duration):
        self.now += duration
        self.heartbeat()

    async def send(self, message):
        self.sent.append((self.now, message))
        return SMTPSendResult(True, message.provider_message_id)

    async def dispatch(self, recipient=None, transport=None):
        recipient = recipient or self.recipient()
        return await control.dispatch(recipient, lambda token: MailMessage(
            recipient['recipient_id'], recipient['email'], 'subject', 'text', '<p>text</p>'),
            transport or self, clock=lambda: self.now, sleep=self.sleep)

    def event(self, index, status='bounced', detail='550 spam message rejected', *, at=None):
        message = self.sent[index][1]
        timestamp = datetime.fromtimestamp(self.now if at is None else at, timezone.utc).isoformat()
        queue = f'ABC{index:04d}'
        self.lines += [f'{timestamp} host postfix/cleanup[1]: {queue}: message-id={message.provider_message_id}',
            f'{timestamp} host postfix/smtp[1]: {queue}: to=<{message.to_email}>, relay=mx.mail.ru[192.0.2.1]:25, '
            f'dsn={"2.0.0" if status == "sent" else "4.7.1" if status == "deferred" else "5.7.1"}, status={status} ({detail})']

    def sync(self):
        path = self.root / 'mail.log'
        path.write_text('\n'.join(self.lines) + '\n', encoding='utf-8')
        logs.synchronize_delivery_statuses(log_path=path)
        self.heartbeat()

    async def test_spacing_restart_idle_and_no_duplicate(self):
        recipient = self.recipient()
        await self.dispatch(recipient)
        self.assertEqual((await self.dispatch(recipient))[1], 'already_claimed')
        await self.dispatch()
        db.initialize_database()
        self.now += 1000
        self.heartbeat()
        await self.dispatch()
        await self.dispatch()
        times = [r[0] for r in self.sent]
        self.assertTrue(all(b-a >= 23 for a, b in zip(times, times[1:])))

    async def test_three_spam_persist_resume_and_reimport(self):
        for i in range(3):
            await self.dispatch()
            self.event(i)
            self.sync()
        self.assertEqual(control.status()['providers'][0]['state'], 'paused_spam')
        db.initialize_database()
        self.assertEqual((await self.dispatch())[1], 'provider_paused')
        self.sync()
        with closing(db.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM mail_delivery_events').fetchone()[0], 3)
        control.manage('resume', 'mailru')
        self.sync()
        self.assertEqual(control.status()['providers'][0]['spam_count'], 0)
        await self.dispatch()
        self.assertEqual(len(self.sent), 4)
        with closing(db.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM mail_provider_periods').fetchone()[0], 2)

    async def test_five_in_partial_window_with_deliveries(self):
        for i in range(9):
            await self.dispatch()
            self.event(i, 'bounced' if i % 2 == 0 else 'sent',
                       '550 spam message rejected' if i % 2 == 0 else '250 OK')
            self.sync()
        state = control.status()['providers'][0]
        self.assertEqual((state['state'], state['spam_count']), ('paused_spam', 5))

    async def test_deferred_blocks_without_python_retry(self):
        r = self.recipient()
        await self.dispatch(r)
        self.event(0, 'deferred', '450 try later')
        self.sync()
        self.assertEqual((await self.dispatch())[1], 'deferred')
        self.event(0, 'sent', '250 OK', at=self.now + 1)
        self.sync()
        await self.dispatch()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual((await self.dispatch(r))[1], 'already_claimed')

    async def test_inflight_heartbeat_and_provider_isolation(self):
        for _ in range(5):
            await self.dispatch()
        self.assertEqual((await self.dispatch())[1], 'waiting_mx')
        with patch.object(control, 'provider_for', return_value='gmail'):
            await self.dispatch()
        self.assertEqual(len(self.sent), 6)
        self.now += 61
        self.assertEqual((await self.dispatch())[1], 'log_heartbeat_stale')

    async def test_overlapping_senders_do_not_duplicate(self):
        r = self.recipient()
        entered, finish = asyncio.Event(), asyncio.Event()
        test = self
        class Slow:
            async def send(self, message):
                entered.set()
                await finish.wait()
                return await test.send(message)
        first = asyncio.create_task(self.dispatch(r, Slow()))
        await entered.wait()
        self.assertEqual((await self.dispatch(r))[1], 'sender_busy')
        finish.set()
        await first
        self.assertEqual(len(self.sent), 1)

    async def test_uncertain_handoff_is_durable_and_never_retried(self):
        class Uncertain:
            async def send(self, message):
                return SMTPSendResult(False, None, 'disconnect', uncertain=True)
        r = self.recipient()
        self.assertEqual((await self.dispatch(r, Uncertain()))[1], 'uncertain_handoff')
        db.initialize_database()
        self.assertIsNotNone(control.status()['active_message'])
        control.manage('acknowledge-handoff')
        self.assertEqual((await self.dispatch(r))[1], 'already_claimed')

    async def test_daily_limit_shared_across_providers(self):
        control.manage('configure', changes={'daily_limit': 1})
        await self.dispatch()
        with patch.object(control, 'provider_for', return_value='gmail'):
            self.assertEqual((await self.dispatch())[1], 'daily_limit')

    async def test_provider_interval_and_manual_pause(self):
        control.manage('configure', changes={'provider_interval': 50})
        await self.dispatch()
        await self.dispatch()
        self.assertGreaterEqual(self.sent[1][0] - self.sent[0][0], 50)
        control.manage('pause', 'mailru')
        self.assertEqual((await self.dispatch())[1], 'provider_paused')
        with patch.object(control, 'provider_for', return_value='gmail'):
            await self.dispatch()
        control.manage('pause')
        self.assertEqual((await self.dispatch())[1], 'manual_pause')

    async def test_final_failure_breaks_series_without_clearing_window(self):
        for i, detail in enumerate(['550 spam message rejected', '550 spam message rejected',
                                     '550 user not found', '550 spam message rejected']):
            await self.dispatch()
            self.event(i, detail=detail)
            self.sync()
        state = control.status()['providers'][0]
        self.assertEqual((state['state'], state['consecutive'], state['spam_count']), ('active', 1, 3))

    async def test_event_time_order_and_old_period_results(self):
        for _ in range(4):
            await self.dispatch()
        base = self.now
        # Import order differs from MX order: delivered must split the series.
        self.event(0, at=base)
        self.event(1, at=base+3)
        self.event(3, at=base+2)
        self.event(2, 'sent', '250 OK', at=base+1)
        self.sync()
        state = control.status()['providers'][0]
        self.assertEqual((state['state'], state['consecutive']), ('active', 2))
        control.manage('resume', 'mailru')
        self.event(3, at=base+4)  # Another final for an old message: one vote only.
        self.sync()
        state = control.status()['providers'][0]
        self.assertEqual((state['state'], state['consecutive'], state['spam_count']), ('active', 0, 0))

    async def test_other_provider_delivery_does_not_reset_spam_series(self):
        await self.dispatch()
        self.event(0)
        self.sync()
        with patch.object(control, 'provider_for', return_value='gmail'):
            await self.dispatch()
        self.event(1, 'sent', '250 OK')
        self.sync()
        for i in (2, 3):
            await self.dispatch()
            self.event(i)
            self.sync()
        states = {p['provider']: p['state'] for p in control.status()['providers']}
        self.assertEqual(states, {'mailru': 'paused_spam', 'gmail': 'active'})

    async def test_lock_excludes_a_separate_process(self):
        import subprocess
        import sys
        r = self.recipient()
        script = '''
import asyncio, sys
from pathlib import Path
from src import database as db
from src.mailing import control
db.DATABASE_FILE = Path(sys.argv[1])
control.provider_for = lambda _: 'mailru'
result = asyncio.run(control.dispatch({'recipient_id': int(sys.argv[2]), 'email': 'a@example.test'}, None, None))
assert result == (None, 'sender_busy'), result
'''
        with control.daily_run_lock(db.DATABASE_FILE.with_suffix('.mail-send.lock')):
            result = subprocess.run([sys.executable, '-c', script, str(db.DATABASE_FILE), str(r['recipient_id'])],
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    async def test_rotation_and_limited_import_keep_feedback_safe(self):
        for _ in range(2):
            await self.dispatch()
        self.event(0, 'deferred', '450 later')
        self.event(1, 'sent', '250 OK')
        path = self.root / 'mail.log'
        path.write_text('\n'.join(self.lines)+'\n', encoding='utf-8')
        logs.synchronize_delivery_statuses(log_path=path, limit=1)
        with closing(db.get_connection()) as c:
            self.assertIsNone(c.execute('SELECT heartbeat FROM mail_control').fetchone()[0])
        path.rename(self.root / 'mail.log.1')
        path.write_text('', encoding='utf-8')
        logs.synchronize_delivery_statuses(log_path=path)
        with closing(db.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM mail_delivery_events').fetchone()[0], 2)
            self.assertIsNotNone(c.execute('SELECT heartbeat FROM mail_control').fetchone()[0])

    async def test_idle_log_heartbeat_and_missing_file_fail_closed(self):
        self.sync()
        with closing(db.get_connection()) as c:
            self.assertIsNotNone(c.execute('SELECT heartbeat FROM mail_control').fetchone()[0])
        with self.assertRaises(FileNotFoundError):
            logs.synchronize_delivery_statuses(log_path=self.root / 'missing')
        self.assertEqual((await self.dispatch())[1], 'log_heartbeat_stale')

    def test_classification(self):
        for detail, expected in [('550 user not found', 'invalid_recipient'),
                                  ('550 permission denied', 'other_failure'),
                                  ('550 spam message rejected', 'spam_rejected')]:
            self.assertEqual(logs.classify_result('bounced', '5.0.0', detail), expected)

    def test_additive_migration_preserves_legacy_messages(self):
        r = self.recipient()
        record = db.create_mail_message(recipient_id=r['recipient_id'], provider='smtp')
        with closing(db.get_connection()) as c, c:
            c.execute('DROP INDEX idx_mail_observation')
            c.execute('DROP INDEX idx_mail_mx_provider')
            for column in ('mx_provider', 'observation_period', 'handoff_at', 'result_reason'):
                c.execute(f'ALTER TABLE mail_messages DROP COLUMN {column}')
            c.execute('ALTER TABLE mail_recipients DROP COLUMN mx_provider')
            for table in ('mail_control', 'mail_provider_periods', 'mail_providers',
                          'mail_mx_cache', 'mail_delivery_events', 'mail_queue_ids'):
                c.execute(f'DROP TABLE {table}')
        db.initialize_database()
        db.initialize_database()
        with closing(db.get_connection()) as c:
            row = c.execute('SELECT * FROM mail_messages WHERE id=?', (record['message_id'],)).fetchone()
            self.assertEqual(row['tracking_token'], record['tracking_token'])
            self.assertEqual(row['status'], 'pending')
            self.assertIsNone(row['observation_period'])

    def test_mx_suffix_matching_and_negative_cache(self):
        self.provider_patch.stop()
        self.assertEqual(control.provider_for('a@corp.test', resolver=lambda _: ['mxs.mail.ru']), 'mailru')
        self.assertEqual(control.provider_for('b@corp.test', resolver=lambda _: self.fail('cached')), 'mailru')
        self.assertEqual(control.provider_for('a@fake.test', resolver=lambda _: ['mail.ru.attacker.test']), 'mx:mail.ru.attacker.test')
        self.assertIsNone(control.provider_for('a@broken.test', resolver=lambda _: []))
        self.assertIsNone(control.provider_for('b@broken.test', resolver=lambda _: self.fail('negative cache')))
        self.provider_patch.start()

    def test_smtp_disconnect_is_uncertain_but_explicit_rejection_is_not(self):
        import smtplib
        from src.mailing.smtp_provider import SMTPMailProvider
        message = MailMessage(1, 'a@example.test', 'test', 'text', '<p>text</p>',
                              include_logo=False, provider_message_id='<fixed@example.test>')
        transport = SMTPMailProvider(host='localhost', port=587, username='test', password='test',
                                     from_email='sender@example.test', from_name='Test')
        with patch('smtplib.SMTP') as smtp:
            instance = smtp.return_value.__enter__.return_value
            instance.send_message.side_effect = smtplib.SMTPServerDisconnected('connection lost')
            self.assertTrue(transport._send_sync(message).uncertain)
            instance.send_message.side_effect = smtplib.SMTPDataError(450, b'try later')
            result = transport._send_sync(message)
            self.assertFalse(result.uncertain)
            self.assertFalse(result.success)
            instance.send_message.side_effect = None
            result = transport._send_sync(message)
            self.assertTrue(result.success)
            self.assertEqual(result.provider_message_id, '<fixed@example.test>')

    def test_real_dns_adapter_without_network(self):
        from types import SimpleNamespace
        self.provider_patch.stop()
        class Answer(list):
            rrset = SimpleNamespace(ttl=10)
        with patch('dns.resolver.resolve', return_value=Answer([
            SimpleNamespace(exchange='aspmx.l.google.com.')
        ])) as resolver:
            self.assertEqual(control.provider_for('a@adapter.test'), 'gmail')
            resolver.assert_called_once_with('adapter.test', 'MX', lifetime=5)
        self.provider_patch.start()


if __name__ == '__main__':
    unittest.main()
