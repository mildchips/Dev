"""Local manual canary CLI. SAFE DEFAULT: all commands are offline except `canary`.

A real canary requires explicit LIVE env + per-key, per-endpoint short-lived
permit + operator command. Never enter secrets in CLI flags/chat/logs.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from .integration_security import Vault, SecurityError, Stopped
from .live_connector import LiveGateway, ReadOnlyHTTPTransport, DurableQuota, live_enabled, LiveDisabled
from .offline_sync import CheckpointStore, OfflineSync, OZON_BY_DAY, WB_DETAILS, ContractError, ExternalAPIError


def parser():
    p=argparse.ArgumentParser(description='AI Profit Office v0.3.7 — guarded local READ ONLY canary')
    p.add_argument('--db', default=os.environ.get('AI_PROFIT_DB',str(Path.home()/'.ai_profit_office'/'prealpha.db')))
    p.add_argument('--vault', default=os.environ.get('AI_PROFIT_VAULT_DIR',str(Path.home()/'.ai_profit_office'/'vault')))
    p.add_argument('--staging', default=os.environ.get('AI_PROFIT_STAGING_DB',str(Path.home()/'.ai_profit_office'/'api_staging.sqlite3')))
    sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('status')
    sub.add_parser('expiries')
    q=sub.add_parser('preflight')
    q.add_argument('--credential',required=True)
    q.add_argument('--store',required=True)
    q.add_argument('--provider',required=True, choices=['WB','Ozon'])
    q=sub.add_parser('set-expiry')
    q.add_argument('--credential',required=True)
    q.add_argument('--date',required=True,help='YYYY-MM-DD, entered after checking marketplace dashboard')
    q=sub.add_parser('arm')
    q.add_argument('--credential',required=True)
    q.add_argument('--store',required=True)
    q.add_argument('--provider',required=True,choices=['WB','Ozon'])
    q.add_argument('--max-requests',type=int,default=2)
    q.add_argument('--minutes',type=int,default=10)
    q=sub.add_parser('disarm')
    q.add_argument('--credential',required=True)
    q=sub.add_parser('canary')
    q.add_argument('--credential',required=True)
    q.add_argument('--store',required=True)
    q.add_argument('--provider',required=True,choices=['WB','Ozon'])
    q.add_argument('--day',help='Ozon: one YYYY-MM-DD date only')
    q.add_argument('--from-date',help='WB: report start YYYY-MM-DD')
    q.add_argument('--to-date',help='WB: report end YYYY-MM-DD')
    q.add_argument('--max-pages',type=int,default=1,help='1–3 HTTP pages; may remain partial')
    return p


def _route(provider):
    return OZON_BY_DAY if provider=='Ozon' else WB_DETAILS


def run(argv=None):
    a=parser().parse_args(argv)
    try:
        vault=Vault(a.vault,a.db)
        if a.action=='status':
            print(json.dumps({'live_environment_enabled':live_enabled(),
                              'disabled_scopes':vault.disabled_scopes(),
                              'permits':vault.live_permit_status()},ensure_ascii=False,indent=2))
        elif a.action=='expiries':
            print(json.dumps(vault.expiry_notifications(),ensure_ascii=False,indent=2))
        elif a.action=='set-expiry':
            vault.set_expiry(a.credential,a.date)
            print('Updated local metadata only; no provider calls')
        elif a.action=='preflight':
            # OFFLINE check: never call a provider, even when LIVE env enabled.
            if not vault.is_credential_for(a.credential,a.store,a.provider):
                raise SecurityError('Credential does not match store/provider')
            vault.require_live_expiry(a.credential)
            print(json.dumps({'status':'OFFLINE_PREFLIGHT_PASS','network_used':False,
                              'provider':a.provider,'store':a.store,'endpoint':_route(a.provider),
                              'live_environment_enabled':live_enabled()},ensure_ascii=False))
        elif a.action=='arm':
            if not live_enabled():
                raise LiveDisabled('LIVE env flag is OFF; no authorization granted')
            print('WARNING: next canary CAN send requests to a real marketplace.')
            phrase=input('Type I_AUTHORIZE_READ_ONLY_CANARY to grant a short permit: ').strip()
            vault.grant_live_permit(a.credential,a.store,a.provider,_route(a.provider),
                                    confirmation=phrase, ttl_seconds=a.minutes*60,
                                    max_calls=a.max_requests)
            print('Narrow permit issued. No network call has been made.')
        elif a.action=='disarm':
            vault.disable_live_permit(a.credential)
            print('Permit disabled; no network call has been made.')
        elif a.action=='canary':
            if not live_enabled():
                raise LiveDisabled('LIVE env is OFF; no request attempted')
            if not 1 <= a.max_pages <= 3:
                raise SecurityError('Canary restricted to 1..3 pages')
            if a.provider=='Ozon' and (not a.day or a.from_date or a.to_date):
                raise SecurityError('Ozon requires --day only')
            if a.provider=='WB' and (not a.from_date or not a.to_date or a.day):
                raise SecurityError('WB requires --from-date and --to-date')
            transport=ReadOnlyHTTPTransport()
            try:
                quota=DurableQuota(vault.dir/'quota.sqlite3')
                gateway=LiveGateway(vault,transport,quota,attempts=2)
                sync=OfflineSync(vault,gateway,CheckpointStore(a.staging),max_pages=a.max_pages)
                if a.provider=='Ozon':
                    result=sync.ozon_day(a.credential,a.store,a.day)
                else:
                    result=sync.wb_period(a.credential,a.store,a.from_date,a.to_date)
                print(json.dumps({'status':result['status'],'events':result['events'],
                                  'note':'STAGING ONLY; not financial profit'},ensure_ascii=False))
            finally:
                transport.close()
        return 0
    except (SecurityError, ContractError, ExternalAPIError, sqlite3.Error, OSError, ValueError,
            TimeoutError) as exc:
        # Deliberately avoid printing transport/header/body/provider errors containing PII.
        print('SAFE ERROR:',str(exc))
        return 2


if __name__=='__main__':
    raise SystemExit(run())
