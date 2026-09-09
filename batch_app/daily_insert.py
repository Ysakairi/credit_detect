import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from google.api_core import exceptions as gcp_exceptions
from google.api_core import retry
from google.cloud import bigquery

PROJECT_ID = os.environ.get("PROJECT_ID")
DESTINATION_TABLE = os.environ.get("DESTINATION_TABLE")
PSEUDO_DAY_COUNT = 50
# Public ulb_fraud_detection lives in the US multi-region. The destination
# dataset is regional (asia-northeast1), so results must be loaded rather
# than written with a same-region destination table.
BQ_QUERY_LOCATION = os.environ.get("BQ_QUERY_LOCATION", "US")
# Cloud Run Job timeout is 600s; fail slightly earlier with a clear error.
BQ_API_TIMEOUT_SECONDS = float(os.environ.get("BQ_API_TIMEOUT_SECONDS", "60"))
BQ_JOB_TIMEOUT_SECONDS = float(os.environ.get("BQ_JOB_TIMEOUT_SECONDS", "480"))

_TABLE_ID_RE = re.compile(
    r"^[a-zA-Z0-9_-]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$"
)

# Transient GCP / network errors: exponential backoff (1s -> 32s, 180s budget).
API_RETRY = retry.Retry(
    predicate=retry.if_transient_error,
    initial=1.0,
    maximum=32.0,
    multiplier=2.0,
    timeout=180.0,
)

_client: Optional[bigquery.Client] = None


class CloudLoggingJsonFormatter(logging.Formatter):
    """stdout JSON logs that Cloud Logging maps to severity and jsonPayload."""

    _SEVERITY = {
        "DEBUG": "DEBUG",
        "INFO": "INFO",
        "WARNING": "WARNING",
        "ERROR": "ERROR",
        "CRITICAL": "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": self._SEVERITY.get(record.levelname, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
        }
        extra = getattr(record, "json_fields", None)
        if isinstance(extra, Mapping):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("daily_insert")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudLoggingJsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


logger = configure_logging()


def log(level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"json_fields": fields} if fields else None)


def get_bq_client(project_id: Optional[str] = None) -> bigquery.Client:
    """Reuse one BigQuery client for the process (Cloud Run Job lifecycle)."""
    global _client
    if _client is None:
        resolved = project_id or PROJECT_ID
        if not resolved:
            raise RuntimeError("PROJECT_ID environment variable is required")
        _client = bigquery.Client(project=resolved)
    return _client


def reset_bq_client() -> None:
    """Test helper to drop the cached client."""
    global _client
    _client = None


def validate_destination_table(table_id: str) -> str:
    if not _TABLE_ID_RE.match(table_id):
        raise ValueError(
            "DESTINATION_TABLE must be project.dataset.table "
            f"(got {table_id!r})"
        )
    return table_id


def resolve_target_date() -> int:
    """Resolve the pseudo-date slice (1-50). Defaults to the JST calendar day."""
    target_date_str = os.environ.get("TARGET_DATE")
    if target_date_str is not None and target_date_str.strip() != "":
        try:
            target_date = int(target_date_str)
        except ValueError as exc:
            raise ValueError(
                f"TARGET_DATE must be an integer, got: {target_date_str!r}"
            ) from exc
        log(logging.INFO, "Target date specified by environment variable", target_date=target_date)
    else:
        jst = timezone(timedelta(hours=9))
        target_date = datetime.now(jst).day
        log(logging.INFO, "Target date from current JST date", target_date=target_date)

    if not 1 <= target_date <= PSEUDO_DAY_COUNT:
        raise ValueError(
            f"TARGET_DATE must be between 1 and {PSEUDO_DAY_COUNT}, got: {target_date}"
        )
    return target_date


def _wait_for_job(job: Any, *, timeout: float) -> Any:
    try:
        return job.result(timeout=timeout, retry=API_RETRY)
    except gcp_exceptions.GoogleAPICallError:
        log(
            logging.ERROR,
            "BigQuery job failed",
            job_id=getattr(job, "job_id", None),
            location=getattr(job, "location", None),
            errors=getattr(job, "errors", None),
        )
        raise


def fetch_daily_slice(client: bigquery.Client, target_date: int):
    """Query the US public dataset for one pseudo-day. Does not write the dest table."""
    query = f"""
        WITH numbered AS (
          SELECT
            *,
            MOD(
              ROW_NUMBER() OVER (ORDER BY Time, Amount, V1) - 1,
              {PSEUDO_DAY_COUNT}
            ) + 1 AS Date
          FROM `bigquery-public-data.ml_datasets.ulb_fraud_detection`
          WHERE Time IS NOT NULL
        )
        SELECT *
        FROM numbered
        WHERE Date = @target_date
    """
    query_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_date", "INT64", target_date),
        ],
        labels={
            "component": "daily-ingest",
            "pipeline": "credit-detect",
        },
        job_timeout_ms=int(BQ_JOB_TIMEOUT_SECONDS * 1000),
    )
    log(
        logging.INFO,
        "Starting BigQuery extract query",
        target_date=target_date,
        location=BQ_QUERY_LOCATION,
    )
    query_job = client.query(
        query,
        job_config=query_config,
        location=BQ_QUERY_LOCATION,
        retry=API_RETRY,
        job_retry=API_RETRY,
        timeout=BQ_API_TIMEOUT_SECONDS,
    )
    _wait_for_job(query_job, timeout=BQ_JOB_TIMEOUT_SECONDS)
    # Avoid BigQuery Storage API (requires extra IAM). REST result is enough for a daily slice.
    return query_job.to_dataframe(create_bqstorage_client=False)


def load_daily_slice(client: bigquery.Client, df, table_id: str) -> int:
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        labels={
            "component": "daily-ingest",
            "pipeline": "credit-detect",
        },
    )
    log(
        logging.INFO,
        "Starting BigQuery load",
        row_count=len(df),
        destination_table=table_id,
    )
    load_job = client.load_table_from_dataframe(
        df,
        table_id,
        job_config=job_config,
        retry=API_RETRY,
        timeout=BQ_API_TIMEOUT_SECONDS,
    )
    _wait_for_job(load_job, timeout=BQ_JOB_TIMEOUT_SECONDS)
    return int(load_job.output_rows or len(df))


def run_ingestion(client: Optional[bigquery.Client] = None) -> None:
    """
    Assign a 1-50 pseudo Date (same logic as initial_converted.sqlx), fetch only
    the target day's rows from the public dataset, and WRITE_TRUNCATE the batch table.

    Source data is in the US public dataset while the destination is regional, so
    the slice is queried then loaded (a single destination query cannot cross regions).
    """
    if not PROJECT_ID:
        raise RuntimeError("PROJECT_ID environment variable is required")
    if not DESTINATION_TABLE:
        raise RuntimeError("DESTINATION_TABLE environment variable is required")
    table_id = validate_destination_table(DESTINATION_TABLE)

    target_date = resolve_target_date()
    bq_client = client or get_bq_client(PROJECT_ID)

    df = fetch_daily_slice(bq_client, target_date)

    if df.empty:
        raise RuntimeError(
            f"No rows found for Date == {target_date}. "
            "Refusing to truncate the destination table."
        )

    output_rows = load_daily_slice(bq_client, df, table_id)
    log(
        logging.INFO,
        "Ingestion complete",
        target_date=target_date,
        row_count=output_rows,
        destination_table=table_id,
    )


if __name__ == "__main__":
    try:
        run_ingestion()
    except Exception:
        logger.exception("Error during ingestion")
        raise
