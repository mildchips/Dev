import json
import warnings

import pytest

from saas_lab import __main__ as cli
from saas_lab.core import Lab

PASSWORD='Synthetic-cli-password-037!'


def inputs(monkeypatch,values):
    values=iter(values)
    monkeypatch.setattr(cli,'getpass',lambda prompt:next(values))


def test_mismatch_retries_then_creates_one_owner(tmp_path,monkeypatch,capsys):
    path=tmp_path/'lab.db'
    inputs(monkeypatch,[PASSWORD,PASSWORD+'wrong',PASSWORD,PASSWORD])
    assert cli.main(['--db',str(path),'user','synthetic-owner','--owner'])==0
    output=capsys.readouterr()
    assert 'Пароли не совпали' in output.err
    assert 'Пользователь создан' in output.err
    assert 'Traceback' not in output.err and PASSWORD not in output.err+output.out
    result=json.loads(output.out)
    lab=Lab(path)
    with lab.db() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM tenants').fetchone()[0]==1
    token,_=lab.login('synthetic-owner',PASSWORD)
    assert lab.tenants(lab.authenticate(token))[0]['tenant']==result['tenant_id']


@pytest.mark.parametrize('bad',['short','x'*257])
def test_password_length_retries_before_confirmation(tmp_path,monkeypatch,capsys,bad):
    inputs(monkeypatch,[bad,PASSWORD,PASSWORD])
    assert cli.main(['--db',str(tmp_path/'lab.db'),'user','synthetic-manager'])==0
    output=capsys.readouterr()
    assert 'от 12 до 256' in output.err
    assert json.loads(output.out)['tenant_id'] is None


@pytest.mark.parametrize('error',[KeyboardInterrupt,EOFError])
def test_cancel_confirmation_does_not_create_user(tmp_path,monkeypatch,capsys,error):
    path=tmp_path/'lab.db';calls=[0]
    def prompt(_):
        calls[0]+=1
        if calls[0]==1:return PASSWORD
        raise error()
    monkeypatch.setattr(cli,'getpass',prompt)
    assert cli.main(['--db',str(path),'user','synthetic-owner','--owner'])==130
    output=capsys.readouterr()
    assert 'Ввод отменён' in output.err and 'Traceback' not in output.err
    with Lab(path).db() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM tenants').fetchone()[0]==0


def test_duplicate_does_not_reset_password_or_create_owner(tmp_path,monkeypatch,capsys):
    path=tmp_path/'lab.db';lab=Lab(path)
    uid,_=lab.provision('synthetic-manager',PASSWORD)
    def forbidden(_):raise AssertionError('duplicate must be checked before password prompt')
    monkeypatch.setattr(cli,'getpass',forbidden)
    assert cli.main(['--db',str(path),'user','SYNTHETIC-MANAGER','--owner'])==2
    output=capsys.readouterr()
    assert 'уже существует' in output.err and 'Traceback' not in output.err
    token,_=lab.login('synthetic-manager',PASSWORD)
    assert lab.authenticate(token).user_id==uid
    assert lab.tenants(lab.authenticate(token))==[]


def test_no_echo_fallback_if_terminal_cannot_hide_input(tmp_path,monkeypatch,capsys):
    def insecure(_):
        warnings.warn('Cannot control echo',cli.GetPassWarning)
        raise AssertionError('must stop before fallback input')
    monkeypatch.setattr(cli,'getpass',insecure)
    assert cli.main(['--db',str(tmp_path/'lab.db'),'user','synthetic-owner','--owner'])==2
    assert 'Не удалось включить скрытый ввод' in capsys.readouterr().err


def test_invalid_login_no_prompt(tmp_path,monkeypatch,capsys):
    def forbidden(_):raise AssertionError('invalid login should not request a password')
    monkeypatch.setattr(cli,'getpass',forbidden)
    assert cli.main(['--db',str(tmp_path/'lab.db'),'user','x'])==2
    assert 'Логин должен содержать' in capsys.readouterr().err


def test_backup_mismatch_retries_and_restores(tmp_path,monkeypatch,capsys):
    path=tmp_path/'lab.db';destination=tmp_path/'backup.aipobak'
    lab=Lab(path);lab.provision('synthetic-owner',PASSWORD,organization=True)
    password='Synthetic backup password 037'
    inputs(monkeypatch,[password,password+'wrong','short',password,password])
    assert cli.main(['--db',str(path),'backup',str(destination)])==0
    out=capsys.readouterr()
    assert 'Пароли не совпали' in out.err and 'от 16 до 256' in out.err
    assert password not in out.err+out.out
    target=tmp_path/'restored.db'
    inputs(monkeypatch,[password])
    assert cli.main(['--db',str(target),'restore',str(destination)])==0
    assert json.loads(capsys.readouterr().out)['integrity']=='ok'
