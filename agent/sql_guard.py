"""LLM 生成 SQL を倉庫に届ける前に、破壊的文を拒否する。

【Agent Engine 上の位置づけ】
Agent Engine 上の Gemini は Text-to-SQL で INSERT/DELETE を混ぜることがある。
サービスアカウントはナレッジ UPSERT のため dataEditor を持つので、実行パス側で
SELECT/WITH 以外を落とさないと、調査エージェントが推論テーブルを壊せる。
スキャン事故対策の LIMIT 付与もここで行い、Analyzer と課金の両方を守る。

【主な関数構成】
- SqlGuardError: グラフが SQL リトライに乗せる専用例外
- sanitize_sql: コメント除去・DML 拒否・末尾 LIMIT
- preview: UI / ログ用の短縮表示
"""

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
    """ガード違反。グラフは一般 Exception と同じリトライ経路に載せるが、型で原因を区別する。"""

    pass


def sanitize_sql(sql: str, max_rows: int = 200) -> str:
    """実行前に破壊操作と無制限 SELECT を取り除く。

    コメントを先に消すのは ``SELECT 1; -- DROP TABLE`` やブロックコメント内 DML を
    キーワード検出から逃さないため。セミコロン除去は複数ステートメントの二段攻撃を潰す。
    既存 LIMIT は書き換えない。サブクエリ内 LIMIT を末尾キャップと誤認して壊すのを避ける。

    Args:
        sql: LLM が出した生 SQL。フェンスは呼び出し側で除去済みを想定。
        max_rows: 末尾 LIMIT が無いときに付与する上限。

    Returns:
        空白正規化済みの SELECT/WITH 文。必要なら末尾 LIMIT 付き。

    Raises:
        SqlGuardError: 空、DML/DDL、SELECT/WITH 以外で始まる場合。
    """
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
    """ログと UI に生 SQL 全量を流さず、調査再現に足る先頭だけを残す。

    Args:
        sql: 表示したい SQL。None なら空文字。
        n: 最大文字数。

    Returns:
        n 文字以内のプレビュー。超過時は末尾に省略記号。
    """
    if not sql:
        return ""
    sql = sql.strip()
    return sql if len(sql) <= n else sql[:n] + "…"
