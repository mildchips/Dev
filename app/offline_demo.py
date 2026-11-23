"""Reproducible synthetic 1 Ozon + 2 WB smoke run. No user tokens/no network."""
import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

from .storage import initialize
from .integration_security import Vault, initialize_key, Stopped
from .offline_sync import (
    ScriptedTransport, MockResponse, Gateway, CheckpointStore, OfflineSync
)


def run():
    with tempfile.TemporaryDirectory(prefix='apo_offline_') as work:
        base=Path(work)
        stores=base/'app.db'
        initialize(stores)
        with sqlite3.connect(stores) as c:
            for sid,p in [('synthetic_wb_1','WB'),('synthetic_wb_2','WB'),('synthetic_ozon','Ozon')]:
                c.execute('INSERT INTO stores(id,name,platform) VALUES(?,?,?)',(sid,sid,p))
        initialize_key(base/'vault')
        vault=Vault(base/'vault',stores)
        registry={sid:vault.add(sid,p,'offline-fixture','NOT_A_REAL_KEY',client_id='000000' if p=='Ozon' else None)
                  for sid,p in [('synthetic_wb_1','WB'),('synthetic_wb_2','WB'),('synthetic_ozon','Ozon')]}
        stage=CheckpointStore(base/'staging.db')
        results={}
        for store in ('synthetic_wb_1','synthetic_wb_2'):
            mocked=ScriptedTransport([
                MockResponse(200,[{'rrdId':1,'reportId':123,'retailAmount':'150.00','currency':'RUB'}]),
                MockResponse(204,None)
            ])
            results[store]=OfflineSync(vault,Gateway(vault,mocked),stage).wb_period(registry[store],store,'2026-11-01','2026-11-07')
        store='synthetic_ozon'
        mocked=ScriptedTransport([
            MockResponse(200,{'accruals':[{'accrual_id':'ac1','accrued_category':'ITEM','accrued':'-15.00'}], 'last_id':'c1'}),
            MockResponse(200,{'accruals':[{'accrual_id':'ac2','accrued_category':'POSTING','accrued':'130.00'}], 'last_id':''})
        ])
        results[store]=OfflineSync(vault,Gateway(vault,mocked),stage).ozon_day(registry[store],store,'2026-11-01')
        vault.set_stop('global','*',True,'test_emergency_stop')
        try:
            vault.check(registry[store])
            raise AssertionError('Kill switch failed')
        except Stopped:
            results['kill_switch']='PASS'
        # Only print aggregate counts. No token text or user data.
        print(json.dumps({'mode':'OFFLINE MOCK ONLY', 'outbound_http_calls':0,'financial_profit_verified':False,
                          'results':results},ensure_ascii=False,indent=2))

if __name__=='__main__':
    run()
