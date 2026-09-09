"""Reject mutating BigQuery statements before they reach the warehouse."""

from __future__ import annotations

import re
from typing import Optional


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|"
    r"EXPORT|LOAD|CALL|EXECUTE\s+IMMEDIATE|BEGIN\s+TRANSACTION)\b",
    re.IGNORECASE,
)
_COMMENTS = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


class SqlGuardError(ValueError):
    pass


def sanitize_sql(sql: str, max_rows: int = 200) -> str:
    if not sql or not sql.strip():
        raise SqlGuardError("SQL が空です")

    stripped = _COMMENTS.sub(" ", sql)
    stripped = stripped.replace(";", " ")
    stripped = _WHITESPACE.sub(" ", stripped).strip()

    if _FORBIDDEN.search(stripped):
        raise SqlGuardError("DML/DDL は許可されていません（SELECT/WITH のみ）")

    head = stripped.split(None, 1)[0].lower()
    if head not in {"select", "with"}:
        raise SqlGuardError("先頭は SELECT または WITH である必要があります")

    # LIMIT が無い場合のみ付与。既存 LIMIT が上限を超える場合は書き換えない
    # （サブクエリ内 LIMIT を壊さないため、末尾にだけ安全なキャップを足す）。
    if not re.search(r"\blimit\s+\d+\s*$", stripped, re.IGNORECASE):
        stripped = f"{stripped} LIMIT {int(max_rows)}"
    return stripped


def preview(sql: Optional[str], n: int = 400) -> str:
    if not sql:
        return ""
    sql = sql.strip()
    return sql if len(sql) <= n else sql[:n] + "…"
