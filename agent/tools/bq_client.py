"""BigQuery 読み取り実行。SELECT ガードと billed-byte 上限付き。

【Agent Engine 上の位置づけ】
SQL Exec ノードの実体。Agent Engine のサービスアカウントで走るため、
ここを通らない SQL を許すと推論テーブルへの書き込みや全表スキャンが課金事故になる。
Client は遅延生成し、pickle 対象のエージェント本体から切り離す。

MockSqlRunner は CI と Streamlit mock 用。V14/V17 が強分離になる固定行を返し、
SOP カーネルと Reflection が GCP なしで「十分」判定まで到達できるようにする。

【主な関数構成】
- SqlRunner: execute(sql) -> rows の Protocol
- _json_safe: BQ 型を Agent Engine RPC が JSON 化できる値へ
- BigQuerySqlRunner.execute: ガード + maximum_bytes_billed
- MockSqlRunner.execute: フィクスチャ行。raise_error でリトライ試験
- default_fraud_rows: 監査変数が分離する合成データ
- execute_bigquery_query: スクリプトからのワンショット実行
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol

from agent.config import AgentConfig
from agent.sql_guard import sanitize_sql

logger = logging.getLogger(__name__)


class SqlRunner(Protocol):
    """グラフが依存する SQL 実行口。本番と mock を差し替えるため。"""

    def execute(self, sql: str) -> List[Dict[str, Any]]:
        ...


def _json_safe(value: Any) -> Any:
    """datetime / bytes を JSON 可能な値にし、query() の戻り値が Agent Engine で落ちないようにする。

    Args:
        value: BQ 行のセル。ネストした dict/list もあり得る。

    Returns:
        isoformat 文字列・UTF-8 文字列・再帰変換済みコンテナ、または元のスカラー。
    """
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


class BigQuerySqlRunner:
    """本番の読み取りランナー。ジョブごとにスキャン上限を付け、FinOps 事故をジョブ単位で止める。"""

    def __init__(self, config: AgentConfig):
        """設定だけ保持し、Client は作らない（Agent Engine pickle 対策）。

        Args:
            config: プロジェクト、ロケーション、行数/バイト上限。
        """
        self.config = config
        self._client = None

    def _client_obj(self):
        """最初の execute まで Client 生成を遅らせ、set_up 時点の ADC 不備でデプロイを落とさない。

        Returns:
            google.cloud.bigquery.Client。
        """
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(
                project=self.config.project_id, location=self.config.location
            )
        return self._client

    def execute(self, sql: str) -> List[Dict[str, Any]]:
        """ガード通過後にだけ query する。結果は Analyzer が載る件数までに切る。

        use_query_cache は同一調査の Reflection リトライで同一 SQL が再実行されるため。

        Args:
            sql: LLM 生成 SQL。内部で sanitize_sql する。

        Returns:
            JSON 安全な dict 行のリスト。最大 max_query_rows 件。
        """
        from google.cloud import bigquery

        safe_sql = sanitize_sql(sql, max_rows=self.config.max_query_rows)
        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.config.max_bytes_billed,
            use_query_cache=True,
        )
        logger.info("Running BigQuery SQL (%s chars)", len(safe_sql))
        job = self._client_obj().query(safe_sql, job_config=job_config)
        rows = []
        for row in job.result(max_results=self.config.max_query_rows):
            rows.append(_json_safe(dict(row.items())))
        return rows


class MockSqlRunner:
    """GCP なしでグラフと SOP カーネルを成立させる決定論フィクスチャ。"""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        """差し込み行を受け、空結果や別分布のテストを可能にする。

        Args:
            rows: 省略時は default_fraud_rows()。
        """
        self.rows = rows if rows is not None else default_fraud_rows()
        self.last_sql: Optional[str] = None

    def execute(self, sql: str) -> List[Dict[str, Any]]:
        """本番と同じ sanitize を通し、ガード契約を mock 経路でも検証する。

        ``raise_error`` は SQL リトライ辺のユニットテスト用スイッチ。

        Args:
            sql: 生成 SQL。

        Returns:
            保持している行のコピー。

        Raises:
            RuntimeError: クエリに raise_error が含まれるとき。
            SqlGuardError: sanitize_sql が拒否したとき。
        """
        self.last_sql = sanitize_sql(sql, max_rows=200)
        if "raise_error" in (sql or "").lower():
            raise RuntimeError("Simulated BigQuery syntax error: Unrecognized name foo")
        return list(self.rows)


def default_fraud_rows() -> List[Dict[str, Any]]:
    """V14/V17/V12 を不正側へシフトさせ、Analyzer が強分離を再現できるようにする。

    実データ無しでも SOP 第7章の判定ロジックと UI の統計タブをデモするため。

    Returns:
        12 行の合成推論結果。前半が不正クラス。
    """
    rows: List[Dict[str, Any]] = []
    for i in range(12):
        row: Dict[str, Any] = {
            "Time": float(1000 + i),
            "Amount": 240.0 + i * 15,
            "Class": 1 if i < 6 else 0,
            "predicted_Class": 1 if i < 7 else 0,
            "fraud_probability": 0.93 - i * 0.04 if i < 7 else 0.12,
            "Hour": 2,
            "Date": 31,
        }
        for v in range(1, 29):
            if v in (14, 17, 12):
                row[f"V{v}"] = -4.2 - i * 0.15 if i < 6 else 0.2 * i
            else:
                row[f"V{v}"] = 0.05 * (i - 6)
        rows.append(row)
    return rows


def execute_bigquery_query(
    project_id: str,
    query: str,
    location: str = "asia-northeast1",
    dataset: str = "dwh_prod",
) -> List[Dict[str, Any]]:
    """グラフ外から同じガード付きランナーを使うためのショートカット。

    Args:
        project_id: GCP プロジェクト。
        query: 実行 SQL。
        location: BQ ロケーション。
        dataset: 設定構築用。クエリ本文のテーブル修飾とは独立。

    Returns:
        JSON 安全な行リスト。
    """
    config = AgentConfig(project_id=project_id, location=location, dataset=dataset)
    return BigQuerySqlRunner(config).execute(query)
