"""v0.3.6 local financial reconciliation, exact kopeks and immutable evidence.

Accepts OUR normalized contract, never treats an API reply as this contract.
External WB/Ozon contracts remain staged until independently verified.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from .storage import connect, initialize, log_action
from .offline_sync import ContractError

CONTRACT = 'aipo.normalized-finance/1'
CATEGORIES = {'sale', 'return', 'storno', 'fee', 'storage', 'delivery', 'refund', 'other'}
LABELS = {'CHECKING': 'Данные в процессе проверки', 'MISMATCH': 'Расхождение',
          'VERIFIED': 'Сверено', 'STALE': 'Устарело', 'PARTIAL': 'Частично загружено'}
ROW_FIELDS = {'event_id','date','sku','type','amount','quantity','posting_id','batch_id','reverses'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS finance_contracts (
 version TEXT PRIMARY KEY, definition TEXT NOT NULL, sha256 TEXT NOT NULL,
 first_seen TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_schema_observations (
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE, provider TEXT NOT NULL,
 contract_hash TEXT NOT NULL, schema_hash TEXT NOT NULL, first_seen TEXT NOT NULL,
 PRIMARY KEY(store_id,provider,contract_hash,schema_hash));
CREATE TABLE IF NOT EXISTS finance_reports (
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 report_id TEXT NOT NULL, provider TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 complete INTEGER NOT NULL DEFAULT 0, imported_at TEXT NOT NULL,
 PRIMARY KEY(store_id,report_id));
CREATE TABLE IF NOT EXISTS finance_sources (
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 event_id TEXT NOT NULL, provider TEXT NOT NULL, source_hash TEXT NOT NULL, original_hash TEXT NOT NULL,
 schema_hash TEXT NOT NULL, contract_version TEXT NOT NULL, payload TEXT NOT NULL,
 category TEXT NOT NULL, amount_kopeks INTEGER NOT NULL, operation_date TEXT NOT NULL,
 sku TEXT NOT NULL, quantity INTEGER, posting_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 reverses TEXT NOT NULL, ingested_at TEXT NOT NULL,
 PRIMARY KEY(store_id,event_id));
CREATE TABLE IF NOT EXISTS finance_report_events (
 store_id TEXT NOT NULL, report_id TEXT NOT NULL, event_id TEXT NOT NULL,
 PRIMARY KEY(store_id,report_id,event_id),
 FOREIGN KEY(store_id,report_id) REFERENCES finance_reports(store_id,report_id) ON DELETE CASCADE,
 FOREIGN KEY(store_id,event_id) REFERENCES finance_sources(store_id,event_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS finance_quarantine (
 id TEXT PRIMARY KEY, store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 report_id TEXT NOT NULL, source_hash TEXT NOT NULL, schema_hash TEXT NOT NULL,
 reason TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(store_id,report_id,source_hash,reason));
CREATE TABLE IF NOT EXISTS finance_controls (
 id TEXT PRIMARY KEY, store_id TEXT NOT NULL, report_id TEXT NOT NULL,
 reference_id TEXT NOT NULL, reference_hash TEXT NOT NULL, payload TEXT NOT NULL, checked_at TEXT NOT NULL,
 FOREIGN KEY(store_id,report_id) REFERENCES finance_reports(store_id,report_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS finance_batches (
 store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
 batch_id TEXT NOT NULL, sku TEXT NOT NULL, effective_from TEXT NOT NULL,
 purchased_units INTEGER NOT NULL CHECK(purchased_units>0), unit_cost_kopeks INTEGER NOT NULL CHECK(unit_cost_kopeks>=0),
 PRIMARY KEY(store_id,batch_id));
CREATE INDEX IF NOT EXISTS finance_report_lookup ON finance_report_events(store_id,report_id);
CREATE TRIGGER IF NOT EXISTS finance_sources_immutable BEFORE UPDATE ON finance_sources
 BEGIN SELECT RAISE(ABORT,'immutable financial source'); END;
CREATE TRIGGER IF NOT EXISTS finance_controls_immutable BEFORE UPDATE ON finance_controls
 BEGIN SELECT RAISE(ABORT,'immutable financial control'); END;
CREATE TRIGGER IF NOT EXISTS finance_contracts_immutable BEFORE UPDATE ON finance_contracts
 BEGIN SELECT RAISE(ABORT,'immutable contract'); END;
CREATE TRIGGER IF NOT EXISTS finance_batches_immutable BEFORE UPDATE ON finance_batches
 BEGIN SELECT RAISE(ABORT,'immutable batch; add a new batch'); END;
'''


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def identifier(value, name='identifier', empty=False):
    if not isinstance(value, str) or len(value) > 160 or (not empty and not value):
        raise ContractError('Invalid '+name)
    if any(ord(c)<32 for c in value):
        raise ContractError('Invalid '+name)
    return value


def day(value):
    if not isinstance(value, str):
        raise ContractError('Invalid ISO date')
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ContractError('Invalid ISO date') from None
    return value


def moscow_day(value):
    """WB timestamps have Moscow calendar semantics; naive times are rejected."""
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    except (ValueError, TypeError, AttributeError):
        raise ContractError('Expected timezone-aware timestamp') from None


def kopeks(value):
    # API JSON floats are deliberately NOT admitted to the normalized ledger.
    if not isinstance(value, (str,int)) or isinstance(value,bool):
        raise ContractError('Money must be decimal text or integer rubles')
    if isinstance(value,str) and not re.fullmatch(r'-?\d{1,13}(\.\d{1,2})?',value):
        raise ContractError('Money must have at most two decimals')
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ContractError('Invalid money') from None
    if not number.is_finite() or abs(number)>Decimal('1000000000000'):
        raise ContractError('Money out of range')
    return int(number*100)


def rub_text(value):
    return None if value is None else format(Decimal(value)/100, '.2f')


def normalized(raw, start, end):
    if not isinstance(raw,dict) or set(raw)-ROW_FIELDS:
        raise ContractError('UNKNOWN_FIELDS')
    required = {'event_id','date','sku','type','amount','quantity'}
    if not required<=set(raw):
        raise ContractError('MISSING_FIELDS')
    result = {k:raw.get(k,'') for k in ROW_FIELDS}
    for field in ('event_id','sku','posting_id','batch_id','reverses'):
        identifier(result[field],field,empty=field!='event_id')
    day(result['date'])
    if not start<=result['date']<=end:
        raise ContractError('DATE_OUTSIDE_REPORT')
    if result['type'] not in CATEGORIES:
        raise ContractError('UNKNOWN_TYPE')
    amount = kopeks(result['amount'])
    result['amount'] = rub_text(amount)
    q = result['quantity']
    if q is not None and (type(q) is not int or not -1000000<=q<=1000000):
        raise ContractError('INVALID_QUANTITY')
    kind=result['type']
    if kind=='sale' and (not result['sku'] or type(q) is not int or q<=0 or amount<0):
        raise ContractError('INVALID_SALE')
    if kind=='return' and (not result['sku'] or type(q) is not int or q>=0 or amount>0 or not result['reverses']):
        raise ContractError('INVALID_RETURN')
    if kind=='storno' and not result['reverses']:
        raise ContractError('STORNO_REQUIRES_REFERENCE')
    if kind not in ('sale','return','storno') and q not in (None,0):
        raise ContractError('NON_SALE_QUANTITY')
    # Signed adjustments on fees/storage/delivery/other are preserved as given.
    # 'refund' is a refund of a fee, never a customer return of goods.
    if kind=='refund' and amount<0:
        raise ContractError('INVALID_FEE_REFUND')
    return result,amount


class Finance:
    def __init__(self,path:Path|str):
        self.path=Path(path)
        initialize(self.path)
        definition=canonical({'contract':CONTRACT,'categories':sorted(CATEGORIES),'fields':sorted(ROW_FIELDS),
                              'money':'decimal-string/RUB','source':'normalized local import; not provider API'})
        with connect(self.path) as db:
            db.executescript(SCHEMA)
            db.execute('INSERT OR IGNORE INTO finance_contracts VALUES(?,?,?,?)',
                       (CONTRACT,definition,hashlib.sha256(definition.encode()).hexdigest(),utcnow()))

    @staticmethod
    def _store(db,store,provider=None):
        found=db.execute('SELECT platform FROM stores WHERE id=?',(store,)).fetchone()
        if not found or (provider is not None and found['platform']!=provider):
            raise ContractError('STORE_PROVIDER_MISMATCH')

    def ingest(self,store,report):
        if not isinstance(report,dict) or set(report)!={'contract','report_id','provider','start','end','complete','events'}:
            raise ContractError('Invalid report envelope')
        if report['provider'] not in ('WB','Ozon'):
            raise ContractError('Invalid provider')
        rid=identifier(report['report_id'],'report_id')
        start,end=day(report['start']),day(report['end'])
        if end<start or type(report['complete']) is not bool or not isinstance(report['events'],list) or len(report['events'])>15000:
            raise ContractError('Invalid report period/completeness/size')
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self._store(db,store,report['provider'])
            previous=db.execute('SELECT * FROM finance_reports WHERE store_id=? AND report_id=?',(store,rid)).fetchone()
            if previous and (previous['start_date'],previous['end_date'],previous['provider'])!=(start,end,report['provider']):
                raise ContractError('REPORT_SCOPE_CONFLICT')
            db.execute('INSERT OR IGNORE INTO finance_reports VALUES(?,?,?,?,?,?,?)',
                       (store,rid,report['provider'],start,end,0,utcnow()))
            accepted=duplicates=quarantined=0
            raw_events=report['events']
            if report['contract']!=CONTRACT and not raw_events:
                raw_events=[{'contract':report['contract']}]
            for raw in raw_events:
                try:
                    raw_hash=digest(raw)
                except (ValueError,TypeError):
                    raise ContractError('Invalid JSON source') from None
                schema_hash=digest({k:type(v).__name__ for k,v in raw.items()} if isinstance(raw,dict) else type(raw).__name__)
                db.execute('INSERT OR IGNORE INTO finance_schema_observations VALUES(?,?,?,?,?)',
                           (store,report['provider'],digest(report['contract']),schema_hash,utcnow()))
                try:
                    if report['contract']!=CONTRACT:
                        raise ContractError('UNKNOWN_CONTRACT')
                    item,amount=normalized(raw,start,end)
                    blob=canonical(item)
                    source_hash=digest(item)
                    old=db.execute('SELECT source_hash FROM finance_sources WHERE store_id=? AND event_id=?',
                                   (store,item['event_id'])).fetchone()
                    if old and old['source_hash']!=source_hash:
                        raise ContractError('SOURCE_EVENT_CONFLICT')
                    linked=db.execute('SELECT 1 FROM finance_report_events WHERE store_id=? AND report_id=? AND event_id=?',
                                      (store,rid,item['event_id'])).fetchone()
                    if previous and previous['complete'] and not linked:
                        raise ContractError('SEALED_REPORT_CHANGED')
                    if not old:
                        db.execute('INSERT INTO finance_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (store,item['event_id'],report['provider'],source_hash,raw_hash,schema_hash,CONTRACT,blob,item['type'],amount,
                             item['date'],item['sku'],item['quantity'],item['posting_id'],item['batch_id'],item['reverses'],utcnow()))
                        accepted+=1
                    else:
                        duplicates+=1
                    db.execute('INSERT OR IGNORE INTO finance_report_events VALUES(?,?,?)',(store,rid,item['event_id']))
                except (ContractError,TypeError) as ex:
                    # Fixed reason codes only: never store unexpected contents/PII in diagnostics.
                    reason=str(ex) if isinstance(ex,ContractError) else 'INVALID_TYPE'
                    db.execute('INSERT OR IGNORE INTO finance_quarantine VALUES(?,?,?,?,?,?,?)',
                               (str(uuid.uuid4()),store,rid,raw_hash,schema_hash,reason,utcnow()))
                    quarantined+=1
            # Completion is monotone. Replaying a page never makes a report incomplete again.
            db.execute('UPDATE finance_reports SET complete=MAX(complete,?),imported_at=? WHERE store_id=? AND report_id=?',
                       (int(report['complete']),utcnow(),store,rid))
            log_action(db,'finance_report_ingested',store,rid,f'accepted={accepted},quarantine={quarantined}')
        return {'accepted':accepted,'duplicates':duplicates,'quarantined':quarantined,'report_id':rid}

    def control(self,store,rid,control):
        """Independent user-supplied reference, never derived from our event sum."""
        expected={'reference_id','total','categories','postings'}
        if not isinstance(control,dict) or set(control)!=expected:
            raise ContractError('Invalid independent control')
        identifier(control['reference_id'],'reference_id')
        total=kopeks(control['total'])
        cats,postings=control['categories'],control['postings']
        if not isinstance(cats,dict) or set(cats)-CATEGORIES or not isinstance(postings,dict):
            raise ContractError('Invalid control breakdown')
        normalized_cats={k:kopeks(v) for k,v in cats.items()}
        normalized_postings={identifier(k,'posting'):kopeks(v) for k,v in postings.items()}
        if sum(normalized_cats.values())!=total:
            raise ContractError('CONTROL_TOTAL_DISAGREES_WITH_CATEGORIES')
        payload={'total':total,'categories':normalized_cats,'postings':normalized_postings}
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self._store(db,store)
            if not db.execute('SELECT 1 FROM finance_reports WHERE store_id=? AND report_id=?',(store,rid)).fetchone():
                raise ContractError('REPORT_NOT_FOUND')
            db.execute('INSERT INTO finance_controls VALUES(?,?,?,?,?,?,?)',
                       (str(uuid.uuid4()),store,rid,control['reference_id'],digest(control),canonical(payload),utcnow()))
            log_action(db,'finance_control_added',store,rid)
        return self.report(store,rid)

    def add_batch(self,store,batch):
        if not isinstance(batch,dict) or set(batch)!={'batch_id','sku','effective_from','purchased_units','unit_cost'}:
            raise ContractError('Invalid cost batch')
        bid,sku=identifier(batch['batch_id']),identifier(batch['sku'])
        stamp=day(batch['effective_from']); qty=batch['purchased_units']; amount=kopeks(batch['unit_cost'])
        if type(qty) is not int or not 0<qty<=1000000 or amount<0:
            raise ContractError('Invalid batch quantity/cost')
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            self._store(db,store)
            old=db.execute('SELECT * FROM finance_batches WHERE store_id=? AND batch_id=?',(store,bid)).fetchone()
            if old:
                if (old['sku'],old['effective_from'],old['purchased_units'],old['unit_cost_kopeks'])!=(sku,stamp,qty,amount):
                    raise ContractError('BATCH_IMMUTABLE')
            else:
                db.execute('INSERT INTO finance_batches VALUES(?,?,?,?,?,?)',(store,bid,sku,stamp,qty,amount))
                log_action(db,'finance_batch_added',store,bid)
        return {'batch_id':bid}

    def _costs(self,db,store,rows):
        # Resolve references across report boundaries in the same store only.
        all_rows={r['event_id']:dict(r) for r in db.execute('SELECT * FROM finance_sources WHERE store_id=?',(store,))}
        batches={r['batch_id']:dict(r) for r in db.execute('SELECT * FROM finance_batches WHERE store_id=?',(store,))}
        revisions=defaultdict(list)
        for r in db.execute('SELECT * FROM cost_revisions WHERE store_id=? ORDER BY effective_from',(store,)):
            revisions[r['sku']].append(dict(r))
        consumed=defaultdict(int); returned=defaultdict(int); reversed_ids=set(); invalid=set()
        for r in all_rows.values():
            if r['category']=='return':
                returned[r['reverses']]+=abs(r['quantity'])
        for r in all_rows.values():
            if r['category']=='sale' and r['batch_id']:
                consumed[r['batch_id']]+=r['quantity']
            if r['category']=='storno':
                target=all_rows.get(r['reverses'])
                if (not target or target['category'] not in ('sale','fee','storage','delivery','refund','other') or
                    target['sku']!=r['sku'] or target['posting_id']!=r['posting_id'] or
                    r['amount_kopeks']!=-target['amount_kopeks'] or r['reverses'] in reversed_ids or
                    r['operation_date']<target['operation_date'] or
                    (target['category']=='sale' and (r['quantity']!=-target['quantity'] or returned[target['event_id']]>0)) or
                    (target['category']!='sale' and r['quantity'] not in (None,0))):
                    invalid.add(r['event_id'])
                reversed_ids.add(r['reverses'])
        values={}; missing=set()
        for row in rows:
            r=dict(row); kind=r['category']; target=r
            if kind=='storno' and r['event_id'] in invalid:
                missing.add(r['sku'] or '(без SKU)'); continue
            if kind=='storno' and r['event_id'] not in invalid:
                ref=all_rows[r['reverses']]
                if ref['category']=='sale':
                    target=ref; kind='sale'
            if kind not in ('sale','return'):
                values[r['event_id']]=0; continue
            if kind=='return':
                target=all_rows.get(r['reverses'])
                if (not target or target['category']!='sale' or target['sku']!=r['sku'] or
                    r['operation_date']<target['operation_date'] or returned[r['reverses']]>target['quantity'] or
                    r['batch_id']!=target['batch_id']):
                    missing.add(r['sku']); invalid.add(r['event_id']); continue
            unit=None; bid=target['batch_id']
            if bid:
                batch=batches.get(bid)
                if (batch and batch['sku']==r['sku'] and batch['effective_from']<=target['operation_date'] and
                    consumed[bid]<=batch['purchased_units']):
                    unit=batch['unit_cost_kopeks']
            else:
                for revision in revisions[r['sku']]:
                    if revision['effective_from']<=target['operation_date']:
                        unit=revision['unit_cost_kopeks']
            if unit is None:
                missing.add(r['sku']); continue
            values[r['event_id']]=unit*r['quantity']
        return values,sorted(missing),sorted(invalid & {r['event_id'] for r in rows})

    def report(self,store,rid,*,now=None):
        now=now or datetime.now(timezone.utc)
        with connect(self.path) as db:
            self._store(db,store)
            report=db.execute('SELECT * FROM finance_reports WHERE store_id=? AND report_id=?',(store,rid)).fetchone()
            if not report:
                raise ContractError('REPORT_NOT_FOUND')
            rows=db.execute('SELECT s.* FROM finance_sources s JOIN finance_report_events e USING(store_id,event_id) '
                            'WHERE e.store_id=? AND e.report_id=? ORDER BY s.operation_date,s.event_id',(store,rid)).fetchall()
            quarantine=[dict(r) for r in db.execute('SELECT reason,source_hash,schema_hash,created_at FROM finance_quarantine '
                                                    'WHERE store_id=? AND report_id=?',(store,rid))]
            reference=db.execute('SELECT * FROM finance_controls WHERE store_id=? AND report_id=? ORDER BY rowid DESC LIMIT 1',(store,rid)).fetchone()
            costs,missing,invalid=self._costs(db,store,rows)
        totals=defaultdict(int); postings=defaultdict(int); per_sku=defaultdict(lambda:{'amount_kopeks':0,'cogs_kopeks':0})
        for row in rows:
            totals[row['category']]+=row['amount_kopeks']
            if row['posting_id']:
                postings[row['posting_id']]+=row['amount_kopeks']
            per_sku[row['sku']]['amount_kopeks']+=row['amount_kopeks']
            per_sku[row['sku']]['cogs_kopeks']+=costs.get(row['event_id'],0)
        actual=sum(totals.values()); difference=None; issues=[]
        if reference:
            expected=json.loads(reference['payload']); difference=actual-expected['total']
            if difference:
                issues.append('TOTAL_MISMATCH')
            if any(totals[k]!=expected['categories'].get(k,0) for k in CATEGORIES):
                issues.append('CATEGORY_MISMATCH')
            if expected['postings'] and any(postings[k]!=expected['postings'].get(k,0) for k in set(postings)|set(expected['postings'])):
                issues.append('POSTING_MISMATCH')
        if quarantine:
            issues.append('QUARANTINE')
        if invalid:
            issues.append('INVALID_REVERSAL')
        if issues:
            status='MISMATCH'
        elif not report['complete']:
            status='PARTIAL'
        elif not reference or not rows:
            status='CHECKING'
        elif now-datetime.fromisoformat(reference['checked_at'])>timedelta(hours=24):
            status='STALE'
        else:
            status='VERIFIED'
        ready=status=='VERIFIED' and not missing
        for sku,item in per_sku.items():
            item['contribution_kopeks']=item['amount_kopeks']-item['cogs_kopeks'] if ready and sku else None
            if sku in missing:
                item['cogs_kopeks']=None
        # Unassigned expenses are NOT silently distributed over SKU.
        return {'report_id':rid,'provider':report['provider'],'start':report['start_date'],'end':report['end_date'],
                'status':status,'label':LABELS[status],'event_count':len(rows),'actual_kopeks':actual,
                'difference_kopeks':difference,'category_kopeks':dict(totals),'issues':issues,
                'quarantine':quarantine,'missing_cogs':missing,'sku':dict(per_sku),
                'contribution_kopeks':actual-sum(costs.values()) if ready else None,
                'net_profit_kopeks':None,'verified_at':reference['checked_at'] if reference else None,
                'reference_id':reference['reference_id'] if reference else None,
                'reference_sha256':reference['reference_hash'] if reference else None,
                'imported_at':report['imported_at'],'contract':CONTRACT,
                'note':'Локальная нормализованная сверка. Вклад после учтённых расходов; налоги и полнота расходов не подтверждены. '
                       'Отчёты могут пересекаться: не складывайте их итоги.'}

    def overview(self,store):
        with connect(self.path) as db:
            self._store(db,store)
            reports=[r['report_id'] for r in db.execute('SELECT report_id FROM finance_reports WHERE store_id=? ORDER BY imported_at DESC',(store,))]
            amounts=[r[0] for r in db.execute('SELECT amount_kopeks FROM finance_sources WHERE store_id=?',(store,))]
            events=(len(amounts),sum(amounts))
            batches=[dict(r) for r in db.execute('SELECT * FROM finance_batches WHERE store_id=?',(store,))]
        return {'reports':[self.report(store,rid) for rid in reports],'unique_events':events[0],
                'unique_accruals_kopeks':events[1],'batches':batches,'net_profit_kopeks':None}


def browser_json(value, money=False):
    """Preserve exact integers in JavaScript, including totals above 2**53."""
    if isinstance(value,dict):
        return {k:browser_json(v,money or k.endswith('_kopeks')) for k,v in value.items()}
    if isinstance(value,list):
        return [browser_json(v,money) for v in value]
    if money and type(value) is int:
        return str(value)
    return value
