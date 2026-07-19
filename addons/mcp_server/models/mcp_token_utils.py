# -*- coding: utf-8 -*-
"""Shared crypto helpers for tokens/secrets.

All bearer secrets (OAuth access/refresh tokens, authorization codes, client
secrets, confirmation tokens) are stored *hashed at rest*. We compare using a
constant-time digest comparison.
"""
import hashlib
import hmac
import secrets

# A generous entropy budget for opaque tokens.
_TOKEN_BYTES = 32


def generate_token(nbytes=_TOKEN_BYTES):
    """Return a URL-safe opaque secret."""
    return secrets.token_urlsafe(nbytes)


def hash_secret(secret):
    """One-way hash for storage. SHA-256 is appropriate for high-entropy,
    randomly-generated opaque tokens (not user passwords)."""
    if secret is None:
        return False
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret, hashed):
    if not secret or not hashed:
        return False
    return hmac.compare_digest(hash_secret(secret), hashed)


def constant_time_equals(a, b):
    return hmac.compare_digest(a or "", b or "")
