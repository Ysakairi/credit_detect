"""BigQuery query runner with a SELECT-only guard and billed-byte cap."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol

from agent.config import AgentConfig
from agent.sql_guard import sanitize_sql

logger = logging.getLogger(__name__)


class SqlRunner(Protocol):
    def execute(self, sql: str) -> List[Dict[str, Any]]:
        ...


def _json_safe(value: Any) -> Any:
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
    def __init__(self, config: AgentConfig):
        self.config = config
        self._client = None

    def _client_obj(self):
        if self._client is None:
            from google.cloud import bigquery

            self._client = bigquery.Client(
                project=self.config.project_id, location=self.config.location
            )
        return self._client

    def execute(self, sql: str) -> List[Dict[str, Any]]:
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
    """Deterministic fixture used by unit tests and the Streamlit mock backend."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = rows if rows is not None else default_fraud_rows()
        self.last_sql: Optional[str] = None

    def execute(self, sql: str) -> List[Dict[str, Any]]:
        self.last_sql = sanitize_sql(sql, max_rows=200)
        if "raise_error" in (sql or "").lower():
            raise RuntimeError("Simulated BigQuery syntax error: Unrecognized name foo")
        return list(self.rows)


def default_fraud_rows() -> List[Dict[str, Any]]:
    """Small PCA-like fixture: fraud cluster is shifted on V14/V17."""
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
    config = AgentConfig(project_id=project_id, location=location, dataset=dataset)
    return BigQuerySqlRunner(config).execute(query)
