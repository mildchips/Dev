"""Guarded READ ONLY HTTP connector. Default: network DISABLED.

There is NO network request until ALL of these hold:
  1. Explicit environment opt-in AI_PROFIT_ALLOW_LIVE=EXPLICIT_READ_ONLY;
  2. A short-lived per-credential/store/provider/endpoint permit exists;
  3. Vault, stop policies, token expiry, endpoint/JSON contracts pass;
  4. Durable per-store provider quota slot is available.

Never use in public SaaS: this is a local single-operator bridge only.
NO financial aggregates/profit recognized here: only safe staging.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from .integration_security import Vault, SecurityError, Stopped
from .offline_sync import (
    ALLOWED, OZON_BY_DAY, WB_DETAILS, MockResponse,
    ContractError, ExternalAPIError, RETRYABLE,
)

LIVE_OPT_IN_ENV = 'AI_PROFIT_ALLOW_LIVE'
LIVE_OPT_IN_VALUE = 'EXPLICIT_READ_ONLY'
HOSTS = {
    'Ozon': 'https://api-seller.ozon.ru',
    'WB': 'https://finance-api.wildberries.ru',
}
MAX_RESPONSE_BYTES = 10_000_000
MAX_WAIT_SECONDS = 180


class LiveDisabled(SecurityError):
    """A live network call is not authorized."""


class QuotaDeferred(RuntimeError):
    """Cannot get a quota slot within the request's max waiting window."""


def live_enabled() -> bool:
    return os.environ.get(LIVE_OPT_IN_ENV) == LIVE_OPT_IN_VALUE


def _strict_body(provider: str, path: str, body: dict[str, Any]) -> None:
    """Fail closed on unexpected fields to avoid unreviewed API operations."""
    if path not in ALLOWED.get(provider, set()) or path not in (OZON_BY_DAY, WB_DETAILS):
        raise SecurityError('Endpoint is not approved for live READ ONLY')
    if not isinstance(body, dict):
        raise ContractError('Request body must be a JSON object')
    if provider == 'Ozon':
        if set(body) != {'date', 'last_id'}:
            raise ContractError('Unrecognized Ozon request fields')
        if not isinstance(body['date'], str):
            raise ContractError('Ozon date must be ISO date')
        try:
            if date.fromisoformat(body['date']).isoformat() != body['date']:
                raise ValueError()
        except ValueError:
            raise ContractError('Invalid Ozon date') from None
        cur = body['last_id']
        if not isinstance(cur, str) or len(cur) > 1024:
            raise ContractError('Invalid Ozon cursor')
    else:
        expected = {'dateFrom', 'dateTo', 'period', 'rrdId', 'limit'}
        if set(body) != expected:
            raise ContractError('Unrecognized WB request fields')
        try:
            start, end = date.fromisoformat(body['dateFrom']), date.fromisoformat(body['dateTo'])
            if (start.isoformat()!=body['dateFrom'] or end.isoformat()!=body['dateTo']):
                raise ValueError()
        except (ValueError, TypeError):
            raise ContractError('Invalid WB dates') from None
        if end < start or body['period'] not in ('daily', 'weekly'):
            raise ContractError('Invalid WB period')
        if (not isinstance(body['rrdId'], int) or isinstance(body['rrdId'], bool) or
                not 0 <= body['rrdId'] < 2 ** 63):
            raise ContractError('Invalid WB rrdId')
        if not isinstance(body['limit'], int) or isinstance(body['limit'], bool) or not 1 <= body['limit'] <= 100000:
            raise ContractError('Invalid WB limit')


def _retry_after(value: str | None, now: Callable[[], datetime] | None = None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
        if math.isfinite(seconds) and 0 <= seconds <= 3600:
            return seconds
    except (ValueError, TypeError):
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return None
        seconds = (dt - (now or (lambda: datetime.now(timezone.utc)))()).total_seconds()
        return max(0.0, min(3600.0, seconds))
    except (ValueError, TypeError, OverflowError):
        return None


class ReadOnlyHTTPTransport:
    """Exact-host pinned API request construction, no redirects/proxies by default.

    Inject `client=httpx.Client(transport=httpx.MockTransport(...))` in tests.
    This object never grants authorization: LiveGateway enforces the gates.
    """

    def __init__(self, client: httpx.Client | None = None):
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            verify=True, trust_env=False, follow_redirects=False,
        )

    def request(self, provider: str, path: str, body: dict, credential: Any) -> MockResponse:
        _strict_body(provider, path, body)
        if provider != credential.platform:
            raise SecurityError('Credential platform mismatch')
        headers = {'Accept':'application/json','Content-Type':'application/json'}
        if provider == 'Ozon':
            if not credential.client_id:
                raise SecurityError('Ozon Client-Id missing')
            headers['Client-Id'] = credential.client_id
            headers['Api-Key'] = credential.secret
        else:
            headers['Authorization'] = credential.secret
        # Fixed host + validated path; no user-controlled URL/hostname.
        url = HOSTS[provider] + path
        try:
            with self.client.stream('POST', url, json=body, headers=headers, follow_redirects=False) as response:
                if response.status_code == 204:
                    return MockResponse(204, None)
                try:
                    if int(response.headers.get('Content-Length','0')) > MAX_RESPONSE_BYTES:
                        raise ContractError('Provider response exceeds maximum size')
                except ValueError:
                    pass
                raw = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    raw.extend(part)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ContractError('Provider response exceeds maximum size')
                if response.status_code == 200:
                    # Never return raw provider error details to app/user.
                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeError):
                        raise ContractError('Provider returned invalid JSON') from None
                else:
                    payload = None
                return MockResponse(response.status_code, payload,
                                    _retry_after(response.headers.get('Retry-After')))
        except (httpx.TimeoutException, httpx.TransportError):
            raise TimeoutError('Provider transport failure (details suppressed)') from None

    def close(self):
        if self._owns_client:
            self.client.close()


