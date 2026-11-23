"""SQLite security reference, isolated from ~/.ai_profit_office and marketplace IO.

The only job kind is synthetic_audit. No tokens, financial reports or external
connections are accepted. Database-file access is outside this lab's boundary.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class Denied(ValueError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)


@dataclass(frozen=True)
class Actor:
    user_id: str
    session_hash: str


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS lab_meta (version INTEGER NOT NULL);
INSERT INTO lab_meta SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM lab_meta);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, login TEXT NOT NULL UNIQUE, salt BLOB NOT NULL,
 password BLOB NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
 locked_until REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id));
CREATE TABLE IF NOT EXISTS members (
 tenant TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('owner','manager','auditor')),
 stores TEXT NOT NULL, active INTEGER NOT NULL, expires REAL NOT NULL,
 PRIMARY KEY(tenant,user_id));
CREATE TABLE IF NOT EXISTS stores (
 tenant TEXT NOT NULL REFERENCES tenants(id), id TEXT NOT NULL, label TEXT NOT NULL,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS sessions (
 hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invites (
 hash TEXT PRIMARY KEY, tenant TEXT NOT NULL, login TEXT NOT NULL,
 role TEXT NOT NULL, stores TEXT NOT NULL, expires REAL NOT NULL,
 used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stops (
 tenant TEXT PRIMARY KEY REFERENCES tenants(id), stopped INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 tenant TEXT NOT NULL, id TEXT NOT NULL, store TEXT NOT NULL,
 requested_by TEXT NOT NULL REFERENCES users(id), idem TEXT NOT NULL,
 state TEXT NOT NULL, attempts INTEGER NOT NULL, available REAL NOT NULL,
 lease TEXT, lease_until REAL, result TEXT,
 PRIMARY KEY(tenant,id), UNIQUE(tenant,store,idem),
 FOREIGN KEY(tenant,store) REFERENCES stores(tenant,id));
CREATE TABLE IF NOT EXISTS audit (
 seq INTEGER PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
 event TEXT NOT NULL, object_id TEXT NOT NULL, at REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
 BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
 BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


class Lab:
    def __init__(self, path: Path | str, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise Denied('symlink database refused')
        with self.db() as db:
            db.executescript(SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=15000')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _audit(self, db, tenant, actor, event, object_id):
        db.execute('INSERT INTO audit(tenant,actor,event,object_id,at) VALUES(?,?,?,?,?)',
                   (tenant, actor, event, object_id, self.clock()))

    def provision(self, login: str, password: str, *, organization=False):
        """Local operator CLI only. No public signup/bootstrap endpoint."""
        login = login.strip().lower()
        if not 3 <= len(login) <= 120 or not 12 <= len(password) <= 256:
            raise Denied('login/password length invalid')
        uid, tid, salt = uuid.uuid4().hex, uuid.uuid4().hex, secrets.token_bytes(16)
        hashed = password_hash(password, salt)
        with self.db() as db:
            db.execute('INSERT INTO users(id,login,salt,password) VALUES(?,?,?,?)', (uid,login,salt,hashed))
            if organization:
                db.execute('INSERT INTO tenants VALUES(?,?)', (tid,uid))
                db.execute('INSERT INTO members VALUES(?,?,?,?,1,?)', (tid,uid,'owner','[]',self.clock()+31536000))
                db.execute('INSERT INTO stops VALUES(?,0)', (tid,))
                self._audit(db,tid,uid,'tenant_created',tid)
        return uid, tid if organization else None

    def login(self, login: str, password: str):
        if len(login) > 120 or len(password) > 256:
            raise Denied('invalid credentials')
        # Persist failed attempts by committing before raising.
        result = None
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE login=?',(login.strip().lower(),)).fetchone()
            salt = row['salt'] if row else bytes(16)
            matched = hmac.compare_digest(password_hash(password,salt),row['password'] if row else bytes(64))
            if row and row['locked_until'] <= self.clock():
                if matched:
                    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    db.execute('UPDATE users SET failures=0,locked_until=0 WHERE id=?',(row['id'],))
                    db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),row['id'],csrf,self.clock()+1800))
                    result = (token,csrf)
                else:
                    failures=row['failures']+1
                    db.execute('UPDATE users SET failures=?,locked_until=? WHERE id=?',
                               (failures,self.clock()+300 if failures>=5 else 0,row['id']))
        if not result:
            raise Denied('invalid credentials or locked')
        return result

    def authenticate(self, token: str, csrf: str | None = None, *, mutation=False):
        with self.db() as db:
            r=db.execute('SELECT * FROM sessions WHERE hash=? AND expires>?',(digest(token),self.clock())).fetchone()
            if not r or (mutation and not hmac.compare_digest(r['csrf'],csrf or '')):
                raise Denied('session or CSRF invalid')
            return Actor(r['user_id'],r['hash'])

    def _session(self, db, actor):
        r=db.execute('SELECT 1 FROM sessions WHERE hash=? AND user_id=? AND expires>?',
                     (actor.session_hash,actor.user_id,self.clock())).fetchone()
        if not r:
            raise Denied('session expired or revoked')

    def logout(self, actor):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE hash=?',(actor.session_hash,))

    def _member(self, db, user, tenant, store=None, *, write=False, owner=False):
        m=db.execute('SELECT * FROM members WHERE tenant=? AND user_id=? AND active=1 AND expires>?',
                     (tenant,user,self.clock())).fetchone()
        if not m or (owner and m['role']!='owner') or (write and m['role']=='auditor'):
            raise Denied('resource unavailable')
        if store is not None:
            exists=db.execute('SELECT 1 FROM stores WHERE tenant=? AND id=?',(tenant,store)).fetchone()
            if not exists or (m['role']!='owner' and store not in json.loads(m['stores'])):
                raise Denied('resource unavailable')
        return m

    def _allowed(self, db, actor, tenant, store=None, **kw):
        self._session(db,actor)
        return self._member(db,actor.user_id,tenant,store,**kw)

    def tenants(self, actor):
        with self.db() as db:
            self._session(db,actor)
            return [dict(r) for r in db.execute('SELECT tenant,role,stores,expires FROM members WHERE user_id=? AND active=1 AND expires>?',
                                              (actor.user_id,self.clock()))]

    def add_store(self, actor, tenant, label):
        if not 1 <= len(label) <= 80:
            raise Denied('label length invalid')
        sid=uuid.uuid4().hex
        with self.db() as db:
            self._allowed(db,actor,tenant,owner=True)
            db.execute('INSERT INTO stores VALUES(?,?,?)',(tenant,sid,label))
            self._audit(db,tenant,actor.user_id,'store_created',sid)
        return sid

    def stores(self, actor, tenant):
        with self.db() as db:
            m=self._allowed(db,actor,tenant)
            scope=json.loads(m['stores'])
            return [dict(r) for r in db.execute('SELECT id,label FROM stores WHERE tenant=?',(tenant,))
                    if m['role']=='owner' or r['id'] in scope]

    def invite(self, actor, tenant, login, role, stores, ttl=3600):
        if role not in ('manager','auditor') or not 60 <= ttl <= 86400 or not stores:
            raise Denied('invalid invitation')
        token=secrets.token_urlsafe(32)
        with self.db() as db:
            self._allowed(db,actor,tenant,owner=True)
            for s in stores:
                self._member(db,actor.user_id,tenant,s)
            db.execute('INSERT INTO invites(hash,tenant,login,role,stores,expires) VALUES(?,?,?,?,?,?)',
                       (digest(token),tenant,login.strip().lower(),role,json.dumps(sorted(set(stores))),self.clock()+ttl))
            self._audit(db,tenant,actor.user_id,'invite_created',digest(token))
        return token

    def accept(self, actor, token):
        with self.db() as db:
            self._session(db,actor)
            i=db.execute('SELECT * FROM invites WHERE hash=? AND used=0 AND expires>?',(digest(token),self.clock())).fetchone()
            u=db.execute('SELECT login FROM users WHERE id=?',(actor.user_id,)).fetchone()
            if not i or i['login']!=u['login']:
                raise Denied('invitation unavailable')
            # Existing membership cannot be overwritten, including a revoked one.
            if db.execute('SELECT 1 FROM members WHERE tenant=? AND user_id=?',(i['tenant'],actor.user_id)).fetchone():
                raise Denied('membership already exists; operator review required')
            db.execute('INSERT INTO members VALUES(?,?,?,?,1,?)',
                       (i['tenant'],actor.user_id,i['role'],i['stores'],self.clock()+86400))
            db.execute('UPDATE invites SET used=1 WHERE hash=?',(digest(token),))
            self._audit(db,i['tenant'],actor.user_id,'invite_accepted',actor.user_id)
            return i['tenant']

    def revoke(self, actor, tenant, user):
        with self.db() as db:
            self._allowed(db,actor,tenant,owner=True)
            m=db.execute('SELECT role FROM members WHERE tenant=? AND user_id=?',(tenant,user)).fetchone()
            if not m or m['role']=='owner':
                raise Denied('cannot revoke owner or unknown member')
            db.execute('UPDATE members SET active=0 WHERE tenant=? AND user_id=?',(tenant,user))
            db.execute("UPDATE jobs SET state='CANCELLED',lease=NULL WHERE tenant=? AND requested_by=? AND state IN ('QUEUED','RUNNING')",(tenant,user))
            self._audit(db,tenant,actor.user_id,'member_revoked',user)

    def stop(self, actor, tenant, stopped=True):
        with self.db() as db:
            self._allowed(db,actor,tenant,owner=True)
            db.execute('UPDATE stops SET stopped=? WHERE tenant=?',(int(stopped),tenant))
            if stopped:
                db.execute("UPDATE jobs SET state='PAUSED',lease=NULL WHERE tenant=? AND state IN ('QUEUED','RUNNING')",(tenant,))
            else:
                db.execute("UPDATE jobs SET state='QUEUED',available=? WHERE tenant=? AND state='PAUSED'",(self.clock(),tenant))
            self._audit(db,tenant,actor.user_id,'stop' if stopped else 'resume',tenant)

    def enqueue(self, actor, tenant, store, idem):
        if not 1 <= len(idem) <= 120:
            raise Denied('idempotency key length invalid')
        with self.db() as db:
            self._allowed(db,actor,tenant,store,write=True)
            if db.execute('SELECT stopped FROM stops WHERE tenant=?',(tenant,)).fetchone()[0]:
                raise Denied('tenant stopped')
            r=db.execute('SELECT id,requested_by FROM jobs WHERE tenant=? AND store=? AND idem=?',(tenant,store,idem)).fetchone()
            if r:
                if r['requested_by']!=actor.user_id:
                    raise Denied('idempotency key owned by another member')
                return r['id']
            jid=uuid.uuid4().hex
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,'QUEUED',0,?,NULL,NULL,NULL)",
                       (tenant,jid,store,actor.user_id,idem,self.clock()))
            self._audit(db,tenant,actor.user_id,'job_queued',jid)
            return jid

    def claim(self):
        """Internal worker method, never exposed as an HTTP endpoint."""
        with self.db() as db:
            db.execute("UPDATE jobs SET state=CASE WHEN attempts>=3 THEN 'DEAD' ELSE 'QUEUED' END,lease=NULL WHERE state='RUNNING' AND lease_until<=?",(self.clock(),))
            db.execute("UPDATE jobs SET state='DEAD',lease=NULL WHERE state='QUEUED' AND attempts>=3")
            for r in db.execute("SELECT * FROM jobs WHERE state='QUEUED' AND available<=? ORDER BY available,id",(self.clock(),)).fetchall():
                try:
                    self._member(db,r['requested_by'],r['tenant'],r['store'],write=True)
                except Denied:
                    db.execute("UPDATE jobs SET state='CANCELLED' WHERE tenant=? AND id=?",(r['tenant'],r['id']))
                    continue
                if db.execute('SELECT stopped FROM stops WHERE tenant=?',(r['tenant'],)).fetchone()[0]:
                    db.execute("UPDATE jobs SET state='PAUSED' WHERE tenant=? AND id=?",(r['tenant'],r['id']))
                    continue
                lease=secrets.token_hex(24)
                db.execute("UPDATE jobs SET state='RUNNING',attempts=attempts+1,lease=?,lease_until=? WHERE tenant=? AND id=?",
                           (lease,self.clock()+30,r['tenant'],r['id']))
                return {'tenant':r['tenant'],'id':r['id'],'lease':lease}
        return None

    def finish(self, claim, *, success=True):
        with self.db() as db:
            r=db.execute("SELECT * FROM jobs WHERE tenant=? AND id=? AND state='RUNNING' AND lease=? AND lease_until>?",
                         (claim['tenant'],claim['id'],claim['lease'],self.clock())).fetchone()
            if not r:
                raise Denied('stale lease')
            self._member(db,r['requested_by'],r['tenant'],r['store'],write=True)
            if db.execute('SELECT stopped FROM stops WHERE tenant=?',(r['tenant'],)).fetchone()[0]:
                raise Denied('tenant stopped')
            state='DONE' if success else ('DEAD' if r['attempts']>=3 else 'QUEUED')
            result=json.dumps({'kind':'synthetic_audit','verified_profit':False}) if success else None
            db.execute('UPDATE jobs SET state=?,result=?,available=?,lease=NULL WHERE tenant=? AND id=?',
                       (state,result,self.clock()+2**r['attempts'],r['tenant'],r['id']))
            self._audit(db,r['tenant'],r['requested_by'],'job_'+state.lower(),r['id'])

    def job(self, actor, tenant, jid):
        with self.db() as db:
            self._allowed(db,actor,tenant)
            r=db.execute('SELECT * FROM jobs WHERE tenant=? AND id=?',(tenant,jid)).fetchone()
            if not r:
                raise Denied('resource unavailable')
            self._member(db,actor.user_id,tenant,r['store'])
            return {k:r[k] for k in ('id','store','state','attempts','result')}

    def audit(self, actor, tenant):
        with self.db() as db:
            self._allowed(db,actor,tenant,owner=True)
            return [dict(r) for r in db.execute('SELECT seq,actor,event,object_id,at FROM audit WHERE tenant=? ORDER BY seq',(tenant,))]
