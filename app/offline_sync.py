"""Offline-only WB/Ozon connector contract runner.

GUARDS: never uses networking; transports are injected scripted doubles, keys
are fake in demos; source financial rows are quarantined until reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Protocol

from .integration_security import Vault, Stopped, SecurityError

OZON_BY_DAY = '/v1/finance/accrual/by-day'
WB_DETAILS = '/api/finance/v1/sales-reports/detailed'
ALLOWED = {'Ozon': {OZON_BY_DAY}, 'WB': {WB_DETAILS}}
RETRYABLE = {429, 500, 502, 503, 504}


class ContractError(RuntimeError):
    pass


class ExternalAPIError(RuntimeError):
    """Redacted provider-side error: NEVER include body or headers."""

    def __init__(self, status: int):
        super().__init__(f'Provider HTTP {status} (response suppressed)')
        self.status = status


@dataclass(frozen=True)
class MockResponse:
    status: int
    payload: Any
    retry_after: float | None = None


class OfflineTransport(Protocol):
    def request(self, platform: str, path: str, body: dict[str, Any], credential: Any) -> MockResponse:
        ...


class ScriptedTransport:
    """Fixture-only transport. No httpx/requests/socket import; never calls Internet."""

    def __init__(self, replies: list[Any]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, platform, path, body, credential):
        self.calls.append((platform, path, dict(body)))
        if not self.replies:
            raise ContractError('Missing mocked response; unexpected request')
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if not isinstance(nxt, MockResponse):
            raise ContractError('Mock reply must be MockResponse')
        return nxt



class SimulatedPacer:
    """Deterministic virtual-time request pacing, for OFFLINE verification only.

    Real deployment requires distributed limits per provider/credential/endpoint.
    """

    def __init__(self, min_interval: dict[str, float] | None = None,
                 sleep: Callable[[float], None] | None = None):
        self.min_interval = min_interval or {'WB': 60.0, 'Ozon': 1.0}
        self.sleep = sleep or (lambda _: None)
        self.clock = 0.0
        self.next_due: dict[tuple[str, str], float] = {}

    def wait(self, credential_id: str, platform: str):
        gap = float(self.min_interval.get(platform, 0))
        if not math.isfinite(gap) or gap < 0:
            raise ContractError('Invalid quota interval')
        key = (credential_id, platform)
        due = self.next_due.get(key, 0)
        if self.clock < due:
            delay = due - self.clock
            self.sleep(delay)
            self.clock += delay
        self.next_due[key] = self.clock + gap


class Gateway:
    """All marketplace calls gated by Vault stop/credentials and endpoint allowlist.

    This object ONLY accepts ScriptedTransport; no production HTTP transport.
    `sleep` is an injected function in tests; real sleeps avoided in CI.
    """

    def __init__(self, vault: Vault, transport: ScriptedTransport,
                 sleep: Callable[[float], None] | None = None, attempts: int = 3,
                 pacer: SimulatedPacer | None = None):
        if not isinstance(transport, ScriptedTransport):
            raise SecurityError('OFFLINE ONLY: non-scripted transport prohibited')
        if not 1 <= attempts <= 5:
            raise ValueError('attempts must be 1..5')
        self.vault, self.transport, self.sleep, self.attempts = vault, transport, sleep or (lambda _: None), attempts
        self.pacer = pacer

    def request(self, credential_id: str, store_id: str, platform: str, path: str, body: dict) -> MockResponse:
        if path not in ALLOWED.get(platform, set()):
            raise SecurityError('Unsupported or obsolete API endpoint')
        if not isinstance(body, dict):
            raise ContractError('JSON request must be an object')
        for attempt in range(self.attempts):
            # Fresh vault check/decrypt at every attempt; no direct credential escape.
            try:
                if self.pacer is not None:
                    self.pacer.wait(credential_id,platform)
                response = self.vault.with_authorized(
                    credential_id,
                    lambda c: self.transport.request(platform, path, body, c)
                    if c.platform == platform and c.store_id == store_id else _deny_provider()
                )
            except (TimeoutError, ConnectionError):
                if attempt + 1 == self.attempts:
                    raise ExternalAPIError(0) from None
                self.sleep(min(2 ** attempt, 8))
                continue
            if not isinstance(response, MockResponse):
                raise ContractError('Unexpected transport result')
            if response.status in RETRYABLE:
                if attempt + 1 == self.attempts:
                    raise ExternalAPIError(response.status)
                delay = response.retry_after if response.retry_after is not None else 2 ** attempt
                if not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
                    raise ContractError('Invalid Retry-After')
                self.sleep(min(float(delay), 120.0))
                continue
            if response.status in (200, 204):
                return response
            # 400/401/402/403/404 are terminal; never retry auth errors.
            raise ExternalAPIError(response.status)
        raise AssertionError('unreachable')


def _deny_provider():
    raise SecurityError('Credential/provider mismatch')


def _json_stable(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _money(value: Any) -> str:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ContractError('Amount must be a decimal string/number')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ContractError('Invalid decimal amount') from None
    if not result.is_finite() or abs(result) > Decimal('1000000000000'):
        raise ContractError('Out-of-range or nonfinite amount')
    if result.as_tuple().exponent < -2:
        raise ContractError('Fractional kopeks are not accepted')
    return str(result.quantize(Decimal('0.01')))


def extract_ozon(payload: Any):
    if not isinstance(payload, dict) or not isinstance(payload.get('accruals'), list):
        raise ContractError('Ozon by-day: expected accruals array')
    cursor = payload.get('last_id', '')
    if not isinstance(cursor, str) or len(cursor) > 1024:
        raise ContractError('Ozon invalid last_id')
    rows = []
    for raw in payload['accruals']:
        if not isinstance(raw, dict) or not isinstance(raw.get('accrual_id'), (str, int)):
            raise ContractError('Ozon missing accrual_id')
        rid = str(raw['accrual_id'])
        if not rid or len(rid) > 160:
            raise ContractError('Ozon invalid accrual_id')
        # Preserve only allowlisted fields. Do not infer profit from raw accruals.
        item = {'accrual_id': rid, 'accrued_category': raw.get('accrued_category', 'UNKNOWN')}
        if item['accrued_category'] not in ('POSTING','ITEM','NON_ITEM','UNKNOWN'):
            raise ContractError('Ozon unexpected category')
        if 'accrued' in raw:
            item['accrued'] = _money(raw['accrued'])
        if 'type_id' in raw:
            item['type_id'] = str(raw['type_id'])[:80]
        rows.append((rid, item))
    return rows, cursor


def extract_wb(payload: Any):
    if not isinstance(payload, list):
        raise ContractError('WB detailed: expected top-level array')
    rows = []
    cursor = None
    for raw in payload:
        if not isinstance(raw, dict) or not isinstance(raw.get('rrdId'), int) or isinstance(raw.get('rrdId'), bool):
            raise ContractError('WB missing integer rrdId')
        rrd = raw['rrdId']
        if rrd < 0 or rrd >= 2**63:
            raise ContractError('WB rrdId out of range')
        if cursor is not None and rrd <= cursor:
            raise ContractError('WB rrdId is not strictly increasing')
        cursor = rrd
        item = {'rrdId': str(rrd)}
        for field in ('reportId', 'nmId'):
            if field in raw and raw[field] is not None:
                item[field] = str(raw[field])
        for field in ('retailAmount','forPay','acquiringFee'):
            if field in raw and raw[field] is not None:
                item[field] = _money(raw[field])
        if raw.get('currency') is not None:
            if raw['currency'] != 'RUB':
                raise ContractError('Currency must be RUB in local MVP')
            item['currency'] = 'RUB'
        rows.append((str(rrd), item))
    return rows, cursor


class CheckpointStore:
    """Offline staging separate from financial ledger; atomic checkpoint per page."""

    def __init__(self, file: Path | str):
        self.path = Path(file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS pages (
                    store_id TEXT NOT NULL, provider TEXT NOT NULL, period TEXT NOT NULL,
                    cursor TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(store_id,provider,period));
                CREATE TABLE IF NOT EXISTS events (
                    store_id TEXT NOT NULL, provider TEXT NOT NULL, period TEXT NOT NULL,
                    event_id TEXT NOT NULL, source_hash TEXT NOT NULL, sanitized_json TEXT NOT NULL,
                    PRIMARY KEY(store_id,provider,event_id));
            ''')
        if __import__('os').name == 'posix':
            self.path.chmod(0o600)

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.execute('PRAGMA busy_timeout=15000')
        return conn

    def checkpoint(self, store: str, provider: str, period: str):
        with self._conn() as c:
            r = c.execute('SELECT cursor, done FROM pages WHERE store_id=? AND provider=? AND period=?',
                          (store,provider,period)).fetchone()
            return (r[0],bool(r[1])) if r else ('',False)

    def commit_page(self, store: str, provider: str, period: str, new_cursor: str,
                    rows: list[tuple[str,dict]], done: bool):
        with self._conn() as c:
            try:
                c.execute('BEGIN IMMEDIATE')
                for eid, clean in rows:
                    blob = _json_stable(clean)
                    digest = hashlib.sha256(blob.encode()).hexdigest()
                    old = c.execute('SELECT source_hash FROM events WHERE store_id=? AND provider=? AND event_id=?',
                                    (store, provider, eid)).fetchone()
                    if old:
                        if old[0] != digest:
                            raise ContractError('Conflicting source event; checkpoint unchanged')
                        continue
                    c.execute('INSERT INTO events VALUES(?,?,?,?,?,?)', (store,provider,period,eid,digest,blob))
                c.execute('INSERT INTO pages VALUES(?,?,?,?,?) ON CONFLICT(store_id,provider,period) '
                          'DO UPDATE SET cursor=excluded.cursor,done=excluded.done',
                          (store,provider,period,new_cursor,int(done)))
                c.commit()
            except Exception:
                c.rollback()
                raise

    def count(self, store: str):
        with self._conn() as c:
            return c.execute('SELECT COUNT(*) FROM events WHERE store_id=?',(store,)).fetchone()[0]


class OfflineSync:
    def __init__(self, vault: Vault, gateway: Gateway, staging: CheckpointStore, max_pages: int = 10):
        self.vault, self.gateway, self.staging, self.max_pages = vault,gateway,staging,max_pages
        if max_pages < 1 or max_pages > 1000:
            raise ValueError('max_pages must be 1..1000')

    def ozon_day(self, cid: str, store: str, day: str):
        date.fromisoformat(day)
        self.vault._store_valid(store,'Ozon')
        cursor, done = self.staging.checkpoint(store,'Ozon',day)
        if done:
            return {'status':'already_complete','events':self.staging.count(store)}
        seen = set()
        for _ in range(self.max_pages):
            response = self.gateway.request(cid,store,'Ozon',OZON_BY_DAY,{'date':day,'last_id':cursor})
            if response.status != 200:
                raise ContractError('Ozon HTTP 204 is not an accepted by-day response')
            rows, next_cursor = extract_ozon(response.payload)
            if next_cursor and (next_cursor == cursor or next_cursor in seen):
                raise ContractError('Ozon cursor loop')
            seen.add(cursor)
            # A nonempty cursor with empty rows is ambiguous; follow it instead of dropping data.
            finished = not next_cursor
            self.staging.commit_page(store,'Ozon',day,next_cursor,rows,finished)
            cursor = next_cursor
            if finished:
                return {'status':'complete','events':self.staging.count(store)}
        raise ContractError('Ozon max pages exceeded; checkpoint retained')

    def wb_period(self, cid: str, store: str, start: str, end: str):
        if date.fromisoformat(end) < date.fromisoformat(start):
            raise ValueError('End date before start date')
        self.vault._store_valid(store,'WB')
        period = f'{start}:{end}'
        cursor, done = self.staging.checkpoint(store,'WB',period)
        if done:
            return {'status':'already_complete','events':self.staging.count(store)}
        previous = int(cursor) if cursor else 0
        for _ in range(self.max_pages):
            response = self.gateway.request(cid,store,'WB',WB_DETAILS,
                {'dateFrom':start,'dateTo':end,'period':'weekly','rrdId':previous,'limit':1000})
            if response.status == 204:
                self.staging.commit_page(store,'WB',period,str(previous),[],True)
                return {'status':'complete','events':self.staging.count(store)}
            rows, next_id = extract_wb(response.payload)
            if not rows:
                raise ContractError('WB HTTP 200 with empty list; expected 204 terminal')
            if next_id is None or next_id <= previous:
                raise ContractError('WB cursor not advancing')
            self.staging.commit_page(store,'WB',period,str(next_id),rows,False)
            previous = next_id
        raise ContractError('WB max pages exceeded; checkpoint retained')
