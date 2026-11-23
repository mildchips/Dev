"""Envelope mechanism with mandatory injected KMS. No cloud adapter or fallback."""
from __future__ import annotations

import base64
import json
import secrets
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KMS(Protocol):
    def wrap(self, key_id: str, plaintext_key: bytes, context: bytes) -> bytes: ...
    def unwrap(self, key_id: str, wrapped_key: bytes, context: bytes) -> bytes: ...


def b64(value):
    return base64.b64encode(value).decode('ascii')


def unb64(value):
    return base64.b64decode(value,validate=True)


def context(tenant: str, store: str, provider: str, version: int, key_id: str):
    if not tenant or not store or provider not in ('WB','Ozon') or type(version) is not int or version<1 or not key_id:
        raise ValueError('invalid encryption scope')
    return json.dumps([tenant,store,provider,version,key_id],separators=(',',':')).encode()


def seal(kms: KMS, *, tenant, store, provider, version, key_id, plaintext: bytes):
    aad=context(tenant,store,provider,version,key_id)
    dek, nonce=secrets.token_bytes(32),secrets.token_bytes(12)
    wrapped=kms.wrap(key_id,dek,aad)  # Failure propagates; no plaintext persistence.
    return {'schema':'aipo.envelope/1','key_id':key_id,'nonce':b64(nonce),
            'wrapped':b64(wrapped),'ciphertext':b64(AESGCM(dek).encrypt(nonce,plaintext,aad))}


def unseal(kms: KMS, envelope, *, tenant, store, provider, version, key_id):
    if envelope.get('schema')!='aipo.envelope/1' or envelope.get('key_id')!=key_id:
        raise ValueError('invalid envelope')
    aad=context(tenant,store,provider,version,key_id)
    dek=kms.unwrap(key_id,unb64(envelope['wrapped']),aad)
    return AESGCM(dek).decrypt(unb64(envelope['nonce']),unb64(envelope['ciphertext']),aad)
