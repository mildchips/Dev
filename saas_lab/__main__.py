import argparse
from getpass import getpass, GetPassWarning
import json
from pathlib import Path
import sqlite3
import sys
import warnings

from .core import Lab, Denied
from .recovery import backup, restore


def hidden_input(prompt):
    # Never fall back to an echoed password if the terminal cannot hide input.
    with warnings.catch_warnings():
        warnings.simplefilter('error', GetPassWarning)
        return getpass(prompt)


def new_password(prompt, minimum):
    print('При вводе пароль не отображается. Для отмены нажмите Ctrl+C.', file=sys.stderr)
    while True:
        password = hidden_input(prompt)
        if not minimum <= len(password) <= 256:
            print(f'Пароль должен содержать от {minimum} до 256 символов. Попробуйте снова.', file=sys.stderr)
            continue
        if password != hidden_input('Повторите пароль: '):
            print('Пароли не совпали. Введите пароль заново два раза.', file=sys.stderr)
            continue
        return password


def run(a):
    if a.command=='restore':
        print(json.dumps(restore(a.source,a.db,hidden_input('Пароль копии: '))))
        return 0
    Path(a.db).parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    lab=Lab(a.db)
    if a.command=='user':
        login=a.login.strip().lower()
        if not 3 <= len(login) <= 120:
            print('Логин должен содержать от 3 до 120 символов.', file=sys.stderr)
            return 2
        with lab.db() as db:
            exists=db.execute('SELECT 1 FROM users WHERE login=?',(login,)).fetchone()
        if exists:
            print('Пользователь с таким логином уже существует. Используйте его для входа; пароль и права не изменены.', file=sys.stderr)
            return 2
        password=new_password('Пароль лабораторного пользователя (12+): ',12)
        try:
            uid,tid=lab.provision(login,password,organization=a.owner)
        except sqlite3.IntegrityError:
            # Another process may create the same login after the precheck.
            print('Не удалось создать пользователя: логин уже занят или нарушена целостность базы. Существующая запись не изменена.', file=sys.stderr)
            return 2
        print('Пользователь создан. Теперь можно выполнять следующую команду.', file=sys.stderr)
        print(json.dumps({'user_id':uid,'tenant_id':tid}))
    elif a.command=='worker-once':
        claim=lab.claim()
        if claim:
            lab.finish(claim)
        print(json.dumps({'synthetic_job_completed':bool(claim)}))
    elif a.command=='backup':
        password=new_password('Новый пароль копии (16+): ',16)
        print(json.dumps(backup(lab,a.destination,password)))
    return 0


def main(argv=None):
    p=argparse.ArgumentParser(description='Synthetic local lab administration; no real seller data')
    p.add_argument('--db',default=str(Path.home()/'.aipo_saas_lab'/'lab.db'))
    sub=p.add_subparsers(dest='command',required=True)
    user=sub.add_parser('user'); user.add_argument('login'); user.add_argument('--owner',action='store_true')
    sub.add_parser('worker-once')
    b=sub.add_parser('backup'); b.add_argument('destination')
    r=sub.add_parser('restore'); r.add_argument('source')
    a=p.parse_args(argv)
    try:
        return run(a)
    except (KeyboardInterrupt, EOFError):
        print('\nВвод отменён. Если создание пользователя было прервано на вводе пароля, пользователь не создан.', file=sys.stderr)
        return 130
    except GetPassWarning:
        print('Не удалось включить скрытый ввод. Запустите команду в обычном Terminal, без перенаправления stdin.', file=sys.stderr)
        return 2
    except Denied:
        print('Операция отклонена. Проверьте параметры и состояние доступа.', file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
