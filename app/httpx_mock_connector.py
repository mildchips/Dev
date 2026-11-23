"""Exercise real HTTP header/JSON construction through httpx.MockTransport only.

NO NETWORK SUPPORT: this class cannot accept a real httpx transport and never
creates a default httpx.Client.  For future live connection write a separate
reviewed module with its own security gate and production-grade rate limits.
"""
from __future__ import annotations

from typing import Callable

from .offline_sync import MockResponse, ScriptedTransport, ALLOWED, ContractError

HOSTS = {
    'WB': 'https://finance-api.wildberries.ru',
    'Ozon': 'https://api-seller.ozon.ru',
}


class HttpxMockTransport(ScriptedTransport):
    def __init__(self, handler: Callable):
        import httpx
        super().__init__([])
        # Explicitly use MockTransport. Even if the handler wants to call an
        # external service, the ONLY HTTP client here is backed by MockTransport.
        self.client = httpx.Client(transport=httpx.MockTransport(handler),
                                   timeout=httpx.Timeout(10.0), follow_redirects=False)

    def request(self, platform, path, body, credential):
        import httpx
        if path not in ALLOWED.get(platform,set()):
            raise ContractError('Endpoint not approved')
        if credential.platform != platform:
            raise ContractError('Credential does not match provider')
        headers={'Accept':'application/json','Content-Type':'application/json'}
        if platform == 'Ozon':
            if not credential.client_id:
                raise ContractError('Ozon Client-Id is missing')
            headers['Client-Id']=credential.client_id
            headers['Api-Key']=credential.secret
        elif platform == 'WB':
            headers['Authorization']=credential.secret
        self.calls.append((platform,path,dict(body)))  # NEVER include headers.
        try:
            res=self.client.post(HOSTS[platform]+path,json=body,headers=headers)
        except httpx.TransportError as exc:
            raise TimeoutError('Mock network failure (details suppressed)') from None
        if res.status_code==204:
            return MockResponse(204,None)
        if int(res.headers.get('Content-Length',0)) > 10_000_000 or len(res.content)>10_000_000:
            raise ContractError('Provider response exceeds limit')
        # Avoid parsing potentially sensitive error bodies.
        payload=None
        if res.status_code==200:
            try:
                payload=res.json()
            except ValueError:
                raise ContractError('Invalid JSON from provider') from None
        retry_after=None
        if res.status_code in (429, 503) and 'Retry-After' in res.headers:
            try:
                retry_after=float(res.headers['Retry-After'])
            except ValueError:
                retry_after=None
        return MockResponse(res.status_code,payload,retry_after)

    def close(self):
        self.client.close()
