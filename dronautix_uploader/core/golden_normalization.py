"""Normalization helpers for Golden Master comparisons."""

from __future__ import annotations

import json
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

VOLATILE_KEYS = {
    "datum",
    "last_updated",
    "published_at",
    "deleted_at",
    "disabled_at",
}

VOLATILE_ID_KEYS = {
    "id",
    "project_id",
}

UUID_OR_SHORT_ID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


def strip_cloudjs_wrapper(text: str) -> str:
    payload = text.strip()
    if payload.startswith("cloud.js"):
        payload = payload[len("cloud.js") :].strip()
    if payload.startswith("="):
        payload = payload[1:].strip()
    return payload.rstrip(";").strip()


def load_json_or_cloudjs(text: str) -> Any:
    return json.loads(strip_cloudjs_wrapper(text))


def _round_float(value: float, digits: int) -> float:
    decimal_value = Decimal(str(value)).quantize(
        Decimal("1").scaleb(-digits),
        rounding=ROUND_HALF_UP,
    )
    return float(decimal_value)


def normalize_value(value: Any, float_digits: int = 8) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key in VOLATILE_KEYS:
                normalized[key] = "<volatile>"
            elif key in VOLATILE_ID_KEYS and isinstance(item, str) and UUID_OR_SHORT_ID_RE.match(item):
                normalized[key] = "<id>"
            else:
                normalized[key] = normalize_value(item, float_digits=float_digits)
        return normalized

    if isinstance(value, list):
        return [normalize_value(item, float_digits=float_digits) for item in value]

    if isinstance(value, float):
        return _round_float(value, float_digits)

    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize_value(value),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def canonical_json_text(text: str) -> str:
    return canonical_json(json.loads(text))


def canonical_cloudjs_text(text: str) -> str:
    return canonical_json(load_json_or_cloudjs(text))
