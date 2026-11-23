"""Local-only administrative CLI: API keys are entered using a hidden prompt."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
from pathlib import Path

from .integration_security import Vault, initialize_key, SecurityError


def parser():
    p=argparse.ArgumentParser(description='AI Profit Office offline integrations control (NO network calls)')
    p.add_argument('--db',default=os.environ.get('AI_PROFIT_DB',str(Path.home()/'.ai_profit_office'/'prealpha.db')))
    p.add_argument('--vault',default=os.environ.get('AI_PROFIT_VAULT_DIR',str(Path.home()/'.ai_profit_office'/'vault')))
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init-key')
    sub.add_parser('stores')
    a=sub.add_parser('add'); a.add_argument('--store',required=True);a.add_argument('--provider',choices=['WB','Ozon'],required=True);a.add_argument('--label',required=True);a.add_argument('--client-id')
    sub.add_parser('list');sub.add_parser('status');sub.add_parser('audit')
    for name in ('stop','resume'):
        q=sub.add_parser(name); q.add_argument('--scope',choices=['global','platform','store','credential'],required=True)
        q.add_argument('--id',required=True,help='* for global, WB/Ozon for platform, or store/credential ID')
        q.add_argument('--reason',default='manual_operator_action',help='no secrets or personal data')
    q=sub.add_parser('revoke');q.add_argument('--id',required=True);q.add_argument('--reason',default='manual_security_revocation')
    q=sub.add_parser('rotate');q.add_argument('--id',required=True);q.add_argument('--client-id')
    q=sub.add_parser('check');q.add_argument('--id',required=True)
    return p


def run(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.cmd=='init-key':
            path=initialize_key(Path(args.vault))
            print('Encrypted local vault initialized:',path.parent)
            return 0
        if args.cmd=='stores':
            with sqlite3.connect(args.db) as db:
                for rec in db.execute('SELECT id,name,platform,is_demo FROM stores'):
                    print({'id':rec[0],'name':rec[1],'platform':rec[2],'is_demo':bool(rec[3])})
            return 0
        v=Vault(args.vault,args.db)
        if args.cmd=='add':
            key=getpass.getpass('Paste API key (hidden; not saved to shell history): ')
            client_id=args.client_id
            if args.provider=='Ozon' and not client_id:
                client_id=input('Ozon Client-Id (non-secret): ').strip()
            token_id=v.add(args.store,args.provider,args.label,key,client_id)
            print('Saved encrypted credential ID:',token_id,'(not connected; no API calls)')
        elif args.cmd=='list':
            print(json.dumps(v.list(),ensure_ascii=False,indent=2))
        elif args.cmd=='status':
            print(json.dumps({'disabled':v.disabled_scopes(),'credentials':v.list()},ensure_ascii=False,indent=2))
        elif args.cmd=='audit':
            print(json.dumps(v.audit(),ensure_ascii=False,indent=2))
        elif args.cmd in ('stop','resume'):
            v.set_stop(args.scope,args.id,args.cmd=='stop',args.reason)
            print('Applied',args.cmd,args.scope,args.id)
        elif args.cmd=='revoke':
            v.revoke_and_purge(args.id,args.reason)
            print('Credential encrypted bytes purged locally; revoke in provider dashboard too:',args.id)
        elif args.cmd=='rotate':
            key=getpass.getpass('Paste replacement key (hidden): ')
            v.rotate(args.id,key,args.client_id)
            print('Credential rotated locally. Revoke previous key at marketplace dashboard.')
        elif args.cmd=='check':
            v.check(args.id)
            print('ALLOWED (local policy only; no network request)')
        return 0
    except (SecurityError, sqlite3.Error, OSError) as exc:
        print('ERROR:',str(exc))
        return 2


if __name__=='__main__':
    raise SystemExit(run())
