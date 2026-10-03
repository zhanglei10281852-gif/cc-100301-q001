from __future__ import annotations

import re
from typing import Any

PHONE_RE = re.compile(r"(?<!\d)(1\d{10})(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[0-9Xx])(?!\d)")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mask_phone(value: str | None) -> str | None:
    if value is None or len(value) < 7:
        return value
    return value[:3] + "****" + value[-4:]


def mask_id_card(value: str | None) -> str | None:
    if value is None or len(value) < 10:
        return value
    return value[:6] + "********" + value[-4:]


def mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return value
    local, domain = value.split("@", 1)
    visible = local[:1]
    return visible + "***@" + domain


def sanitize_text(value: str) -> str:
    value = PHONE_RE.sub(lambda match: mask_phone(match.group(1)) or "", value)
    value = ID_CARD_RE.sub(lambda match: mask_id_card(match.group(0)) or "", value)
    value = EMAIL_RE.sub(lambda match: mask_email(match.group(0)) or "", value)
    return value


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = key.casefold()
            if lowered in {"password", "password_hash", "token", "token_digest", "secret", "authorization"}:
                result[key] = "***"
            elif lowered in {"phone", "contact", "mobile"} and isinstance(item, str):
                result[key] = mask_phone(item)
            elif lowered in {"id_card", "identity_number"} and isinstance(item, str):
                result[key] = mask_id_card(item)
            elif lowered == "email" and isinstance(item, str):
                result[key] = mask_email(item)
            else:
                result[key] = sanitize_payload(item)
        return result
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value
