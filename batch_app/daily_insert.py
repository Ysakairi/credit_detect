"""Cloud Run Job: 日次の不正検知バッチを BigQuery に取り込む。

GCP サービス:
    - Cloud Run Jobs: 本モジュールの実行環境。Scheduler → Workflows から起動される。
    - BigQuery: 公開データセットからの抽出と、地域データセットへのロード。
    - Cloud Logging: stdout の JSON を構造化ログとして収集（Logging クライアントは使わない）。

認証 / IAM:
    Application Default Credentials (ADC)。サービスアカウントは
    ``sa-run-jobs-executor``（``roles/bigquery.jobUser`` + データセット
    ``roles/bigquery.dataEditor``）。キーファイルは埋め込まない。

必須環境変数（Terraform が Job に注入）:
    PROJECT_ID          ロード先プロジェクト
    DESTINATION_TABLE   ``project.dataset.table`` 形式

任意:
    TARGET_DATE, BQ_QUERY_LOCATION, BQ_API_TIMEOUT_SECONDS, BQ_JOB_TIMEOUT_SECONDS

設計上の制約:
    公開テーブル ``bigquery-public-data.ml_datasets.ulb_fraud_detection`` は US
    マルチリージョン、宛先 ``dwh_prod`` は asia-northeast1。BigQuery は
    クエリジョブと宛先テーブルのリージョンを一致させる必要があるため、
    ``destination`` 付きの単一クエリでは書けない。Query（US）→ クライアント経由 →
    Load（asia-northeast1）に分解する。
"""

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
# 公開データを 50 疑似日に分割する。initial_converted.sqlx / README の運用前提と一致させる。
PSEUDO_DAY_COUNT = 50
# Public ulb_fraud_detection lives in the US multi-region. The destination
# dataset is regional (asia-northeast1), so results must be loaded rather
# than written with a same-region destination table.
BQ_QUERY_LOCATION = os.environ.get("BQ_QUERY_LOCATION", "US")
# Cloud Run Job の timeout は 600s（terraform/main.tf）。API 開始は短く切り、
# ジョブ完了待ちは 480s でクライアント側フェイルファストし、コンテナ kill より先に
# 原因付きで落とす。残りの余裕はリトライとログ flush 用。
BQ_API_TIMEOUT_SECONDS = float(os.environ.get("BQ_API_TIMEOUT_SECONDS", "60"))
BQ_JOB_TIMEOUT_SECONDS = float(os.environ.get("BQ_JOB_TIMEOUT_SECONDS", "480"))

# テーブル ID はクエリパラメータにできないため文字列連結が必要。
# 想定外の識別子（SQL 断片）を拒否してロード先の取り違えを防ぐ。
_TABLE_ID_RE = re.compile(
    r"^[a-zA-Z0-9_-]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$"
)

# 429/5xx など if_transient_error に載る障害だけを再試行する。
# initial=1s, multiplier=2, max=32s は GCP クライアントの既定に近い形。
# timeout=180s は「RPC の再試行予算」であり、ジョブ本体の 480s とは別。
# 4xx（権限不足・スキーマ不正）は再試行しても直らないので対象外。
API_RETRY = retry.Retry(
    predicate=retry.if_transient_error,
    initial=1.0,
    maximum=32.0,
    multiplier=2.0,
    timeout=180.0,
)
# load_table_from_dataframe は client.query() と違い retry= を受け取らない。
# 公式引数はアップロード再試行回数 num_retries（ライブラリ既定と同じ 6）。
BQ_LOAD_NUM_RETRIES = 6

_client: Optional[bigquery.Client] = None


class CloudLoggingJsonFormatter(logging.Formatter):
    """Cloud Logging が jsonPayload / severity として解釈できる 1 行 JSON にする。

    google-cloud-logging を使わない理由:
        追加の Logging API IAM と依存関係が不要で、Cloud Run は stdout を
        自動収集するため。テキストログだと severity フィルタが効きにくい。
    """

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
    """stdout へ JSON を出す。propagate=False でルートロガーのテキスト二重出力を避ける。"""
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
    """構造化フィールド（target_date, job_id 等）を jsonPayload に載せる。"""
    logger.log(level, message, extra={"json_fields": fields} if fields else None)


def get_bq_client(project_id: Optional[str] = None) -> bigquery.Client:
    """BigQuery クライアントをプロセス内で再利用する。

    Cloud Run Job はリクエストのたびにプロセスが起きるわけではないが、
    認証トークンと HTTP セッションの再作成を避け、テストでは同一インスタンスを
    差し込めるようにする。認証は ADC（メタデータサーバ上の Job SA）。
    """
    global _client
    if _client is None:
        resolved = project_id or PROJECT_ID
        if not resolved:
            raise RuntimeError("PROJECT_ID environment variable is required")
        _client = bigquery.Client(project=resolved)
    return _client


def reset_bq_client() -> None:
    """テストでキャッシュされたクライアントを捨てる。本番 Job では呼ばない。"""
    global _client
    _client = None


def validate_destination_table(table_id: str) -> str:
    """ロード先 ID を検証する。BigQuery はテーブル名をクエリパラメータにできない。"""
    if not _TABLE_ID_RE.match(table_id):
        raise ValueError(
            "DESTINATION_TABLE must be project.dataset.table "
            f"(got {table_id!r})"
        )
    return table_id


def resolve_target_date() -> int:
    """疑似日付 1〜50 を決める。既定は JST のカレンダー日。

    Cloud Scheduler は Asia/Tokyo 02:00 起動のため、スライスを JST の day に
    合わせる。月は 31 日までなので本番の日次は 1〜31 のみ（学習用 32〜50 は
    initial_setup の一括ロード側）。TARGET_DATE は手動再実行・テスト用。
    """
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
    """ジョブ完了まで待つ。timeout は Cloud Run 600s より短くし、job_id をログに残す。"""
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
    """US 公開データから 1 疑似日だけを抽出する。宛先テーブルはまだ書き換えない。

    フィルタを BigQuery 側で行うのは、約 28 万行の全表を Cloud Run（1Gi）へ
    落とさないため。日次スライスは約 1/50 で、クォータ的にもページングを自前実装
    する必要はない（クライアント SDK が REST ページを透過的に辿る）。

    create_bqstorage_client=False:
        Storage Read API は ``roles/bigquery.readSessionUser`` が別途必要。
        Job SA には付与していないため、日次件数なら REST で足りる。
    """
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
    """地域データセットへロードする。WRITE_TRUNCATE で Job 再実行をべき等にする。

    Cloud Run Job の max_retries や Workflows リトライで二重実行されても、
    同じ疑似日の全量で置き換わるため重複行は残らない。空 DataFrame での
    TRUNCATE は run_ingestion 側で拒否する（前日データを消さないため）。
    """
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
        num_retries=BQ_LOAD_NUM_RETRIES,
        timeout=BQ_API_TIMEOUT_SECONDS,
    )
    _wait_for_job(load_job, timeout=BQ_JOB_TIMEOUT_SECONDS)
    return int(load_job.output_rows or len(df))


def run_ingestion(client: Optional[bigquery.Client] = None) -> None:
    """疑似日付スライスを公開データから取り、バッチテーブルを WRITE_TRUNCATE する。

    Date の付け方は ``initial_converted.sqlx`` と一致させる（学習・推論の
    リーク防止）。空スライスで Load しないのは、WRITE_TRUNCATE が既存行を
    消すため。client 引数は ADC を使わない単体テスト用。
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
