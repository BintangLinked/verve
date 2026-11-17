"""Payload fingerprint for idempotency.

Purpose: distinguishes "the same request repeated" (same fingerprint -> answered again) from
"the same request_id used for different content" (different fingerprint -> 409 IDEMPOTENCY_KEY_REUSED).

Relations: used by services/sales.py and services/replenishment.py so both apply the same rules.
"""
import hashlib


def fingerprint(*parts: object) -> str:
    """SHA-256 of the payload parts that determine the meaning of a request."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
