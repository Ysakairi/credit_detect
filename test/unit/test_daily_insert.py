"""Cloud Run Job 取り込み処理の単体テスト。

実 GCP（BigQuery / ADC）は呼ばない。クロスリージョン Query/Load の
リトライ引数・空スライスでの TRUNCATE 拒否・クライアント再利用を固定する。

試験書: test/UNIT_TEST.md 節 4.1
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "batch_app"))

import daily_insert  # noqa: E402


class ResolveTargetDateTest(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"TARGET_DATE": "12"}):
            self.assertEqual(daily_insert.resolve_target_date(), 12)

    def test_rejects_out_of_range(self):
        with mock.patch.dict(os.environ, {"TARGET_DATE": "99"}):
            with self.assertRaises(ValueError):
                daily_insert.resolve_target_date()

    def test_rejects_zero(self):
        with mock.patch.dict(os.environ, {"TARGET_DATE": "0"}):
            with self.assertRaises(ValueError):
                daily_insert.resolve_target_date()

    def test_accepts_boundaries(self):
        with mock.patch.dict(os.environ, {"TARGET_DATE": "1"}):
            self.assertEqual(daily_insert.resolve_target_date(), 1)
        with mock.patch.dict(os.environ, {"TARGET_DATE": "50"}):
            self.assertEqual(daily_insert.resolve_target_date(), 50)
        with mock.patch.dict(os.environ, {"TARGET_DATE": "51"}):
            with self.assertRaises(ValueError):
                daily_insert.resolve_target_date()

    def test_rejects_non_integer(self):
        with mock.patch.dict(os.environ, {"TARGET_DATE": "today"}):
            with self.assertRaises(ValueError):
                daily_insert.resolve_target_date()

    def test_blank_env_uses_jst_calendar_day(self):
        jst = timezone(timedelta(hours=9))
        frozen = datetime(2026, 9, 10, 2, 0, tzinfo=jst)
        with mock.patch.dict(os.environ, {"TARGET_DATE": "  "}, clear=False):
            with mock.patch.object(daily_insert, "datetime") as dt_mod:
                dt_mod.now.return_value = frozen
                self.assertEqual(daily_insert.resolve_target_date(), 10)


class DestinationTableTest(unittest.TestCase):
    def test_accepts_valid_id(self):
        self.assertEqual(
            daily_insert.validate_destination_table(
                "skir_sample_credit.dwh_prod.ulb_fraud_detection_Batch"
            ),
            "skir_sample_credit.dwh_prod.ulb_fraud_detection_Batch",
        )

    def test_rejects_sql_injection(self):
        with self.assertRaises(ValueError):
            daily_insert.validate_destination_table(
                "proj.dwh.table; DROP TABLE dwh.t"
            )

    def test_rejects_too_few_parts(self):
        with self.assertRaises(ValueError):
            daily_insert.validate_destination_table("only.two")


class LoggingTest(unittest.TestCase):
    def test_json_severity_and_fields(self):
        formatter = daily_insert.CloudLoggingJsonFormatter()
        record = logging.LogRecord(
            name="daily_insert",
            level=logging.ERROR,
            pathname="daily_insert.py",
            lineno=1,
            msg="boom",
            args=(),
            exc_info=None,
        )
        record.json_fields = {"target_date": 3, "job_id": "abc"}
        payload = json.loads(formatter.format(record))
        self.assertEqual(payload["severity"], "ERROR")
        self.assertEqual(payload["message"], "boom")
        self.assertEqual(payload["target_date"], 3)
        self.assertEqual(payload["job_id"], "abc")


class IngestionTest(unittest.TestCase):
    def setUp(self):
        daily_insert.reset_bq_client()
        self.env = mock.patch.dict(
            os.environ,
            {
                "PROJECT_ID": "skir_sample_credit",
                "DESTINATION_TABLE": "skir_sample_credit.dwh_prod.ulb_fraud_detection_Batch",
                "TARGET_DATE": "5",
            },
        )
        self.env.start()
        daily_insert.PROJECT_ID = "skir_sample_credit"
        daily_insert.DESTINATION_TABLE = (
            "skir_sample_credit.dwh_prod.ulb_fraud_detection_Batch"
        )

    def tearDown(self):
        self.env.stop()
        daily_insert.reset_bq_client()
        daily_insert.PROJECT_ID = os.environ.get("PROJECT_ID")
        daily_insert.DESTINATION_TABLE = os.environ.get("DESTINATION_TABLE")

    def test_refuses_empty_slice_without_loading(self):
        client = mock.Mock()
        query_job = mock.Mock()
        query_job.to_dataframe.return_value = pd.DataFrame()
        client.query.return_value = query_job

        with self.assertRaises(RuntimeError):
            daily_insert.run_ingestion(client=client)

        client.load_table_from_dataframe.assert_not_called()

    def test_query_uses_us_location_retry_and_timeout(self):
        client = mock.Mock()
        query_job = mock.Mock()
        query_job.to_dataframe.return_value = pd.DataFrame({"Date": [5]})
        query_job.output_rows = 1
        client.query.return_value = query_job
        load_job = mock.Mock()
        load_job.output_rows = 1
        client.load_table_from_dataframe.return_value = load_job

        daily_insert.run_ingestion(client=client)

        _, kwargs = client.query.call_args
        self.assertEqual(kwargs["location"], "US")
        self.assertIs(kwargs["retry"], daily_insert.API_RETRY)
        self.assertIs(kwargs["job_retry"], daily_insert.API_RETRY)
        self.assertEqual(kwargs["timeout"], daily_insert.BQ_API_TIMEOUT_SECONDS)
        query_job.result.assert_called()
        result_kwargs = query_job.result.call_args.kwargs
        self.assertEqual(result_kwargs["timeout"], daily_insert.BQ_JOB_TIMEOUT_SECONDS)
        self.assertIs(result_kwargs["retry"], daily_insert.API_RETRY)
        query_job.to_dataframe.assert_called_with(create_bqstorage_client=False)

        load_kwargs = client.load_table_from_dataframe.call_args.kwargs
        self.assertEqual(load_kwargs["num_retries"], daily_insert.BQ_LOAD_NUM_RETRIES)
        self.assertNotIn("retry", load_kwargs)
        self.assertNotIn("job_retry", load_kwargs)
        load_job.result.assert_called()

        job_config = load_kwargs["job_config"]
        self.assertEqual(
            job_config.write_disposition,
            daily_insert.bigquery.WriteDisposition.WRITE_TRUNCATE,
        )

    def test_client_is_reused(self):
        with mock.patch.object(
            daily_insert.bigquery, "Client", return_value=mock.Mock()
        ) as ctor:
            first = daily_insert.get_bq_client("proj-a")
            second = daily_insert.get_bq_client("proj-a")
        self.assertIs(first, second)
        ctor.assert_called_once_with(project="proj-a")

    def test_missing_project_id(self):
        daily_insert.PROJECT_ID = None
        with self.assertRaises(RuntimeError):
            daily_insert.run_ingestion(client=mock.Mock())

    def test_missing_destination_table(self):
        daily_insert.DESTINATION_TABLE = None
        with self.assertRaises(RuntimeError):
            daily_insert.run_ingestion(client=mock.Mock())


class ExtractQueryContractTest(unittest.TestCase):
    def test_order_by_and_pseudo_day_count(self):
        src = (ROOT / "batch_app" / "daily_insert.py").read_text(encoding="utf-8")
        self.assertIn("ORDER BY Time, Amount, V1", src)
        self.assertEqual(daily_insert.PSEUDO_DAY_COUNT, 50)
        self.assertIn("create_bqstorage_client=False", src)
        self.assertEqual(daily_insert.BQ_JOB_TIMEOUT_SECONDS, 480.0)
        self.assertEqual(daily_insert.BQ_API_TIMEOUT_SECONDS, 60.0)
        self.assertEqual(daily_insert.BQ_LOAD_NUM_RETRIES, 6)
        load_fn = src.split("def load_daily_slice", 1)[1].split("def run_ingestion", 1)[0]
        self.assertIn("num_retries=BQ_LOAD_NUM_RETRIES", load_fn)
        self.assertNotIn("retry=", load_fn)

    def test_load_table_from_dataframe_has_no_retry_parameter(self):
        from google.cloud.bigquery import Client

        params = inspect.signature(Client.load_table_from_dataframe).parameters
        self.assertIn("num_retries", params)
        self.assertNotIn("retry", params)


if __name__ == "__main__":
    unittest.main()
