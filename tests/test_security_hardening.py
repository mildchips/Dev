"""Explicit regression coverage for local access boundary and security claims."""
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.server import create_app
from app.local_auth import set_password
from app.storage import initialize

PASS='secret-long-passphrase-1234'

@pytest.fixture
def app_db(tmp_path):
    db=tmp_path/'workspace.sqlite3'
    app=create_app(db,test_mode=True)
    return app,db

def login(client):
    response=client.post('/api/auth/login',json={'passphrase':PASS})
    assert response.status_code==200,response.text
    client.headers['X-AIPO-CSRF']=response.json()['csrf']
    return response

def test_fail_closed_when_unconfigured(app_db):
    app,db=app_db
    with TestClient(app) as c:
        assert c.get('/api/health').status_code==200
        assert c.get('/api/stores').status_code==423
        assert c.post('/api/stores',json={'name':'Store','platform':'WB'}).status_code==423
        assert c.get('/api/auth/status').json()['configured'] is False

def test_nobody_sees_financial_data_without_login(app_db):
    app,db=app_db;set_password(db,PASS)
    with TestClient(app) as c:
        assert c.get('/api/stores').status_code==401
        assert c.get('/api/stores/someone/dashboard').status_code==401
        assert c.delete('/api/stores/someone').status_code==401
        assert c.get('/api/auth/status').json()['authenticated'] is False

def test_login_session_csrf_and_logout(app_db):
    app,db=app_db;set_password(db,PASS)
    with TestClient(app) as c:
        response=login(c)
        assert 'httponly' in response.headers['set-cookie'].lower()
        assert 'samesite=strict' in response.headers['set-cookie'].lower()
        assert c.get('/api/stores').status_code==200
        c.headers.pop('X-AIPO-CSRF')
        assert c.post('/api/stores',json={'name':'Test','platform':'WB'}).status_code==403
        c.headers['X-AIPO-CSRF']='invalid-csrf'
        assert c.post('/api/stores',json={'name':'Test','platform':'WB'}).status_code==403
        c.headers['X-AIPO-CSRF']=c.get('/api/auth/status').json()['csrf']
        assert c.post('/api/stores',json={'name':'Test','platform':'WB'}).status_code==201
        assert c.post('/api/auth/logout').status_code==200
        assert c.get('/api/stores').status_code==401

def test_forbid_cross_site_origin_even_after_login(app_db):
    app,db=app_db;set_password(db,PASS)
    with TestClient(app) as c:
        login(c)
        assert c.post('/api/stores',headers={'Origin':'https://malicious.example'},json={'name':'Evil','platform':'WB'}).status_code==403
        assert c.get('/api/stores',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403

def test_reject_unexpected_host(app_db):
    app,db=app_db;set_password(db,PASS)
    with TestClient(app) as c:
        assert c.get('/api/health',headers={'Host':'attacker.example'}).status_code==400

def test_auth_rotation_retires_session(app_db):
    app,db=app_db;set_password(db,PASS)
    with TestClient(app) as c:
        login(c)
        set_password(db,'another-very-long-passphrase',allow_reset=True)
        assert c.get('/api/stores').status_code==401
        assert c.post('/api/auth/login',json={'passphrase':PASS}).status_code==401

def test_brute_force_is_rate_limited(app_db):
    app,db=app_db;set_password(db,PASS)
    with TestClient(app) as c:
        for _ in range(5):
            assert c.post('/api/auth/login',json={'passphrase':'wrong'}).status_code==401
        assert c.post('/api/auth/login',json={'passphrase':PASS}).status_code==401

def test_local_database_restricted_file_permissions(app_db):
    import os
    app,db=app_db
    if os.name=='posix':
        assert db.stat().st_mode & 0o077 == 0
        assert db.parent.stat().st_mode & 0o077 == 0

def test_no_docs_exposed(app_db):
    app,db=app_db;set_password(db,PASS)
    with TestClient(app) as c:
        login(c)
        assert c.get('/api/openapi.json').status_code==404
        assert c.get('/api/docs').status_code==404
