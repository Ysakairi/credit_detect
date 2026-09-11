"""Copy the US public fraud table into regional ``dwh_prod``.

Dataform ``initial_setup`` materializes tables in ``asia-northeast1``.
``bigquery-public-data.ml_datasets.ulb_fraud_detection`` lives in the US
multi-region, so a single Dataform query job cannot read it and write
``dwh_prod`` at the same time. BigQuery reports that as:

    Access Denied: Table bigquery-public-data:ml_datasets.ulb_fraud_detection:
    User does not have permission to query table ..., or perhaps it does not exist.

This script uses the same Query(US) → Load(asia-northeast1) split as
``daily_insert.py``. Run it once with your user ADC (Owner / BigQuery Job User)
before Dataform tag ``initial_setup``.

Required env:
    PROJECT_ID   GCP project that owns ``dwh_prod``

Optional:
    DESTINATION_TABLE   default ``PROJECT_ID.dwh_prod.ulb_fraud_detection_public``
"""

from __future__ import annotations

import logging
import os
import sys

from google.cloud import bigquery

from daily_insert import (
    API_RETRY,
    BQ_API_TIMEOUT_SECONDS,
    BQ_JOB_TIMEOUT_SECONDS,
    BQ_QUERY_LOCATION,
    get_bq_client,
    load_daily_slice,
    log,
    validate_destination_table,
    _wait_for_job,
)

PUBLIC_SOURCE = "`bigquery-public-data.ml_datasets.ulb_fraud_detection`"
DEFAULT_TABLE_NAME = "ulb_fraud_detection_public"


def default_destination_table(project_id: str) -> str:
    return f"{project_id}.dwh_prod.{DEFAULT_TABLE_NAME}"


def fetch_public_table(client: bigquery.Client):
    query = f"""
        SELECT *
        FROM {PUBLIC_SOURCE}
        WHERE Time IS NOT NULL
    """
    job_config = bigquery.QueryJobConfig(
        labels={
            "component": "copy-public-source",
            "pipeline": "credit-detect",
        },
        job_timeout_ms=int(BQ_JOB_TIMEOUT_SECONDS * 1000),
    )
    log(
        logging.INFO,
        "Starting BigQuery extract of public fraud table",
        location=BQ_QUERY_LOCATION,
        source="bigquery-public-data.ml_datasets.ulb_fraud_detection",
    )
    query_job = client.query(
        query,
        job_config=job_config,
        location=BQ_QUERY_LOCATION,
        retry=API_RETRY,
        job_retry=API_RETRY,
        timeout=BQ_API_TIMEOUT_SECONDS,
    )
    _wait_for_job(query_job, timeout=BQ_JOB_TIMEOUT_SECONDS)
    return query_job.to_dataframe(create_bqstorage_client=False)


def run_copy() -> int:
    project_id = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("PROJECT_ID is required")
    table_id = validate_destination_table(
        os.environ.get("DESTINATION_TABLE") or default_destination_table(project_id)
    )
    client = get_bq_client(project_id)
    df = fetch_public_table(client)
    if df.empty:
        raise RuntimeError("Public fraud table query returned no rows")
    return load_daily_slice(client, df, table_id)


def main() -> int:
    try:
        n = run_copy()
    except Exception as exc:
        log(logging.ERROR, "copy_public_source failed", error=str(exc))
        return 1
    log(logging.INFO, "Public fraud table copied", row_count=n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
