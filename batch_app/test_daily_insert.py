import json
import logging
import os
import unittest
from unittest import mock

import pandas as pd

import daily_insert


class ResolveTargetDateTest(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict(os.environ, {"TARGET_DATE": "12"}):
            self.assertEqual(daily_insert.resolve_target_date(), 12)

    def test_rejects_out_of_range(self):
        with mock.patch.dict(os.environ, {"TARGET_DATE": "99"}):
            with self.assertRaises(ValueError):
                daily_insert.resolve_target_date()

    def test_rejects_non_integer(self):
        with mock.patch.dict(os.environ, {"TARGET_DATE": "today"}):
            with self.assertRaises(ValueError):
                daily_insert.resolve_target_date()


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
        self.assertIs(load_kwargs["retry"], daily_insert.API_RETRY)
        load_job.result.assert_called()

    def test_client_is_reused(self):
        with mock.patch.object(
            daily_insert.bigquery, "Client", return_value=mock.Mock()
        ) as ctor:
            first = daily_insert.get_bq_client("proj-a")
            second = daily_insert.get_bq_client("proj-a")
        self.assertIs(first, second)
        ctor.assert_called_once_with(project="proj-a")


if __name__ == "__main__":
    unittest.main()
