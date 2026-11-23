"""Local-only credential vault and hierarchical emergency stop for research release.

NO HTTP administration endpoints are exposed. NO outbound marketplace calls are implemented.
For future SaaS, replace local SQLite + key file with tenant-scoped PostgreSQL,
KMS envelope encryption, RBAC/MFA, durable queues and centralized kill-switches.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, TypeVar

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecurityError(RuntimeError):
    pass


class Stopped(SecurityError):
    pass


KEY_BASENAME = 'vault.key'
PROVIDERS = ('WB', 'Ozon')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _protect_dir(path: Path) -> None:
    if path.exists() and path.is_symlink():
        raise SecurityError('Symlinked vault directories are forbidden')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'posix':
        path.chmod(0o700)


def _check_file(path: Path) -> None:
    if path.is_symlink():
        raise SecurityError('Symlinked secrets are forbidden')
    if os.name == 'posix' and (path.stat().st_mode & 0o077):
        raise SecurityError(f'Insecure file permissions: {path} (require 0600)')


def initialize_key(vault_dir: Path) -> Path:
    """Create local development key exactly once. Do NOT use file key in production."""
    _protect_dir(vault_dir)
    dest = vault_dir / KEY_BASENAME
    if dest.exists():
        _check_file(dest)
        raise SecurityError('Key already exists; do not overwrite it (would lose access)')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(dest, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(secrets.token_bytes(32))
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    _check_file(dest)
    return dest


@dataclass(frozen=True)
class Credential:
    platform: str
    store_id: str
    client_id: str | None
    secret: str


T = TypeVar('T')


class Vault:
    """Separate local SQLite vault for testing kill policies and encrypted secrets.

    This is intentionally not exposed through the local Pre-Alpha web API.
    All credential-bearing future requests MUST pass through with_authorized().
    """

    def __init__(self, vault_dir: Path | str, store_db: Path | str):
        untrusted_dir=Path(vault_dir).expanduser()
        if untrusted_dir.is_symlink():
            raise SecurityError('Symlinked vault path forbidden')
        self.dir = untrusted_dir.resolve()
        self.db_path = self.dir / 'integrations.sqlite3'
        self.key_path = self.dir / KEY_BASENAME
        self.store_db = Path(store_db).expanduser().resolve()
        _protect_dir(self.dir)
        if not self.key_path.exists():
            raise SecurityError('Vault not initialized: run init-key first')
        _check_file(self.key_path)
        key = self.key_path.read_bytes()
        if len(key) != 32:
            raise SecurityError('Invalid AES-256 key')
        self._aead = AESGCM(key)
        if not self.store_db.is_file():
            raise SecurityError('App database not found. Start the app and create a store first.')
        if self.db_path.is_symlink():
            raise SecurityError('Symlinked credential DB forbidden')
        self._init_db()

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=10000')
            # Local secret vault prefers secure-delete and rollback journal;
            # avoiding WAL reduces retained old ciphertext after rotation.
            db.execute('PRAGMA secure_delete=ON')
            db.execute('PRAGMA journal_mode=DELETE')
            yield db
        finally:
            db.close()

    def _init_db(self):
        with self._conn() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS credentials (
                id TEXT PRIMARY KEY, store_id TEXT NOT NULL, platform TEXT NOT NULL,
                label TEXT NOT NULL, client_id TEXT, ciphertext BLOB,
                fingerprint TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
                version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS credential_store ON credentials(store_id,platform);
            CREATE TABLE IF NOT EXISTS control (
                scope TEXT NOT NULL, target TEXT NOT NULL, stopped INTEGER NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(scope,target)
            );
            CREATE TABLE IF NOT EXISTS live_permits (
                credential_id TEXT PRIMARY KEY, store_id TEXT NOT NULL, platform TEXT NOT NULL,
                path TEXT NOT NULL, expires_at TEXT NOT NULL, max_calls INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS security_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                operation TEXT NOT NULL, scope TEXT NOT NULL, target TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(credentials)').fetchall()}
            if 'expires_on' not in columns:
                db.execute('ALTER TABLE credentials ADD COLUMN expires_on TEXT')
        if os.name == 'posix':
            self.db_path.chmod(0o600)

    def _store_valid(self, store_id: str, platform: str):
        with sqlite3.connect(self.store_db) as db:
            row = db.execute('SELECT platform, is_demo FROM stores WHERE id=?', (store_id,)).fetchone()
        if not row:
            raise SecurityError('Unknown store')
        if row[1]:
            raise SecurityError('Cannot attach real credentials to a demo store')
        if row[0] not in ('Both', platform):
            raise SecurityError('Provider does not match store')

    @staticmethod
    def _aad(cid: str, store: str, provider: str) -> bytes:
        return f'AI-Profit-Office|v1|{cid}|{store}|{provider}'.encode()

    def _audit(self, db, op: str, scope: str, target: str, reason: str):
        # Never put tokens/client IDs in this audit. Clamp reason length.
        clean = ' '.join(reason.split())[:160] or 'operator_action'
        db.execute('INSERT INTO security_audit(timestamp,operation,scope,target,reason) VALUES(?,?,?,?,?)',
                   (_now(), op, scope, target, clean))

    def add(self, store_id: str, platform: str, label: str, secret: str,
            client_id: str | None = None, expires_on: str | None = None) -> str:
        if platform not in PROVIDERS:
            raise SecurityError('Unsupported platform')
        if not secret or not secret.strip() or len(secret) > 16384:
            raise SecurityError('Invalid credential')
        if platform == 'Ozon' and not client_id:
            raise SecurityError('Ozon requires Client-Id')
        if len(label) > 80 or not label.strip():
            raise SecurityError('Label must be 1..80 characters')
        self._store_valid(store_id, platform)
        self._validate_expiry(expires_on)
        cid = str(uuid.uuid4())
        nonce = os.urandom(12)
        ciphertext = nonce + self._aead.encrypt(nonce, secret.encode(), self._aad(cid, store_id, platform))
        # Fingerprint is non-reversible and only used for deduplication/status.
        fingerprint = hashlib.sha256(ciphertext).hexdigest()[:12]
        with self._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO credentials(id,store_id,platform,label,client_id,ciphertext,fingerprint,created_at,updated_at,expires_on) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (cid, store_id, platform, label, client_id, ciphertext, fingerprint, _now(), _now(),expires_on))
            self._audit(db, 'credential_added', 'credential', cid, 'new_encrypted_credential')
            db.commit()
        return cid

    def list(self):
        with self._conn() as db:
            return [dict(r) for r in db.execute('SELECT id,store_id,platform,label,state,version,created_at,updated_at,expires_on FROM credentials ORDER BY created_at,id').fetchall()]

    def audit(self, limit: int = 30):
        with self._conn() as db:
            return [dict(r) for r in db.execute('SELECT * FROM security_audit ORDER BY id DESC LIMIT ?', (min(limit, 100),)).fetchall()]

    def _status(self, db, cid: str):
        row = db.execute('SELECT id,store_id,platform,label,client_id,ciphertext,state FROM credentials WHERE id=?', (cid,)).fetchone()
        if not row:
            raise SecurityError('Unknown credential')
        self._store_valid(row['store_id'], row['platform'])
        if row['state'] != 'active' or not row['ciphertext']:
            raise Stopped('Token is disabled or its key material was destroyed')
        expiry = db.execute('SELECT expires_on FROM credentials WHERE id=?', (cid,)).fetchone()[0]
        if expiry is not None and date.fromisoformat(expiry) < datetime.now(timezone.utc).date():
            raise Stopped('Token expired; rotate in provider dashboard')
        scopes = (('global','*'),('platform',row['platform']),('store',row['store_id']),('credential',cid))
        for scope, target in scopes:
            flag = db.execute('SELECT stopped FROM control WHERE scope=? AND target=?', (scope,target)).fetchone()
            if flag and flag['stopped']:
                raise Stopped(f'Blocked at {scope} level')
        return row

    def check(self, cid: str) -> bool:
        with self._conn() as db:
            self._status(db, cid)
        return True

    def with_authorized(self, cid: str, dispatcher: Callable[[Credential], T]) -> T:
        """Only approved outbound adapters should call this wrapper.

        Callback MUST avoid storing/logging the secret. In-flight requests cannot
        be cancelled retroactively by this local flag; platform revocation is needed.
        """
        with self._conn() as db:
            row = self._status(db, cid)
            aad = self._aad(cid, row['store_id'], row['platform'])
            nonce, ciphertext = row['ciphertext'][:12], row['ciphertext'][12:]
            secret = self._aead.decrypt(nonce, ciphertext, aad).decode()
            creds = Credential(platform=row['platform'], store_id=row['store_id'],
                               client_id=row['client_id'], secret=secret)
            self._status(db, cid)  # final check immediately before dispatch
        return dispatcher(creds)

    def set_stop(self, scope: str, target: str, stopped: bool, reason: str = 'operator_action'):
        if scope == 'global':
            if target != '*':
                raise SecurityError('Global target must be *')
        elif scope == 'platform':
            if target not in PROVIDERS:
                raise SecurityError('Unknown platform')
        elif scope == 'store':
            if not target:
                raise SecurityError('Store ID required')
        elif scope == 'credential':
            if not target:
                raise SecurityError('Credential ID required')
        else:
            raise SecurityError('Invalid scope')
        with self._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO control(scope,target,stopped,updated_at) VALUES(?,?,?,?) '
                       'ON CONFLICT(scope,target) DO UPDATE SET stopped=excluded.stopped,updated_at=excluded.updated_at',
                       (scope,target,int(stopped),_now()))
            if stopped:
                if scope == 'global':
                    db.execute('DELETE FROM live_permits')
                elif scope == 'platform':
                    db.execute('DELETE FROM live_permits WHERE platform=?',(target,))
                elif scope == 'store':
                    db.execute('DELETE FROM live_permits WHERE store_id=?',(target,))
                else:
                    db.execute('DELETE FROM live_permits WHERE credential_id=?',(target,))
            self._audit(db, 'stop' if stopped else 'resume', scope,target,reason)
            db.commit()

    def disabled_scopes(self):
        with self._conn() as db:
            return [dict(r) for r in db.execute('SELECT scope,target,stopped,updated_at FROM control WHERE stopped=1 ORDER BY scope,target')]

    def revoke_and_purge(self, cid: str, reason: str = 'suspected_exposure'):
        """Permanently stops this credential and clears the active encrypted bytes.
        Existing filesystem backups require separate deletion/rotation handling.
        """
        with self._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM credentials WHERE id=?', (cid,)).fetchone():
                raise SecurityError('Unknown credential')
            db.execute("UPDATE credentials SET state='revoked',ciphertext=NULL,version=version+1,updated_at=? WHERE id=?",(_now(),cid))
            db.execute('DELETE FROM live_permits WHERE credential_id=?',(cid,))
            self._audit(db,'revoke_purge','credential',cid,reason)
            db.commit()

    def rotate(self, cid: str, secret: str, client_id: str | None = None, expires_on: str | None = None):
        if not secret.strip():
            raise SecurityError('Empty secret')
        self._validate_expiry(expires_on)
        with self._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT id,store_id,platform,state,client_id FROM credentials WHERE id=?',(cid,)).fetchone()
            if not row or row['state']=='revoked':
                raise SecurityError('Cannot rotate unknown/revoked credential')
            self._store_valid(row['store_id'],row['platform'])
            new_client=client_id if client_id is not None else row['client_id']
            if row['platform']=='Ozon' and not new_client:
                raise SecurityError('Ozon Client-Id required')
            nonce=os.urandom(12)
            ct=nonce+self._aead.encrypt(nonce,secret.encode(),self._aad(cid,row['store_id'],row['platform']))
            db.execute('UPDATE credentials SET ciphertext=?,client_id=?,state=?,version=version+1,updated_at=?,expires_on=? WHERE id=?',
                       (ct,new_client,'active',_now(),expires_on,cid))
            db.execute('DELETE FROM live_permits WHERE credential_id=?',(cid,))
            self._audit(db,'rotate','credential',cid,'replaced_encrypted_material')
            db.commit()

    @staticmethod
    def _validate_expiry(expires_on: str | None) -> None:
        if expires_on is None:
            return
        if not isinstance(expires_on, str) or len(expires_on) != 10:
            raise SecurityError('Expiry must be YYYY-MM-DD')
        try:
            if date.fromisoformat(expires_on).isoformat() != expires_on:
                raise ValueError()
        except ValueError:
            raise SecurityError('Expiry must be YYYY-MM-DD') from None

    def set_expiry(self, cid: str, expires_on: str):
        """Local operator-entered expiry; does not query the provider."""
        self._validate_expiry(expires_on)
        if expires_on is None:
            raise SecurityError('Expiry cannot be blank')
        with self._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM credentials WHERE id=?',(cid,)).fetchone():
                raise SecurityError('Unknown credential')
            db.execute('UPDATE credentials SET expires_on=?, updated_at=? WHERE id=?',
                       (expires_on,_now(),cid))
            db.execute('DELETE FROM live_permits WHERE credential_id=?',(cid,))
            self._audit(db,'expiry_updated','credential',cid,'operator_entered_expiry')
            db.commit()

    def expiry_notifications(self):
        """Local metadata alerts: unknown Ozon keys and 30/14/7-day reminders."""
        today=datetime.now(timezone.utc).date()
        result=[]
        for row in self.list():
            raw=row.get('expires_on')
            if not raw:
                level='unknown' if row['platform']=='Ozon' else 'not_provided'
                days=None
            else:
                days=(date.fromisoformat(raw)-today).days
                level=('expired' if days < 0 else 'urgent_7d' if days <= 7 else
                       'warning_14d' if days <= 14 else 'notice_30d' if days <=30 else 'ok')
            result.append({'credential_id':row['id'],'platform':row['platform'],
                           'store_id':row['store_id'],'expires_on':raw,'days_left':days,'status':level})
        return result

    def is_credential_for(self, cid: str, store_id: str, provider: str) -> bool:
        with self._conn() as db:
            row=db.execute('SELECT store_id,platform FROM credentials WHERE id=?',(cid,)).fetchone()
            if not row:
                raise SecurityError('Unknown credential')
            return row['store_id']==store_id and row['platform']==provider

    def require_live_expiry(self, cid: str):
        """Ozon live keys require explicit expiry; offline code remains compatible."""
        with self._conn() as db:
            row=db.execute('SELECT platform,expires_on FROM credentials WHERE id=?',(cid,)).fetchone()
            if not row:
                raise SecurityError('Unknown credential')
            if row['platform']=='Ozon' and not row['expires_on']:
                raise SecurityError('Set an Ozon key expiration date before live requests')
        self.check(cid)

    def grant_live_permit(self, cid: str, store_id: str, provider: str,
                          path: str, *, confirmation: str, ttl_seconds: int = 600,
                          max_calls: int = 3):
        """One narrow, time-limited permit for a manual canary, no network call."""
        from .offline_sync import ALLOWED
        if confirmation != 'I_AUTHORIZE_READ_ONLY_CANARY':
            raise SecurityError('Explicit local operator confirmation required')
        if path not in ALLOWED.get(provider,set()):
            raise SecurityError('Endpoint is not approved')
        if not 1 <= ttl_seconds <= 1800 or not 1 <= max_calls <= 10:
            raise SecurityError('Permit limits exceeded')
        if not self.is_credential_for(cid,store_id,provider):
            raise SecurityError('Credential/store mismatch')
        self.require_live_expiry(cid)
        expiry=(datetime.now(timezone.utc)+timedelta(seconds=ttl_seconds)).isoformat()
        with self._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            self._status(db,cid)
            db.execute('INSERT INTO live_permits VALUES(?,?,?,?,?,?,0) ON CONFLICT(credential_id) '
                       'DO UPDATE SET store_id=excluded.store_id, platform=excluded.platform,'
                       'path=excluded.path,expires_at=excluded.expires_at,max_calls=excluded.max_calls, used=0',
                       (cid,store_id,provider,path,expiry,max_calls))
            self._audit(db,'live_permit_granted','credential',cid,'narrow_read_only_canary')
            db.commit()

    def consume_live_permit(self,cid: str,store_id: str,provider: str,path: str):
        """One atomic budget unit consumed for EVERY outbound HTTP attempt."""
        with self._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            self._status(db,cid)
            row=db.execute('SELECT * FROM live_permits WHERE credential_id=?',(cid,)).fetchone()
            valid=(row and row['store_id']==store_id and row['platform']==provider and
                   row['path']==path and row['used']<row['max_calls'] and
                   datetime.fromisoformat(row['expires_at'])>datetime.now(timezone.utc))
            if not valid:
                raise SecurityError('LIVE canary permit missing, expired or exhausted')
            db.execute('UPDATE live_permits SET used=used+1 WHERE credential_id=?',(cid,))
            self._audit(db,'live_attempt_permitted','credential',cid,'outbound_attempt_budget_consumed')
            db.commit()

    def disable_live_permit(self, cid: str):
        with self._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM live_permits WHERE credential_id=?',(cid,))
            self._audit(db,'live_permit_disarmed','credential',cid,'manual_disarm')
            db.commit()

    def live_permit_status(self):
        with self._conn() as db:
            rows=db.execute('SELECT credential_id,store_id,platform,path,expires_at,max_calls,used '
                            'FROM live_permits').fetchall()
            return [dict(r) for r in rows]
