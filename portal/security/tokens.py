"""Access tokens (ES256 JWT), opaque refresh tokens, and one-time codes.

- Access tokens are short-lived JWTs signed with the first configured EC P-256 key. Every
  configured key is accepted for verification (selected by `kid`), so keys rotate without signing
  everyone out.
- Refresh tokens and one-time codes are random; only an HMAC-SHA256 under the server secret is
  stored, so a database leak doesn't reveal usable tokens.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from portal.config import get_portal_settings

ALGORITHM = "ES256"
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.+?-----END [A-Z ]*PRIVATE KEY-----", re.S)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class SigningKey:
    kid: str
    private: ec.EllipticCurvePrivateKey

    @property
    def public(self) -> ec.EllipticCurvePublicKey:
        return self.private.public_key()


def _kid(key: ec.EllipticCurvePrivateKey) -> str:
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()[:12]).decode().rstrip("=")


@lru_cache
def signing_keys() -> tuple[SigningKey, ...]:
    s = get_portal_settings()
    text = s.jwt_private_keys
    if s.jwt_private_keys_file:
        text = Path(s.jwt_private_keys_file).read_text()
    pems = _PEM.findall(text.replace("\\n", "\n"))
    if not pems:
        raise ConfigError("No JWT signing key configured (PORTAL_JWT_PRIVATE_KEYS). "
                          "Generate one with: python -m portal.cli genkeys")
    keys = []
    for pem in pems:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
            raise ConfigError("JWT keys must be EC P-256 (ES256) private keys")
        keys.append(SigningKey(_kid(key), key))
    return tuple(keys)


@lru_cache
def _secret() -> bytes:
    raw = get_portal_settings().secret_key
    if len(raw) < 32:
        raise ConfigError("PORTAL_SECRET_KEY must be at least 32 characters of random data. "
                          "Generate one with: python -m portal.cli genkeys")
    return raw.encode()


def check_config() -> None:
    """Fail at start-up, not at first sign-in, if secrets are missing."""
    signing_keys()
    _secret()


def generate_private_key_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


# --- access tokens -------------------------------------------------------------------------------

def issue_access_token(user_id: uuid.UUID, session_id: uuid.UUID) -> tuple[str, int]:
    s = get_portal_settings()
    key = signing_keys()[0]
    now = datetime.now(timezone.utc)
    ttl = s.access_token_minutes * 60
    claims = {
        "iss": s.jwt_issuer,
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": secrets.token_urlsafe(12),
    }
    token = jwt.encode(claims, key.private, algorithm=ALGORITHM, headers={"kid": key.kid})
    return token, ttl


def decode_access_token(token: str) -> dict:
    """Verify signature, expiry and issuer. Raises jwt.InvalidTokenError."""
    header = jwt.get_unverified_header(token)
    if header.get("alg") != ALGORITHM:
        raise jwt.InvalidAlgorithmError("unexpected algorithm")
    key = next((k for k in signing_keys() if k.kid == header.get("kid")), None)
    if key is None:
        # A token from a retired key (rotation, or a restart with ephemeral keys) is just an invalid token:
        # InvalidKeyError isn't an InvalidTokenError, so callers would turn it into a 500.
        raise jwt.InvalidSignatureError("unknown key")
    return jwt.decode(token, key.public, algorithms=[ALGORITHM], issuer=get_portal_settings().jwt_issuer,
                      options={"require": ["exp", "iat", "sub", "sid", "iss"]}, leeway=10)


# --- opaque secrets ------------------------------------------------------------------------------

def keyed_hash(value: str, purpose: str) -> str:
    """HMAC-SHA256 hex digest, domain-separated by purpose."""
    return hmac.new(_secret(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def new_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def otp_hash(email: str, challenge_id: uuid.UUID, code: str) -> str:
    # Bound to the email and challenge, so a code can't be replayed against another challenge.
    return keyed_hash(f"{email}|{challenge_id}|{code}", "otp")


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
