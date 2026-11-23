import os
import sqlite3
from pathlib import Path

import pytest

from app.storage import initialize
from app.integration_security import Vault, SecurityError, Stopped, initialize_key


@pytest.fixture
def vault(tmp_path):
    db_path = tmp_path/'app.sqlite3'
    initialize(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute('INSERT INTO stores(id,name,platform) VALUES (?,?,?)', ('wb1','Seller WB','WB'))
        db.execute('INSERT INTO stores(id,name,platform) VALUES (?,?,?)', ('wb2','Seller WB #2','WB'))
        db.execute('INSERT INTO stores(id,name,platform) VALUES (?,?,?)', ('oz1','Seller Ozon','Ozon'))
        db.execute('INSERT INTO stores(id,name,platform,is_demo) VALUES (?,?,?,1)', ('demo','DEMO','WB'))
    folder=tmp_path/'vault'
    initialize_key(folder)
    return Vault(folder,db_path)


def test_multiple_stores_and_provider_credentials(vault):
    c1=vault.add('wb1','WB','first','WB_SUPER_SECRET_ABC')
    c2=vault.add('wb2','WB','second','WB_SUPER_SECRET_DEF')
    c3=vault.add('oz1','Ozon','third','OZON_SUPER_SECRET',client_id='123456')
    assert len({c1,c2,c3}) == 3
    assert vault.with_authorized(c1,lambda credential:credential.secret)=='WB_SUPER_SECRET_ABC'
    assert vault.with_authorized(c3,lambda credential:(credential.platform,credential.client_id))==('Ozon','123456')
    rows=vault.list()
    assert not any('SECRET' in str(r) for r in rows)
    assert 'WB_SUPER_SECRET_ABC'.encode() not in vault.db_path.read_bytes()
    assert 'OZON_SUPER_SECRET'.encode() not in vault.db_path.read_bytes()
    assert 'SECRET' not in str(vault.audit())


def test_stores_enforced(vault):
    with pytest.raises(SecurityError): vault.add('fake','WB','label','password')
    with pytest.raises(SecurityError): vault.add('demo','WB','label','password')
    with pytest.raises(SecurityError): vault.add('oz1','WB','label','password')
    with pytest.raises(SecurityError): vault.add('oz1','Ozon','label','password')


def test_emergency_global_shutdown_and_resume(vault):
    key = vault.add('wb1','WB','wb','key-value')
    vault.set_stop('global','*',True,'emergency')
    with pytest.raises(Stopped,match='global'):
        vault.with_authorized(key,lambda secret:None)
    vault.set_stop('global','*',False,'resolved')
    assert vault.check(key)


def test_platform_store_and_token_scopes(vault):
    a=vault.add('wb1','WB','wb1','A')
    b=vault.add('wb2','WB','wb2','B')
    c=vault.add('oz1','Ozon','oz','C',client_id='44')
    vault.set_stop('store','wb1',True)
    with pytest.raises(Stopped): vault.check(a)
    assert vault.check(b)
    assert vault.check(c)
    vault.set_stop('store','wb1',False)
    vault.set_stop('platform','WB',True)
    for k in (a,b):
        with pytest.raises(Stopped): vault.check(k)
    assert vault.check(c)
    vault.set_stop('platform','WB',False)
    vault.set_stop('credential',a,True)
    with pytest.raises(Stopped): vault.check(a)
    assert vault.check(b)


def test_revoke_cannot_be_reenabled(vault):
    a=vault.add('wb1','WB','wb','SECRET')
    vault.revoke_and_purge(a)
    vault.set_stop('credential',a,False)
    with pytest.raises(Stopped): vault.check(a)
    with pytest.raises(SecurityError): vault.rotate(a,'NEW')
    with sqlite3.connect(vault.db_path) as db:
        assert db.execute('SELECT ciphertext FROM credentials WHERE id=?',(a,)).fetchone()[0] is None


def test_rotate_changes_secret_and_protects_aad(vault):
    a=vault.add('wb1','WB','wb','OLD')
    vault.rotate(a,'NEW')
    assert vault.with_authorized(a,lambda c:c.secret)=='NEW'
    assert 'OLD'.encode() not in vault.db_path.read_bytes()
    with sqlite3.connect(vault.db_path) as db:
        db.execute("UPDATE credentials SET store_id='wb2' WHERE id=?",(a,))
    with pytest.raises(Exception): vault.with_authorized(a,lambda c:c.secret)


def test_permissions_and_repeat_init(vault):
    with pytest.raises(SecurityError): initialize_key(vault.dir)
    if os.name=='posix':
        vault.key_path.chmod(0o644)
        with pytest.raises(SecurityError): Vault(vault.dir,vault.store_db)
        vault.key_path.chmod(0o600)


def test_invalid_scopes(vault):
    with pytest.raises(SecurityError): vault.set_stop('global','wrong',True)
    with pytest.raises(SecurityError): vault.set_stop('platform','UNKNOWN',True)
    with pytest.raises(SecurityError): vault.set_stop('nonsense','*',True)


def test_no_outbound_calls_during_stop(vault):
    key=vault.add('wb1','WB','wb','secret')
    called=[]
    def dispatch(c):
        called.append(c.platform)
    vault.set_stop('global','*',True)
    with pytest.raises(Stopped):
        vault.with_authorized(key,dispatch)
    assert called==[]


def test_encrypted_key_different_credential_binding(vault):
    a=vault.add('wb1','WB','wb1','SAME')
    b=vault.add('wb2','WB','wb2','SAME')
    with sqlite3.connect(vault.db_path) as db:
        cta=db.execute('SELECT ciphertext FROM credentials WHERE id=?',(a,)).fetchone()[0]
        ctb=db.execute('SELECT ciphertext FROM credentials WHERE id=?',(b,)).fetchone()[0]
    assert cta != ctb
