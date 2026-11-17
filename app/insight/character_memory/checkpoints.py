"""Lossless compact checkpoints; avoid rewriting megabytes on every reservation.

Cache keys, attempt counts and validation remain unchanged. Legacy JSON entries
are accepted so an interrupted analysis can resume across deployment.
"""

import base64
import json
import zlib
from typing import Any

CODEC = "zlib-json-v1"


def encode(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("codec") == CODEC:
        return value
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return {"codec": CODEC, "data": base64.b64encode(zlib.compress(raw)).decode("ascii")}


def decode(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("codec") != CODEC:
        return value
    result = json.loads(zlib.decompress(base64.b64decode(value["data"], validate=True)))
    if not isinstance(result, dict):
        raise ValueError("Invalid checkpoint object")
    return result
