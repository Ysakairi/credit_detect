"""Extract SQL / JSON payloads from LLM text."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple


_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def extract_sql(text: str) -> str:
    if not text:
        return ""
    fenced = _SQL_FENCE.findall(text)
    if fenced:
        return fenced[0].strip().rstrip(";")
    stripped = text.strip()
    if stripped.lower().startswith(("select", "with")):
        return stripped.strip().rstrip(";")
    return stripped.replace("```sql", "").replace("```", "").strip().rstrip(";")


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    candidates = _JSON_FENCE.findall(text)
    raw = candidates[-1] if candidates else text
    match = _JSON_OBJECT.search(raw)
    if not match:
        return None
    blob = match.group(0)
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        blob = blob.replace("true", "true").replace("false", "false")
        blob = re.sub(r",\s*}", "}", blob)
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def parse_reflection(text: str) -> Tuple[bool, int, str]:
    parsed = extract_json(text) or {}
    score = parsed.get("score", parsed.get("reflection_score", 0))
    try:
        score_i = int(score)
    except (TypeError, ValueError):
        score_i = 0
    if "is_sufficient" in parsed:
        is_sufficient = bool(parsed.get("is_sufficient"))
    else:
        is_sufficient = score_i >= 80
    critique = str(parsed.get("critique") or text or "").strip()
    return is_sufficient, score_i, critique


def numbered_plan(text: str) -> list[str]:
    steps = []
    for line in (text or "").splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
        if cleaned:
            steps.append(cleaned)
    return steps or [text.strip()] if text and text.strip() else []
