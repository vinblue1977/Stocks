"""Web Push to installed home-screen apps (iOS 16.4+, other browsers too): RFC 8291 encryption, RFC 8292 VAPID.

Built on `cryptography` only. The payload is encrypted to the browser's key, and each request is signed with
our VAPID key so the push service knows it comes from this app.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Push services we send to. The server POSTs to the subscription's endpoint, so only known hosts are allowed.
PUSH_HOSTS = ("web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com")
RECORD_SIZE = 4096


class Gone(Exception):
    """The subscription no longer exists (404/410): delete it."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host in PUSH_HOSTS or host.endswith(".notify.windows.com"))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None, server_key: Any = None) -> bytes:
    """aes128gcm body for one record (RFC 8188 / 8291)."""
    ua_public = unb64url(p256dh)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(unb64url(auth), shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02: last (only) record
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


def vapid_header(endpoint: str, private_key_b64: str, subject: str) -> str:
    """`Authorization: vapid t=<JWT>, k=<public key>` for this push service."""
    key = ec.derive_private_key(int.from_bytes(unb64url(private_key_b64), "big"), ec.SECP256R1())
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": subject}
    signing_input = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." + b64url(json.dumps(claims).encode())
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing_input + "." + b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={b64url(_public_bytes(key.public_key()))}"


def send(subscription: dict[str, Any], message: dict[str, Any], private_key_b64: str, subject: str, ttl: int = 86400) -> None:
    """Deliver one notification. Raises Gone when the subscription is expired or revoked."""
    endpoint = subscription["endpoint"]
    if not allowed_endpoint(endpoint):
        raise ValueError("Not a known push service")
    body = encrypt(json.dumps(message).encode(), subscription["keys"]["p256dh"], subscription["keys"]["auth"])
    request = urllib.request.Request(  # noqa: S310 - https to an allow-listed push service only
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": vapid_header(endpoint, private_key_b64, subject),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(ttl),
            "Urgency": "high",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5):  # noqa: S310 - short: runs inside the alert job
            return
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            raise Gone(endpoint) from exc
        raise
