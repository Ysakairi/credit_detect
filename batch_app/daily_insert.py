import logging
import os
from datetime import datetime, timedelta, timezone

from google.cloud import bigquery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("daily_insert")

PROJECT_ID = os.environ.get("PROJECT_ID")
DESTINATION_TABLE = os.environ.get("DESTINATION_TABLE")
PSEUDO_DAY_COUNT = 50


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
        logger.info("Target date specified by environment variable: %s", target_date)
    else:
        jst = timezone(timedelta(hours=9))
        target_date = datetime.now(jst).day
        logger.info("Target date from current JST date: %s", target_date)

    if not 1 <= target_date <= PSEUDO_DAY_COUNT:
        raise ValueError(
            f"TARGET_DATE must be between 1 and {PSEUDO_DAY_COUNT}, got: {target_date}"
        )
    return target_date


def run_ingestion() -> None:
    """
    Assign a 1-50 pseudo Date (same logic as initial_converted.sqlx), fetch only
    the target day's rows from the public dataset, and WRITE_TRUNCATE the batch table.
    """
    if not PROJECT_ID:
        raise RuntimeError("PROJECT_ID environment variable is required")
    if not DESTINATION_TABLE:
        raise RuntimeError("DESTINATION_TABLE environment variable is required")

    target_date = resolve_target_date()
    client = bigquery.Client(project=PROJECT_ID)

    # Date assignment matches initial_converted.sqlx:
    # MOD(ROW_NUMBER() OVER (ORDER BY Time, Amount, V1) - 1, 50) + 1
    # Filter in BigQuery so the job does not download the full public table.
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
        ]
    )

    logger.info("Fetching rows for Date == %s from public dataset...", target_date)
    df = client.query(query, job_config=query_config).to_dataframe()

    if df.empty:
        raise RuntimeError(
            f"No rows found for Date == {target_date}. "
            "Refusing to truncate the destination table."
        )

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    logger.info("Loading %s rows into %s...", len(df), DESTINATION_TABLE)
    job = client.load_table_from_dataframe(
        df,
        DESTINATION_TABLE,
        job_config=job_config,
    )
    job.result()
    logger.info("Loaded %s rows into %s.", job.output_rows, DESTINATION_TABLE)


if __name__ == "__main__":
    try:
        run_ingestion()
    except Exception:
        logger.exception("Error during ingestion")
        raise
