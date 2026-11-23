"""Separate localhost lab API; never mount alongside app.server financial routes."""
import argparse
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.ru_guardrails import is_loopback_peer
from .core import Lab, Denied

COOKIE='aipo_lab_session'


class Login(BaseModel):
    login: str=Field(min_length=3,max_length=120)
    password: str=Field(min_length=1,max_length=256)


class Store(BaseModel):
    label: str=Field(min_length=1,max_length=80)


class Invitation(BaseModel):
    login: str=Field(min_length=3,max_length=120)
    role: Literal['manager','auditor']
    stores: list[str]=Field(min_length=1,max_length=100)
    ttl: int=Field(default=3600,ge=60,le=86400)


class Accept(BaseModel):
    token: str=Field(min_length=20,max_length=100)


class Job(BaseModel):
    store: str=Field(min_length=1,max_length=64)
    idempotency_key: str=Field(min_length=1,max_length=120)


def create_app(path, *, test_mode=False):
    lab=Lab(path)
    app=FastAPI(title='AI Profit Office RU — synthetic security lab',version='0.3.7',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.lab=lab
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost']+(['testserver'] if test_mode else []))

    @app.middleware('http')
    async def security(request: Request, call_next):
        if not test_mode and not is_loopback_peer(request.client.host if request.client else None):
            return JSONResponse({'detail':'localhost only'},status_code=403)
        if request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'detail':'cross-site denied'},status_code=403)
        mutation=request.method not in ('GET','HEAD','OPTIONS')
        origin=request.headers.get('origin')
        if mutation and origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'origin denied'},status_code=403)
        if request.url.path.startswith('/api/') and request.url.path!='/api/login':
            try:
                request.state.actor=lab.authenticate(request.cookies.get(COOKIE,''),request.headers.get('x-aipo-csrf'),mutation=mutation)
            except Denied:
                return JSONResponse({'detail':'session or CSRF invalid'},status_code=401)
        response=await call_next(request)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                                 'X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
                                 'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @app.exception_handler(Denied)
    async def denied(request,exc):
        return JSONResponse({'detail':'Доступ запрещён или объект недоступен'},status_code=403)

    @app.get('/')
    def index():
        return FileResponse(Path(__file__).with_name('index.html'))

    @app.get('/lab.js')
    def javascript():
        return FileResponse(Path(__file__).with_name('lab.js'),media_type='text/javascript')

    @app.get('/lab.css')
    def stylesheet():
        return FileResponse(Path(__file__).with_name('lab.css'),media_type='text/css')

    @app.post('/api/login')
    def login(p: Login):
        token,csrf=lab.login(p.login,p.password)
        r=JSONResponse({'csrf':csrf})
        r.set_cookie(COOKIE,token,httponly=True,samesite='strict',secure=False,max_age=1800)
        return r

    @app.post('/api/logout')
    def logout(r: Request):
        lab.logout(r.state.actor)
        response=JSONResponse({'signed_out':True})
        response.delete_cookie(COOKIE)
        return response

    @app.get('/api/me')
    def me(r: Request):
        return {'user_id':r.state.actor.user_id,'memberships':lab.tenants(r.state.actor),'market':'RU','production_ready':False}

    @app.get('/api/tenants/{tenant}/stores')
    def stores(tenant: str,r: Request):
        return lab.stores(r.state.actor,tenant)

    @app.post('/api/tenants/{tenant}/stores')
    def add_store(tenant: str,p: Store,r: Request):
        return {'id':lab.add_store(r.state.actor,tenant,p.label)}

    @app.post('/api/tenants/{tenant}/invites')
    def invite(tenant: str,p: Invitation,r: Request):
        return {'token':lab.invite(r.state.actor,tenant,p.login,p.role,p.stores,p.ttl)}

    @app.post('/api/accept')
    def accept(p: Accept,r: Request):
        return {'tenant':lab.accept(r.state.actor,p.token)}

    @app.delete('/api/tenants/{tenant}/members/{user}')
    def revoke(tenant: str,user: str,r: Request):
        lab.revoke(r.state.actor,tenant,user)
        return {'revoked':True}

    @app.post('/api/tenants/{tenant}/stop')
    def stop(tenant: str,r: Request):
        lab.stop(r.state.actor,tenant)
        return {'stopped':True}

    @app.post('/api/tenants/{tenant}/resume')
    def resume(tenant: str,r: Request):
        lab.stop(r.state.actor,tenant,False)
        return {'stopped':False}

    @app.post('/api/tenants/{tenant}/jobs')
    def enqueue(tenant: str,p: Job,r: Request):
        return {'id':lab.enqueue(r.state.actor,tenant,p.store,p.idempotency_key)}

    @app.get('/api/tenants/{tenant}/jobs/{jid}')
    def job(tenant: str,jid: str,r: Request):
        return lab.job(r.state.actor,tenant,jid)

    @app.get('/api/tenants/{tenant}/jobs/{jid}/export')
    def export(tenant: str,jid: str,r: Request):
        result=lab.job(r.state.actor,tenant,jid)
        return JSONResponse(result,headers={'Content-Disposition':'attachment; filename="synthetic-job.json"'})

    @app.get('/api/tenants/{tenant}/audit')
    def audit(tenant: str,r: Request):
        return lab.audit(r.state.actor,tenant)

    return app


def main():
    import uvicorn
    p=argparse.ArgumentParser(description='Synthetic local lab, no marketplace connections')
    p.add_argument('--db',default=str(Path.home()/'.aipo_saas_lab'/'lab.db'))
    args=p.parse_args()
    Path(args.db).parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    uvicorn.run(create_app(args.db),host='127.0.0.1',port=8766,proxy_headers=False,access_log=False)


if __name__=='__main__':
    main()
