"""Synthetic report workflow laboratory. New routes NEVER enter live allowlist.

The response shapes here are local fixture contracts, not asserted WB/Ozon schemas.
Read-only report generation at Ozon creates report identifiers only, not transactions.
"""
from __future__ import annotations
import json
import re
import httpx
from .finance import day, identifier
from .offline_sync import ContractError, ExternalAPIError, extract_wb, RETRYABLE
from .integration_security import SecurityError

WB_LIST='/api/finance/v1/sales-reports/list'
WB_DETAILS='/api/finance/v1/sales-reports/detailed'
OZ_TYPES='/v1/finance/accrual/types'
OZ_POSTINGS='/v1/finance/accrual/postings'
OZ_LIST='/v1/report/finance/realization/list'
OZ_REPORT='/v1/report/finance/realization/number/create'
OZ_POSTING_REPORT='/v1/report/finance/realization/number/posting/create'


def allowed(provider,path):
    return (provider=='WB' and (path in (WB_LIST,WB_DETAILS) or
            re.fullmatch(re.escape(WB_DETAILS)+r'/[1-9][0-9]{0,18}',path))) or (
            provider=='Ozon' and path in (OZ_TYPES,OZ_POSTINGS,OZ_LIST,OZ_REPORT,OZ_POSTING_REPORT))


