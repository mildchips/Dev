"""Read-only recommendations + local data management. No marketplace live API or LLM in v0.3."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Query, Request
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
import hmac
from . import local_auth
from .ru_guardrails import is_loopback_peer, runtime_posture

from .ingest import parse_ledger, parse_money, parse_date, IngestError, MAX_FILE_BYTES
from .engine import analyze, summary, money, VERSION
from .scenarios import ScenarioRequest, compare
from .storage import initialize, connect, log_action

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path.home()/'.ai_profit_office'/'prealpha.db'

class StoreCreate(BaseModel):
    name:str=Field(min_length=2,max_length=80)
    platform:Literal['WB','Ozon']='WB'

class CostUpdate(BaseModel):
    sku:str=Field(min_length=1,max_length=120)
    unit_cost_rub:str=Field(min_length=1,max_length=30)

class ActionCreate(BaseModel):
    hypothesis: str = Field(min_length=8,max_length=500)
    source_signal_key: str = Field(default='',max_length=200)

class ActionUpdate(BaseModel):
    status: Literal['draft','planned','implemented','rejected']

class StoreDeleteConfirm(BaseModel):
    confirm_name: str = Field(min_length=1,max_length=80)

class CostRevisionUpdate(BaseModel):
    sku: str = Field(min_length=1,max_length=120)
    effective_from: str = Field(min_length=8,max_length=10)
    unit_cost_rub: str = Field(min_length=1,max_length=30)

class ReviewUpdate(BaseModel):
    status:Literal['detected','reviewing','confirmed','dismissed']

class LoginPayload(BaseModel):
    passphrase: str = Field(min_length=1,max_length=1024)

def create_app(db_path:Path|str|None=None, *, test_mode:bool=False) -> FastAPI:
    db_path=Path(db_path or os.environ.get('AI_PROFIT_DB', DEFAULT_DB))
    initialize(db_path)
    local_auth.init_schema(db_path)
    from .finance import Finance, browser_json
    from .offline_sync import ContractError
    finance = Finance(db_path)
    app=FastAPI(title='AI Profit Office Local Audit',version='0.3.7',docs_url=None,redoc_url=None,openapi_url=None)
    app.mount('/assets',StaticFiles(directory=str(ROOT/'web')),name='assets')
    # This remains a single-operator localhost application. test_mode only permits
    # TestClient's synthetic Host; never use it in network deployment.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1','localhost'] + (['testserver'] if test_mode else []))

    @app.middleware('http')
    async def local_security(request: Request, call_next):
        path = request.url.path
        # This release is strictly LOCAL-ONLY. TrustedHost checks Host headers
        # but does not prove the actual peer is local. Enforce the peer as well.
        # This is NOT a reverse-proxy-compatible public deployment mode.
        if not test_mode and not is_loopback_peer(request.client.host if request.client else None):
            return JSONResponse({'detail':'Local-only service: remote network access denied'},status_code=403)
        if path.startswith('/api/'):
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail':'Cross-site requests are forbidden'},status_code=403)
            if request.method not in ('GET','HEAD','OPTIONS'):
                origin = request.headers.get('origin')
                if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                    return JSONResponse({'detail':'Invalid request origin'},status_code=403)
            anonymous = path in ('/api/health','/api/auth/status','/api/auth/login')
            if not anonymous:
                if not local_auth.configured(db_path):
                    return JSONResponse({'detail':'Configure local passphrase with python -m app.local_auth init-password'},status_code=423)
                csrf=local_auth.verify_session(db_path,request.cookies.get(local_auth.COOKIE_NAME))
                if not csrf:
                    return JSONResponse({'detail':'Please sign in to local workspace'},status_code=401)
                if request.method not in ('GET','HEAD','OPTIONS') and not hmac.compare_digest(request.headers.get('x-aipo-csrf',''),csrf):
                    return JSONResponse({'detail':'Missing or invalid CSRF token'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Cache-Control']='no-store'
        response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
        return response

    @app.get('/api/auth/status')
    def auth_status(request: Request):
        ready=local_auth.configured(db_path)
        csrf=local_auth.verify_session(db_path,request.cookies.get(local_auth.COOKIE_NAME)) if ready else None
        return {'configured':ready,'authenticated':bool(csrf),'csrf':csrf}

    @app.post('/api/auth/login')
    def local_login(payload:LoginPayload):
        if not local_auth.configured(db_path):
            raise HTTPException(423,'Configure local passphrase via CLI')
        pair=local_auth.login(db_path,payload.passphrase)
        if not pair:
            raise HTTPException(401,'Invalid passphrase or temporarily locked')
        token,csrf=pair
        response=JSONResponse({'authenticated':True,'csrf':csrf})
        response.set_cookie(local_auth.COOKIE_NAME,token,httponly=True,samesite='strict',secure=False,path='/',max_age=local_auth.SESSION_SECONDS)
        return response

    @app.post('/api/auth/logout')
    def local_logout(request:Request):
        local_auth.logout(db_path,request.cookies.get(local_auth.COOKIE_NAME))
        response=JSONResponse({'authenticated':False})
        response.delete_cookie(local_auth.COOKIE_NAME,path='/')
        return response


    def store_record(store_id:str,db):
        r=db.execute('SELECT * FROM stores WHERE id=?',(store_id,)).fetchone()
        if not r:
            raise HTTPException(404,'Магазин не найден')
        return dict(r)

    @app.get('/')
    def index():
        return FileResponse(ROOT/'web'/'index.html')

    @app.get('/api/health')
    def health():
        return {'status':'ok','version':VERSION,'mode':'local-only','marketplace_api':False,'ai_llm':False}

    @app.get('/api/system/posture')
    def system_posture():
        return runtime_posture(local_only=True)

    @app.get('/api/stores')
    def stores():
        with connect(db_path) as db:
            rows=db.execute('SELECT * FROM stores ORDER BY created_at DESC,id').fetchall()
            return {'stores':[dict(r) for r in rows]}

    @app.post('/api/stores',status_code=201)
    def add_store(payload:StoreCreate):
        store_id=str(uuid.uuid4())
        with connect(db_path) as db:
            db.execute('INSERT INTO stores(id,name,platform) VALUES (?,?,?)',(store_id,payload.name,payload.platform))
            log_action(db,'store_created',store_id,subject=payload.platform)
        return {'id':store_id,'name':payload.name,'platform':payload.platform,'is_demo':False}

    @app.delete('/api/stores/{store_id}')
    def delete_store(store_id:str, payload:StoreDeleteConfirm):
        with connect(db_path) as db:
            store=store_record(store_id,db)
            if payload.confirm_name != store['name']:
                raise HTTPException(409,'Confirm exact store name before deletion')
            # Ensure the operator considers any credential connections first.
            db.execute('DELETE FROM stores WHERE id=?',(store_id,))
            log_action(db,'store_deleted',store_id,subject=store['platform'])
        return {'deleted':True,'note':'Any vault credentials must also be revoked in WB/Ozon dashboards'}

    @app.get('/api/local/audit')
    def local_audit(limit:int=Query(default=100,ge=1,le=250)):
        with connect(db_path) as db:
            records=db.execute('SELECT id,created_at,action,store_id,subject,detail FROM local_audit ORDER BY id DESC LIMIT ?',(limit,)).fetchall()
        return {'events':[dict(row) for row in records],'note':'Local operator history only; not append-only production SIEM'}

    @app.get('/api/stores/{store_id}/imports')
    def imports(store_id:str):
        with connect(db_path) as db:
            store_record(store_id,db)
            items=db.execute('SELECT * FROM imports WHERE store_id=? ORDER BY created_at DESC',(store_id,)).fetchall()
        return {'imports':[{**dict(r),'imported_sum_rub':money(r['imported_sum_kopeks']),
                            'control_sum_rub':money(r['control_kopeks'])} for r in items]}

    @app.post('/api/stores/{store_id}/imports')
    async def import_rows(store_id:str,file:UploadFile=File(...),control_sum_rub:str|None=Form(default=None)):
        data=await file.read(MAX_FILE_BYTES+1)
        try:
            parsed=parse_ledger(data,file.filename or '')
            control=parse_money(control_sum_rub) if control_sum_rub is not None and control_sum_rub.strip() else None
        except IngestError as ex:
            raise HTTPException(422,str(ex)) from ex
        if control is not None and control!=parsed.sum_kopeks:
            raise HTTPException(422,f'Контрольная сумма {money(control)} ₽ не совпадает с суммой операций {money(parsed.sum_kopeks)} ₽; импорт отменён')
        import_id=str(uuid.uuid4())
        with connect(db_path) as db:
            store_record(store_id,db)
            if db.execute('SELECT 1 FROM imports WHERE store_id=? AND sha256=?',(store_id,parsed.sha256)).fetchone():
                raise HTTPException(409,'Этот файл уже загружен в магазин — повторный импорт заблокирован')
            db.execute('INSERT INTO imports(id,store_id,filename,sha256,count_rows,control_kopeks,imported_sum_kopeks) VALUES(?,?,?,?,?,?,?)',
                       (import_id,store_id,(file.filename or '')[:160],parsed.sha256,len(parsed.rows),control,parsed.sum_kopeks))
            db.executemany('INSERT INTO ledger(store_id,import_id,source_row,date,sku,category,amount_kopeks,quantity,operation_id,order_id,description) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  [(store_id,import_id,r['source_row'],r['date'],r['sku'],r['category'],r['amount_kopeks'],r['quantity'],r['operation_id'],r['order_id'],r['description']) for r in parsed.rows])
            db.execute('UPDATE stores SET revision=revision+1 WHERE id=?',(store_id,))
            log_action(db,'file_imported',store_id,subject=import_id,detail=f'rows={len(parsed.rows)},sha256={parsed.sha256[:12]}')
        return {'import_id':import_id,'count_rows':len(parsed.rows),'sum_rub':money(parsed.sum_kopeks),
                'file_sha256':parsed.sha256, 'warnings':parsed.warnings,
                'reconciliation':'file_sum_matches_control' if control is not None else 'control_sum_not_provided',
                'source':'normalized_template_only'}

    @app.put('/api/stores/{store_id}/costs')
    def upsert_cost(store_id:str,payload:CostUpdate):
        try:
            cost=parse_money(payload.unit_cost_rub)
        except IngestError as ex:
            raise HTTPException(422,str(ex)) from ex
        if cost<0:
            raise HTTPException(422,'Себестоимость не может быть отрицательной')
        with connect(db_path) as db:
            store_record(store_id,db)
            db.execute('INSERT INTO costs(store_id,sku,unit_cost_kopeks) VALUES(?,?,?) ON CONFLICT(store_id,sku) DO UPDATE SET unit_cost_kopeks=excluded.unit_cost_kopeks,modified_at=CURRENT_TIMESTAMP', (store_id,payload.sku,cost))
            db.execute('UPDATE stores SET revision=revision+1 WHERE id=?',(store_id,))
            log_action(db,'static_cost_changed',store_id,subject=payload.sku)
        return {'sku':payload.sku,'unit_cost_rub':money(cost)}

    @app.post('/api/stores/{store_id}/scenarios/compare')
    def compare_scenario(store_id: str, payload: ScenarioRequest):
        # Local authenticated owner only. Never forwards user assumptions to a model.
        with connect(db_path) as db:
            store_record(store_id,db)
        try:
            return compare(payload)
        except IngestError as ex:
            raise HTTPException(422,str(ex)) from ex

    @app.put('/api/stores/{store_id}/cost-revisions')
    def upsert_cost_revision(store_id: str, payload: CostRevisionUpdate):
        try:
            cost=parse_money(payload.unit_cost_rub)
            stamp=parse_date(payload.effective_from)
        except IngestError as ex:
            raise HTTPException(422,str(ex)) from ex
        if cost<0:
            raise HTTPException(422,'Себестоимость не может быть отрицательной')
        with connect(db_path) as db:
            store_record(store_id,db)
            db.execute('INSERT INTO cost_revisions(store_id,sku,effective_from,unit_cost_kopeks) '
                       'VALUES(?,?,?,?) ON CONFLICT(store_id,sku,effective_from) DO UPDATE SET unit_cost_kopeks=excluded.unit_cost_kopeks',
                       (store_id,payload.sku,stamp,cost))
            db.execute('UPDATE stores SET revision=revision+1 WHERE id=?',(store_id,))
            log_action(db,'historical_cost_changed',store_id,subject=payload.sku,detail=stamp)
        return {'sku':payload.sku,'effective_from':stamp,'unit_cost_rub':money(cost)}

    @app.get('/api/stores/{store_id}/actions')
    def actions(store_id:str):
        with connect(db_path) as db:
            store_record(store_id,db)
            data=db.execute('SELECT * FROM action_plans WHERE store_id=? ORDER BY created_at DESC,id DESC',(store_id,)).fetchall()
        return {'actions':[dict(r) for r in data],'realized_profit_rub':None,
                'warning':'Статус implemented означает выполнение, а не подтверждённый финансовый эффект.'}

    @app.post('/api/stores/{store_id}/actions',status_code=201)
    def add_action(store_id:str,payload:ActionCreate):
        aid=str(uuid.uuid4())
        with connect(db_path) as db:
            store_record(store_id,db)
            db.execute('INSERT INTO action_plans(id,store_id,hypothesis,source_signal_key) VALUES(?,?,?,?)',
                       (aid,store_id,payload.hypothesis,payload.source_signal_key))
            log_action(db,'action_planned',store_id,subject=aid)
        return {'id':aid,'status':'draft','realized_profit_rub':None}

    @app.patch('/api/stores/{store_id}/actions/{action_id}')
    def update_action(store_id:str,action_id:str,payload:ActionUpdate):
        with connect(db_path) as db:
            store_record(store_id,db)
            cur=db.execute('UPDATE action_plans SET status=?,updated_at=CURRENT_TIMESTAMP WHERE store_id=? AND id=?',
                           (payload.status,store_id,action_id))
            if not cur.rowcount:
                raise HTTPException(404,'Задача не найдена в указанном магазине')
            log_action(db,'action_status_changed',store_id,subject=action_id,detail=payload.status)
        return {'id':action_id,'status':payload.status,'realized_profit_rub':None}

    @app.get('/api/stores/{store_id}/cost-revisions')
    def get_cost_revisions(store_id: str):
        with connect(db_path) as db:
            store_record(store_id,db)
            data=db.execute('SELECT sku,effective_from,unit_cost_kopeks FROM cost_revisions WHERE store_id=? ORDER BY sku,effective_from', (store_id,)).fetchall()
        return {'revisions':[{'sku':r['sku'],'effective_from':r['effective_from'],'unit_cost_rub':money(r['unit_cost_kopeks'])} for r in data]}

    @app.get('/api/stores/{store_id}/costs')
    def get_costs(store_id:str):
        with connect(db_path) as db:
            store_record(store_id,db)
            rows=db.execute('SELECT * FROM costs WHERE store_id=? ORDER BY sku',(store_id,)).fetchall()
        return {'costs':[{'sku':r['sku'],'unit_cost_rub':money(r['unit_cost_kopeks'])} for r in rows]}

    def data(store_id:str):
        with connect(db_path) as db:
            store=store_record(store_id,db)
            rows=db.execute('SELECT * FROM ledger WHERE store_id=? ORDER BY date, id',(store_id,)).fetchall()
            costs=db.execute('SELECT sku,unit_cost_kopeks FROM costs WHERE store_id=?',(store_id,)).fetchall()
            revisions=db.execute('SELECT sku,effective_from,unit_cost_kopeks FROM cost_revisions WHERE store_id=? ORDER BY sku,effective_from',(store_id,)).fetchall()
            reviews=db.execute('SELECT signal_key,status,revision FROM reviews WHERE store_id=?',(store_id,)).fetchall()
        cost_map={r['sku']:r['unit_cost_kopeks'] for r in costs}
        for r in revisions:
            if not isinstance(cost_map.get(r['sku']),list):
                cost_map[r['sku']]=[]
            cost_map[r['sku']].append({'effective_from':r['effective_from'],'unit_cost_kopeks':r['unit_cost_kopeks']})
        return store,[dict(r) for r in rows],cost_map,{r['signal_key']:dict(r) for r in reviews}

    @app.get('/api/stores/{store_id}/finance')
    def finance_overview(store_id:str):
        with connect(db_path) as db:
            store_record(store_id,db)
        return browser_json(finance.overview(store_id))

    async def finance_file(file):
        import json
        raw=await file.read(MAX_FILE_BYTES+1)
        if len(raw)>MAX_FILE_BYTES:
            raise HTTPException(413,'Файл превышает 5 МБ')
        def unique_pairs(pairs):
            out={}
            for key,value in pairs:
                if key in out:
                    raise ValueError('duplicate key')
                out[key]=value
            return out
        try:
            return json.loads(raw,object_pairs_hook=unique_pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError,RecursionError):
            raise HTTPException(422,'Некорректный JSON; ключи должны быть уникальными') from None

    @app.get('/api/stores/{store_id}/finance/journal')
    def finance_journal(store_id:str,limit:int=Query(100,ge=1,le=1000)):
        with connect(db_path) as db:
            store_record(store_id,db)
            rows=db.execute('SELECT event_id,provider,source_hash,original_hash,schema_hash,contract_version,category,amount_kopeks,'
                            'operation_date,sku,quantity,posting_id,batch_id,reverses,ingested_at '
                            'FROM finance_sources WHERE store_id=? ORDER BY ingested_at DESC,event_id LIMIT ?',
                            (store_id,limit)).fetchall()
        return browser_json({'events':[dict(r) for r in rows]})

    @app.post('/api/stores/{store_id}/finance/reports')
    async def finance_import(store_id:str,file:UploadFile=File(...)):
        with connect(db_path) as db:
            store_record(store_id,db)
        payload=await finance_file(file)
        try:
            return finance.ingest(store_id,payload)
        except ContractError as ex:
            raise HTTPException(422,str(ex)) from None

    @app.post('/api/stores/{store_id}/finance/controls')
    async def finance_control(store_id:str,report_id:str=Form(...),file:UploadFile=File(...)):
        with connect(db_path) as db:
            store_record(store_id,db)
        payload=await finance_file(file)
        try:
            return browser_json(finance.control(store_id,report_id,payload))
        except ContractError as ex:
            raise HTTPException(422,str(ex)) from None

    @app.post('/api/stores/{store_id}/finance/batches')
    async def finance_batch(store_id:str,file:UploadFile=File(...)):
        with connect(db_path) as db:
            store_record(store_id,db)
        payload=await finance_file(file)
        try:
            return finance.add_batch(store_id,payload)
        except ContractError as ex:
            raise HTTPException(422,str(ex)) from None

    @app.get('/api/stores/{store_id}/dashboard')
    def dashboard(store_id:str):
        store,entries,costs,reviews=data(store_id)
        result=summary(entries,costs)
        findings=analyze(entries,costs)
        for f in findings:
            review=reviews.get(f['key'])
            f['status']=review['status'] if review and review['revision']==store['revision'] else 'detected'
        totals={'historical_negative_contribution_rub':sum(f['amount_rub'] or 0 for f in findings if f['code']=='NEGATIVE_SKU'),
                'hypothetical_period_deviation_rub':sum(f['amount_rub'] or 0 for f in findings if f['code'] in ('ADS_RATE_SPIKE','LOGISTICS_RATE_SPIKE'))}
        return {'store':store,'summary':result,'opportunities':findings,'totals':totals,
                'note':'Суммы разных классов НЕ складываются в «найденную экономию». Нет гарантий 100 000 ₽.',
                'engine_version':VERSION}

    @app.post('/api/stores/{store_id}/reviews/{signal_key:path}')
    def review(store_id:str,signal_key:str,payload:ReviewUpdate):
        store,entries,costs,_=data(store_id)
        if signal_key not in {r['key'] for r in analyze(entries,costs)}:
            raise HTTPException(404,'Сигнал не найден в актуальном аудите')
        with connect(db_path) as db:
            db.execute('INSERT INTO reviews(store_id,signal_key,revision,status) VALUES (?,?,?,?) ON CONFLICT(store_id,signal_key) DO UPDATE SET revision=excluded.revision,status=excluded.status,updated_at=CURRENT_TIMESTAMP',
                       (store_id,signal_key,store['revision'],payload.status))
            log_action(db,'review_status_changed',store_id,subject=signal_key,detail=payload.status)
        return {'key':signal_key,'status':payload.status,'revision':store['revision']}

    @app.get('/api/stores/{store_id}/ledger')
    def get_ledger(store_id:str,limit:int=Query(default=100,ge=1,le=1000),sku:str|None=None):
        with connect(db_path) as db:
            store_record(store_id,db)
            if sku:
                rows=db.execute('SELECT * FROM ledger WHERE store_id=? AND sku=? ORDER BY date DESC,id DESC LIMIT ?',(store_id,sku,limit)).fetchall()
            else:
                rows=db.execute('SELECT * FROM ledger WHERE store_id=? ORDER BY date DESC,id DESC LIMIT ?',(store_id,limit)).fetchall()
        return {'entries':[{**dict(r),'amount_rub':money(r['amount_kopeks'])} for r in rows]}

    @app.get('/api/stores/{store_id}/ledger.csv')
    def export_ledger(store_id:str):
        import csv,io
        with connect(db_path) as db:
            store_record(store_id,db)
            rows=db.execute('SELECT * FROM ledger WHERE store_id=? ORDER BY date,id',(store_id,)).fetchall()
        output=io.StringIO()
        writer=csv.writer(output)
        writer.writerow(['date','sku','category','amount_rub','quantity','operation_id','order_id','import_id','source_row'])
        for r in rows:
            def safe(s):
                t=str(s or '')
                return "'"+t if t and t[0] in '=+-@\t\r' else t
            writer.writerow([r['date'],safe(r['sku']),r['category'],money(r['amount_kopeks']),r['quantity'],safe(r['operation_id']),safe(r['order_id']),r['import_id'],r['source_row']])
        return Response(content='\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="ledger-export.csv"'})

    @app.post('/api/demo',status_code=201)
    def add_demo():
        """Synthetic data only, explicit DEMO flag."""
        from .demo_data import generate_demo
        demo=generate_demo()
        store_id=str(uuid.uuid4())
        import_id=str(uuid.uuid4())
        with connect(db_path) as db:
            db.execute('INSERT INTO stores(id,name,platform,is_demo,revision) VALUES(?,?,?,?,?)',(store_id,'Учебный магазин', 'WB',1,1))
            db.execute('INSERT INTO imports(id,store_id,filename,sha256,count_rows,imported_sum_kopeks) VALUES (?,?,?,?,?,?)',(import_id,store_id,'SYNTHETIC_DEMO.csv',f'synthetic:{store_id}',len(demo['rows']),sum(r['amount_kopeks'] for r in demo['rows'])))
            db.executemany('INSERT INTO ledger(store_id,import_id,source_row,date,sku,category,amount_kopeks,quantity,operation_id,order_id,description) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                 [(store_id,import_id,i,r['date'],r['sku'],r['category'],r['amount_kopeks'],r['quantity'],r['operation_id'],r['order_id'],r['description']) for i,r in enumerate(demo['rows'],2)])
            db.executemany('INSERT INTO costs(store_id,sku,unit_cost_kopeks) VALUES(?,?,?)',[(store_id,k,v) for k,v in demo['costs'].items()])
        return {'id':store_id,'is_demo':True}

    return app

app=create_app()
