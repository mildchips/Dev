"""Encrypted SQLite LAB backups. Not a backup of the old financial app or cloud PITR."""
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC=b'AIPO-LAB-BACKUP-1\n'
MAX_BYTES=64*1024*1024


def key(password, salt):
    if not 16 <= len(password) <= 256:
        raise ValueError('backup password must have 16..256 characters')
    return hashlib.scrypt(password.encode(),salt=salt,n=32768,r=8,p=1,maxmem=64*1024*1024,dklen=32)


def write_new(path, data):
    path=Path(path)
    # Exclusive creation also rejects dangling symlinks. Never replace existing data.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def backup(lab, destination, password):
    mem=sqlite3.connect(':memory:')
    src=sqlite3.connect(lab.path)
    try:
        src.backup(mem)
        # A backup from WAL inherits its header. Rebuild the in-memory snapshot
        # in MEMORY journal mode before serialize, so deserialize needs no WAL.
        mem.execute('PRAGMA journal_mode=MEMORY')
        mem.execute('VACUUM')
        payload=mem.serialize()
    finally:
        src.close()
        mem.close()
    if len(payload)>MAX_BYTES:
        raise ValueError('lab backup too large')
    salt,nonce=secrets.token_bytes(16),secrets.token_bytes(12)
    header=MAGIC+salt+nonce
    data=header+AESGCM(key(password,salt)).encrypt(nonce,payload,header)
    write_new(destination,data)
    return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}


def restore(source, destination, password):
    if Path(destination).exists() or Path(destination).is_symlink():
        raise FileExistsError('restore only to a new lab database path')
    source=Path(source)
    if source.stat().st_size>MAX_BYTES+256:
        raise ValueError('backup too large')
    data=source.read_bytes()
    n=len(MAGIC)
    if not data.startswith(MAGIC) or len(data)<n+44:
        raise ValueError('invalid backup format')
    salt,nonce=data[n:n+16],data[n+16:n+28]
    header=data[:n+28]
    payload=AESGCM(key(password,salt)).decrypt(nonce,data[n+28:],header)
    mem=sqlite3.connect(':memory:')
    try:
        mem.deserialize(payload)
        if mem.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('database integrity failed')
        if mem.execute('SELECT version FROM lab_meta').fetchall()!=[(1,)]:
            raise ValueError('unsupported lab version')
        if mem.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('database references failed')
        # Never resurrect pre-backup sessions, invitations or in-flight leases.
        mem.execute('DELETE FROM sessions')
        mem.execute('UPDATE invites SET used=1')
        # A snapshot may predate a membership revocation. Disable all delegates;
        # do not silently restore their old access after an owner resumes jobs.
        mem.execute("UPDATE members SET active=0 WHERE role!='owner'")
        mem.execute("UPDATE jobs SET state='PAUSED',lease=NULL WHERE state IN ('QUEUED','RUNNING')")
        mem.execute('UPDATE stops SET stopped=1')
        mem.commit()
        result=mem.serialize()
    finally:
        mem.close()
    write_new(destination,result)
    return {'integrity':'ok','sessions_revoked':True,'delegates_disabled':True,'all_tenants_stopped':True}
