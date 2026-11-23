"""Short-lived, signed browser sessions for the single-admin prototype."""

import hashlib
import hmac
import re
import time

SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_COOKIE_NAME = "clinical_scribe_session"


def create_session_token(secret: str, now: int | None = None) -> str:
    expires_at = (now if now is not None else int(time.time())) + SESSION_TTL_SECONDS
    payload = str(expires_at)
    signature = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def is_valid_session_token(secret: str, token: str | None, now: int | None = None) -> bool:
    if not token:
        return False
    try:
        payload, signature = token.split(".", 1)
        expires_at = int(payload)
    except (ValueError, AttributeError):
        return False

    current_time = now if now is not None else int(time.time())
    if expires_at <= current_time or expires_at > current_time + SESSION_TTL_SECONDS:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False

    expected = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.encode("ascii"), expected.encode("ascii"))
