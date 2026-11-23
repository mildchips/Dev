"""Single-operator LOCAL authentication gate, not SaaS tenant RBAC.

Scrypt password verifier + hashed opaque sessions + CSRF token + login throttling.
No plaintext password/session token is stored in SQLite. HTTP restricted to loopback.
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS local_auth (
 id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB NOT NULL, verifier BLOB NOT NULL,
 changed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS local_sessions (
 token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, created_at INTEGER NOT NULL,
 last_seen INTEGER NOT NULL, expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS local_login_guard (
 id INTEGER PRIMARY KEY CHECK(id=1), fail_count INTEGER NOT NULL DEFAULT 0,
 locked_until INTEGER NOT NULL DEFAULT 0);
'''
COOKIE_NAME = 'aipo_local_session'
SESSION_SECONDS = 12 * 60 * 60
IDLE_SECONDS = 30 * 60
MIN_PASSWORD_LENGTH = 12


def _conn(db_path: Path):
    db = sqlite3.connect(str(db_path), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000')
    return db


def init_schema(db_path: Path):
    with _conn(db_path) as db:
        db.executescript(SCHEMA)


def configured(db_path: Path) -> bool:
    with _conn(db_path) as db:
        return db.execute('SELECT 1 FROM local_auth WHERE id=1').fetchone() is not None


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1, dklen=32)


def set_password(db_path: Path, password: str, *, allow_reset: bool = False):
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > 1024:
        raise ValueError('Passphrase must be at least 12 and at most 1024 characters')
    salt = secrets.token_bytes(32)
    digest = _hash_password(password, salt)
    with _conn(db_path) as db:
        if db.execute('SELECT 1 FROM local_auth WHERE id=1').fetchone() and not allow_reset:
            raise ValueError('Already initialized. Use reset-password, which terminates all sessions.')
        db.execute('INSERT INTO local_auth(id,salt,verifier,changed_at) VALUES(1,?,?,?) '
                   'ON CONFLICT(id) DO UPDATE SET salt=excluded.salt,verifier=excluded.verifier,changed_at=excluded.changed_at',
                   (salt, digest, int(time.time())))
        db.execute('DELETE FROM local_sessions')
        db.execute('DELETE FROM local_login_guard')
    if os.name == 'posix':
        db_path.chmod(0o600)


def login(db_path: Path, password: str) -> tuple[str, str] | None:
    now = int(time.time())
    with _conn(db_path) as db:
        row = db.execute('SELECT salt,verifier FROM local_auth WHERE id=1').fetchone()
        if not row:
            return None
        lock = db.execute('SELECT fail_count,locked_until FROM local_login_guard WHERE id=1').fetchone()
        if lock and lock['locked_until'] > now:
            return None
        actual = _hash_password(password[:1024], row['salt'])
        if not hmac.compare_digest(actual, row['verifier']):
            failures = (lock['fail_count'] if lock else 0) + 1
            wait = 60 if failures >= 5 else 0
            db.execute('INSERT INTO local_login_guard(id,fail_count,locked_until) VALUES(1,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET fail_count=excluded.fail_count,locked_until=excluded.locked_until',
                       (failures, now + wait))
            return None
        db.execute('DELETE FROM local_login_guard')
        raw = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        db.execute('INSERT INTO local_sessions(token_hash,csrf,created_at,last_seen,expires_at) VALUES(?,?,?,?,?)',
                   (hashlib.sha256(raw.encode()).hexdigest(), csrf, now, now, now + SESSION_SECONDS))
        db.execute('DELETE FROM local_sessions WHERE expires_at <= ? OR last_seen < ?', (now, now-IDLE_SECONDS))
        return raw, csrf


def verify_session(db_path: Path, token: str | None) -> str | None:
    if not token or len(token) > 256:
        return None
    now = int(time.time())
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with _conn(db_path) as db:
        row = db.execute('SELECT csrf,expires_at,last_seen FROM local_sessions WHERE token_hash=?', (token_hash,)).fetchone()
        if not row or row['expires_at'] <= now or row['last_seen'] < now-IDLE_SECONDS:
            return None
        db.execute('UPDATE local_sessions SET last_seen=? WHERE token_hash=?', (now, token_hash))
        return row['csrf']


def logout(db_path: Path, token: str | None):
    if token:
        with _conn(db_path) as db:
            db.execute('DELETE FROM local_sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))


def main():
    import argparse
    from .storage import initialize
    parser=argparse.ArgumentParser(description='Local login configuration; never give passwords to AI or shell arguments')
    parser.add_argument('operation',choices=('init-password','reset-password'))
    parser.add_argument('--db',default=os.environ.get('AI_PROFIT_DB',str(Path.home()/'.ai_profit_office'/'prealpha.db')))
    args=parser.parse_args()
    path=Path(args.db).expanduser().resolve()
    initialize(path)
    init_schema(path)
    a=getpass.getpass('Local app passphrase (12+ characters): ')
    b=getpass.getpass('Repeat passphrase: ')
    if a!=b:
        print('Passphrases do not match.',file=sys.stderr)
        return 2
    try:
        set_password(path,a,allow_reset=args.operation=='reset-password')
    except ValueError as ex:
        print('ERROR:',ex,file=sys.stderr)
        return 2
    print('Local authentication configured; old sessions were invalidated.')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
