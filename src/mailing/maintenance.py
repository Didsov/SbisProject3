"""Offline deployment helpers. No SMTP, no service management."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from src import database as db


def backup_database(source: Path, destination: Path):
    """SQLite online backup API includes committed WAL without touching source data.

    Exclusively create the destination: never overwrite an existing backup.
    The caller supplies its directory, ownership and retention policy.
    """
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination:
        raise ValueError('Backup destination equals source')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(destination)) as copy:
            original.backup(copy, pages=256)
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity_check failed')
            tables = [r[0] for r in copy.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {name: copy.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                    for name in tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('--output', required=True, type=Path)
    sub.add_parser('migrate-paused')
    sub.add_parser('classify-legacy')
    args = parser.parse_args()
    if args.action == 'backup':
        print(json.dumps(backup_database(db.DATABASE_FILE, args.output), indent=2))
    elif args.action == 'classify-legacy':
        from src.mailing.control import provider_for, ensure_provider
        import time
        with closing(db.get_connection()) as c:
            if not c.execute('SELECT paused FROM mail_control WHERE id=1').fetchone()[0]:
                raise RuntimeError('Pause sending before legacy classification')
            rows = c.execute("""SELECT m.id,COALESCE(m.smtp_recipient_email,r.email) AS email
                FROM mail_messages m JOIN mail_recipients r ON r.id=m.recipient_id
                WHERE m.provider='smtp' AND m.is_test=0 AND m.status='sent'
                AND m.delivery_status IN ('unknown','deferred') AND m.mx_provider IS NULL""").fetchall()
        classified = 0
        for row in rows:
            provider = provider_for(row['email'])
            if provider:
                with closing(db.get_connection()) as c, c:
                    ensure_provider(c, provider, time.time())
                    c.execute('UPDATE mail_messages SET mx_provider=? WHERE id=? AND mx_provider IS NULL',
                              (provider, row['id']))
                classified += 1
        print(json.dumps(dict(classified=classified, unresolved=len(rows)-classified)))
    else:
        from src.mailing.control import manage, status
        db.initialize_database()
        manage('pause')
        snapshot = status()
        print(json.dumps(dict(paused=snapshot['paused'], safety_revision=snapshot['safety_revision']), indent=2))


if __name__ == '__main__':
    main()