class DurableQuota:
    """Local cross-process quota reservation in SQLite.

    For WB: one request per 60s *per store*, across both finance routes.
    For Ozon: conservative 2s per store, NOT an official universal limit.
    Production should use a central Redis/DB quota shared by all workers.
    """

    def __init__(self, path: Path | str, min_interval: dict[str, float] | None = None,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise SecurityError('Quota file symlink forbidden')
        self.clock = clock
        self.min_interval = min_interval or {'WB': 60.0, 'Ozon': 2.0}
        with self._conn() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS quota_slots
                      (provider TEXT NOT NULL, store_id TEXT NOT NULL,
                       next_at REAL NOT NULL, PRIMARY KEY(provider,store_id))''')
        if os.name == 'posix':
            self.path.chmod(0o600)

    @contextmanager
    def _conn(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        try:
            db.execute('PRAGMA busy_timeout=10000')
            yield db
        finally:
            db.close()

    def reserve(self, provider: str, store_id: str, *, max_wait: float = MAX_WAIT_SECONDS) -> float:
        """Return wait until the reserved slot; atomic among local processes."""
        if provider not in HOSTS or not store_id:
            raise SecurityError('Invalid quota identity')
        gap = float(self.min_interval.get(provider, 60.0))
        if not math.isfinite(gap) or gap < 0:
            raise ValueError('Invalid quota interval')
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT next_at FROM quota_slots WHERE provider=? AND store_id=?',
                            (provider, store_id)).fetchone()
            now = self.clock()
            due = max(now, row[0] if row else now)
            if due - now > max_wait:
                c.rollback()
                raise QuotaDeferred('Quota capacity unavailable within maximum waiting window')
            c.execute('INSERT INTO quota_slots VALUES(?,?,?) ON CONFLICT(provider,store_id) '
                      'DO UPDATE SET next_at=excluded.next_at', (provider, store_id, due + gap))
            c.commit()
        return max(0.0, due - now)


@dataclass(frozen=True)
class LiveGateway:
    vault: Vault
    transport: ReadOnlyHTTPTransport
    quota: DurableQuota
    attempts: int = 3
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time
    max_wait: float = MAX_WAIT_SECONDS

    def __post_init__(self):
        if not 1 <= self.attempts <= 5:
            raise ValueError('Retry attempts must be 1..5')
        if not 0 < self.max_wait <= 3600:
            raise ValueError('Invalid max_wait')

    def _ensure_enabled(self):
        if not live_enabled():
            raise LiveDisabled('LIVE is OFF. No network request was made.')

    def _wait_interruptible(self, seconds: float, cid: str):
        if seconds > self.max_wait:
            raise QuotaDeferred('Request deferred: per-store quota would wait too long')
        remaining = seconds
        while remaining > 0:
            self._ensure_enabled()
            self.vault.check(cid)
            delta = min(1.0, remaining)
            self.sleeper(delta)
            remaining -= delta
        self._ensure_enabled()
        self.vault.check(cid)

    def request(self, credential_id: str, store_id: str, provider: str,
                path: str, body: dict[str, Any]) -> MockResponse:
        _strict_body(provider, path, body)
        self._ensure_enabled()
        if not self.vault.is_credential_for(credential_id, store_id, provider):
            raise SecurityError('Credential/store/provider mismatch')
        self.vault.require_live_expiry(credential_id)
        for attempt in range(self.attempts):
            self._ensure_enabled()
            self.vault.check(credential_id)
            due = self.quota.reserve(provider, store_id, max_wait=self.max_wait)
            self._wait_interruptible(due, credential_id)
            # Permit consumed atomically immediately BEFORE the network dispatch.
            self.vault.consume_live_permit(credential_id, store_id, provider, path)
            try:
                res = self.vault.with_authorized(
                    credential_id,
                    lambda c: self.transport.request(provider,path,body,c)
                )
            except (TimeoutError, ConnectionError):
                if attempt + 1 == self.attempts:
                    raise ExternalAPIError(0) from None
                self._wait_interruptible(min(2**attempt, 8), credential_id)
                continue
            if res.status in (401,403):
                self.vault.set_stop('credential', credential_id, True, 'provider_auth_or_scope_rejected')
                raise ExternalAPIError(res.status)
            if res.status in RETRYABLE:
                if attempt + 1 == self.attempts:
                    raise ExternalAPIError(res.status)
                delay = res.retry_after if res.retry_after is not None else min(2**attempt, 16)
                if not isinstance(delay,(float,int)) or not math.isfinite(delay) or delay < 0:
                    raise ContractError('Provider sent invalid retry interval')
                self._wait_interruptible(min(delay,3600), credential_id)
                continue
            if res.status in (200,204):
                return res
            raise ExternalAPIError(res.status)
        raise AssertionError('Unreachable')