class ReportLab:
    def __init__(self,vault,transport,*,attempts=3,sleep=None):
        if not isinstance(transport,httpx.MockTransport):
            raise SecurityError('Report lab requires httpx.MockTransport')
        if type(attempts) is not int or not 1<=attempts<=5:
            raise ValueError('Invalid attempts')
        self.vault=vault; self.attempts=attempts; self.sleep=sleep or (lambda _:None)
        self.client=httpx.Client(transport=transport,trust_env=False,follow_redirects=False)
        self.calls=[]

    def close(self):
        self.client.close()

    def request(self,cid,store,provider,path,body):
        if not allowed(provider,path):
            raise SecurityError('Unapproved report-lab endpoint')
        if not self.vault.is_credential_for(cid,store,provider):
            raise SecurityError('Store/provider mismatch')
        for attempt in range(self.attempts):
            self.vault.check(cid)
            def dispatch(credential):
                headers={'Authorization':credential.secret} if provider=='WB' else {
                    'Client-Id':credential.client_id,'Api-Key':credential.secret}
                host='https://finance-api.wildberries.ru' if provider=='WB' else 'https://api-seller.ozon.ru'
                self.calls.append((provider,path,body.copy()))
                return self.client.post(host+path,json=body,headers=headers)
            try:
                response=self.vault.with_authorized(cid,dispatch)
            except httpx.TransportError:
                if attempt+1==self.attempts:
                    raise ExternalAPIError(0) from None
                self.sleep(2**attempt); continue
            if response.status_code in (401,403):
                self.vault.set_stop('credential',cid,True,'report_lab_auth_failed')
            if response.status_code in RETRYABLE and attempt+1<self.attempts:
                self.sleep(2**attempt); continue
            if response.status_code==204:
                return None
            if response.status_code!=200:
                raise ExternalAPIError(response.status_code)
            try:
                result=response.json()
            except (ValueError,UnicodeError):
                raise ContractError('Invalid report JSON') from None
            # This marker makes it impossible to mistake these fixture shapes for verified live schemas.
            if not isinstance(result,dict) or result.get('lab_contract')!='aipo.report-lab/1':
                raise ContractError('Unverified provider response; retain in staging')
            return result
        raise ExternalAPIError(0)

    def wb_reports(self,cid,store,start,end,*,limit=100,max_pages=20):
        day(start);day(end)
        if end<start or type(limit) is not int or not 1<=limit<=1000 or not 1<=max_pages<=1000:
            raise ContractError('Invalid WB catalog scope')
        offset=0; result={}
        for _ in range(max_pages):
            page=self.request(cid,store,'WB',WB_LIST,{'dateFrom':start,'dateTo':end,'period':'weekly','offset':offset,'limit':limit})
            if page is None or not isinstance(page.get('reports'),list):
                raise ContractError('Invalid mock WB list')
            rows=page['reports']
            if not rows:
                return list(result.values())
            if len(rows)>limit:
                raise ContractError('Oversized WB catalog page')
            for row in rows:
                if not isinstance(row,dict) or type(row.get('reportId')) is not int or not 0<row['reportId']<2**63:
                    raise ContractError('Invalid mock report id')
                rid=str(row['reportId'])
                if rid in result:
                    raise ContractError('WB report catalog cursor loop')
                result[rid]={'report_id':rid} # no seller names/PII
            offset+=len(rows)
        raise ContractError('WB catalog page limit exceeded')

    def wb_details(self,cid,store,start,end,*,report_id=None,max_pages=20):
        day(start);day(end)
        if end<start or not 1<=max_pages<=1000:
            raise ContractError('Invalid WB detail scope')
        path=WB_DETAILS
        if report_id is not None:
            if not isinstance(report_id,str) or not re.fullmatch('[1-9][0-9]{0,18}',report_id) or int(report_id)>=2**63:
                raise ContractError('Invalid WB report id')
            path+='/'+report_id
        cursor=0; result={}
        for _ in range(max_pages):
            body={'rrdId':cursor,'limit':1000} if report_id else {
                'dateFrom':start,'dateTo':end,'period':'weekly','rrdId':cursor,'limit':1000}
            page=self.request(cid,store,'WB',path,body)
            if page is None:
                return list(result.values())
            rows,next_id=extract_wb(page.get('rows'))
            if not rows or next_id<=cursor or any(int(rid)<=cursor for rid,_ in rows):
                raise ContractError('WB detail cursor invalid')
            for rid,row in rows:
                if report_id and row.get('reportId')!=report_id:
                    raise ContractError('WB detail report mismatch')
                result[rid]=row
            cursor=next_id
        raise ContractError('WB detail page limit exceeded')

    def ozon_types(self,cid,store):
        page=self.request(cid,store,'Ozon',OZ_TYPES,{})
        if page is None or not isinstance(page.get('types'),list):
            raise ContractError('Invalid mock Ozon type catalog')
        # IDs only. A provider type name NEVER creates a semantic profit mapping.
        ids=[identifier(r.get('type_id')) for r in page['types'] if isinstance(r,dict)]
        if len(ids)!=len(page['types']) or len(set(ids))!=len(ids):
            raise ContractError('Invalid mock Ozon type IDs')
        return {'type_ids':ids,'automatic_financial_mapping':False}

    def ozon_postings(self,cid,store,accrual_ids):
        if not isinstance(accrual_ids,list) or not 1<=len(accrual_ids)<=1000:
            raise ContractError('Invalid accrual IDs')
        for aid in accrual_ids:
            identifier(aid)
        page=self.request(cid,store,'Ozon',OZ_POSTINGS,{'accrual_ids':accrual_ids})
        if page is None or not isinstance(page.get('links'),list):
            raise ContractError('Invalid mock posting links')
        links={}
        for row in page['links']:
            if not isinstance(row,dict):
                raise ContractError('Invalid mock posting link')
            aid=identifier(row.get('accrual_id'));pid=identifier(row.get('posting_id'))
            if aid not in accrual_ids or (aid in links and links[aid]!=pid):
                raise ContractError('Ambiguous posting link')
            links[aid]=pid
        if set(links)!=set(accrual_ids):
            raise ContractError('Incomplete posting links')
        return links

    def ozon_realization(self,cid,store,start,end):
        day(start);day(end)
        if end<start:
            raise ContractError('Invalid realization period')
        page=self.request(cid,store,'Ozon',OZ_LIST,{'start':start,'end':end})
        if page is None or not isinstance(page.get('numbers'),list) or len(page['numbers'])>1000:
            raise ContractError('Invalid mock realization catalog')
        numbers=[identifier(n,'report number') for n in page['numbers']]
        if len(numbers)!=len(set(numbers)):
            raise ContractError('Duplicate realization number')
        reports=[]
        for number in numbers:
            item={'number':number}
            for kind,path in [('standard',OZ_REPORT),('posting',OZ_POSTING_REPORT)]:
                reply=self.request(cid,store,'Ozon',path,{'number':number})
                if reply is None:
                    raise ContractError('Ambiguous realization response')
                code=reply.get('report_code')
                if code is not None:
                    identifier(code,'report code')
                item[kind]=code # None means not formed; NOT a report with zero money.
            reports.append(item)
        return {'reports':reports,'recognized_financial_events':0,'live_verified':False}
