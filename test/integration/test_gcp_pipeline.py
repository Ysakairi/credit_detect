"""GCP パイプライン結合試験。

試験書: test/INTEGRATION_TEST.md
認証が無い場合は skip。破壊的な Job / Workflow 実行は環境変数でオプトイン。
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROJECT_ID = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
REGION = os.environ.get("GCP_REGION", "asia-northeast1")
AR_REPO = os.environ.get("GCP_AR_REPO", "my-repo")
DATASET = "dwh_prod"
JOB_NAME = "daily-ingest-job"
WORKFLOW_NAME = "fraud-detection-pipeline"
SCHEDULER_NAME = "daily-fraud-pipeline-trigger"
DATAFORM_REPO = "fraud-pipeline-repo"
TARGET_DATE = int(os.environ.get("IT_TARGET_DATE", "5"))

REQUIRED_APIS = (
    "bigquery.googleapis.com",
    "run.googleapis.com",
    "workflows.googleapis.com",
    "cloudscheduler.googleapis.com",
    "dataform.googleapis.com",
    "iam.googleapis.com",
    "secretmanager.googleapis.com",
)


def _adc_available() -> tuple[bool, str]:
    if not PROJECT_ID:
        return False, "GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT が未設定"
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if creds is None:
            return False, "ADC が取得できない"
        return True, ""
    except Exception as exc:  # noqa: BLE001 — 結合試験の skip 理由をそのまま出す
        return False, str(exc)


ADC_OK, ADC_REASON = _adc_available()


def skip_without_gcp(reason: str = ""):
    msg = reason or ADC_REASON or "GCP 認証なし"
    return unittest.skipUnless(ADC_OK, msg)


class GcpHelpers:
    _bq = None
    _session = None

    @classmethod
    def bq(cls):
        from google.cloud import bigquery

        if cls._bq is None:
            cls._bq = bigquery.Client(project=PROJECT_ID)
        return cls._bq

    @classmethod
    def authed_session(cls):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        if cls._session is None:
            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            cls._session = AuthorizedSession(creds)
        return cls._session

    @classmethod
    def get_json(cls, url: str, ok_statuses: tuple[int, ...] = (200,)):
        session = cls.authed_session()
        resp = session.get(url, timeout=60)
        if resp.status_code not in ok_statuses:
            raise AssertionError(f"GET {url} -> {resp.status_code}: {resp.text[:500]}")
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


@skip_without_gcp()
class EnvironmentTest(unittest.TestCase):
    def test_required_apis_enabled(self):
        enabled = []
        for api in REQUIRED_APIS:
            url = (
                f"https://serviceusage.googleapis.com/v1/projects/{PROJECT_ID}"
                f"/services/{api}"
            )
            body = GcpHelpers.get_json(url)
            state = body.get("state")
            enabled.append((api, state))
            self.assertEqual(state, "ENABLED", msg=f"{api} state={state}")

    def test_artifact_registry_repo(self):
        url = (
            f"https://artifactregistry.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{REGION}/repositories/{AR_REPO}"
        )
        body = GcpHelpers.get_json(url, ok_statuses=(200, 404))
        if body.get("error") or "name" not in body:
            self.skipTest(f"Artifact Registry {AR_REPO} が未作成")
        self.assertIn(AR_REPO, body["name"])

    def test_ingest_image_exists(self):
        url = (
            f"https://artifactregistry.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{REGION}/repositories/{AR_REPO}/packages/daily-ingest"
        )
        body = GcpHelpers.get_json(url, ok_statuses=(200, 404))
        if "name" not in body:
            self.skipTest("daily-ingest イメージが未登録")

    def test_dataset_location(self):
        dataset = GcpHelpers.bq().get_dataset(f"{PROJECT_ID}.{DATASET}")
        self.assertEqual(dataset.location.lower(), REGION.lower())

    def test_cloud_run_job_contract(self):
        url = (
            f"https://run.googleapis.com/v2/projects/{PROJECT_ID}/locations/"
            f"{REGION}/jobs/{JOB_NAME}"
        )
        job = GcpHelpers.get_json(url)
        template = job["template"]["template"]
        self.assertEqual(template.get("timeout"), "600s")
        self.assertGreaterEqual(int(template.get("maxRetries", 0)), 3)
        container = template["containers"][0]
        self.assertEqual(container["resources"]["limits"]["memory"], "1Gi")
        env = {item["name"]: item.get("value") for item in container.get("env", [])}
        self.assertEqual(env.get("PROJECT_ID"), PROJECT_ID)
        self.assertEqual(
            env.get("DESTINATION_TABLE"),
            f"{PROJECT_ID}.{DATASET}.ulb_fraud_detection_Batch",
        )

    def test_workflow_exists(self):
        url = (
            f"https://workflowexecutions.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{REGION}/workflows/{WORKFLOW_NAME}"
        )
        # Workflows Management API
        mgmt = (
            f"https://workflows.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{REGION}/workflows/{WORKFLOW_NAME}"
        )
        body = GcpHelpers.get_json(mgmt)
        self.assertIn("name", body)
        self.assertTrue(body.get("serviceAccount"))

    def test_scheduler_job(self):
        url = (
            f"https://cloudscheduler.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{REGION}/jobs/{SCHEDULER_NAME}"
        )
        body = GcpHelpers.get_json(url)
        self.assertEqual(body.get("schedule"), "0 2 * * *")
        self.assertEqual(body.get("timeZone"), "Asia/Tokyo")

    def test_dataform_repository(self):
        url = (
            f"https://dataform.googleapis.com/v1beta1/projects/{PROJECT_ID}"
            f"/locations/{REGION}/repositories/{DATAFORM_REPO}"
        )
        body = GcpHelpers.get_json(url)
        self.assertIn("name", body)

    def test_project_id_matches_workflow_settings(self):
        settings = (ROOT / "dataform" / "workflow_settings.yaml").read_text(
            encoding="utf-8"
        )
        project = re.search(r"^defaultProject:\s*(\S+)", settings, re.M).group(1)
        self.assertEqual(
            project,
            PROJECT_ID,
            msg="workflow_settings.yaml の defaultProject と GCP_PROJECT_ID が不一致",
        )


@skip_without_gcp()
class IamTest(unittest.TestCase):
    def _sa_email(self, account_id: str) -> str:
        return f"{account_id}@{PROJECT_ID}.iam.gserviceaccount.com"

    def test_run_jobs_sa_exists(self):
        email = self._sa_email("sa-run-jobs-executor")
        url = (
            f"https://iam.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/serviceAccounts/{email}"
        )
        body = GcpHelpers.get_json(url)
        self.assertEqual(body.get("email"), email)

    def test_workflows_and_scheduler_sas_are_distinct(self):
        wf = self._sa_email("sa-workflows-orchestrator")
        sch = self._sa_email("sa-scheduler-trigger")
        for email in (wf, sch):
            url = (
                f"https://iam.googleapis.com/v1/projects/{PROJECT_ID}"
                f"/serviceAccounts/{email}"
            )
            body = GcpHelpers.get_json(url)
            self.assertEqual(body.get("email"), email)
        self.assertNotEqual(wf, sch)


@skip_without_gcp()
class IngestContractTest(unittest.TestCase):
    def test_public_dataset_reachable(self):
        from google.cloud import bigquery

        job = GcpHelpers.bq().query(
            "SELECT COUNT(*) AS n FROM `bigquery-public-data.ml_datasets.ulb_fraud_detection`",
            location="US",
        )
        n = list(job.result())[0]["n"]
        self.assertGreater(n, 1000)

    def test_cross_region_destination_query_is_rejected(self):
        from google.api_core import exceptions as gcp_exceptions
        from google.cloud import bigquery

        sql = f"""
        CREATE OR REPLACE TABLE `{PROJECT_ID}.{DATASET}._it_should_not_exist` AS
        SELECT * FROM `bigquery-public-data.ml_datasets.ulb_fraud_detection` LIMIT 1
        """
        with self.assertRaises(Exception):
            job = GcpHelpers.bq().query(sql, location="US")
            list(job.result())

    def test_slice_query_returns_rows(self):
        sql = f"""
        WITH numbered AS (
          SELECT
            *,
            MOD(ROW_NUMBER() OVER (ORDER BY Time, Amount, V1) - 1, 50) + 1 AS Date
          FROM `bigquery-public-data.ml_datasets.ulb_fraud_detection`
          WHERE Time IS NOT NULL
        )
        SELECT COUNT(*) AS n, COUNTIF(Time IS NULL) AS null_time
        FROM numbered
        WHERE Date = @target_date
        """
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("target_date", "INT64", TARGET_DATE)
            ]
        )
        job = GcpHelpers.bq().query(sql, job_config=job_config, location="US")
        row = list(job.result())[0]
        self.assertGreater(row["n"], 0)
        self.assertEqual(row["null_time"], 0)

    def test_slice_query_is_deterministic(self):
        from google.cloud import bigquery

        sql = """
        SELECT COUNT(*) AS n
        FROM (
          SELECT MOD(ROW_NUMBER() OVER (ORDER BY Time, Amount, V1) - 1, 50) + 1 AS Date
          FROM `bigquery-public-data.ml_datasets.ulb_fraud_detection`
          WHERE Time IS NOT NULL
        )
        WHERE Date = 5
        """
        counts = []
        for _ in range(2):
            job = GcpHelpers.bq().query(sql, location="US")
            counts.append(list(job.result())[0]["n"])
        self.assertEqual(counts[0], counts[1])


@skip_without_gcp()
class OptionalMutatingTest(unittest.TestCase):
    def test_run_ingest_job(self):
        if os.environ.get("RUN_INGEST_JOB") != "1":
            self.skipTest("RUN_INGEST_JOB=1 のときのみ Cloud Run Job を起動する")
        session = GcpHelpers.authed_session()
        url = (
            f"https://run.googleapis.com/v1/namespaces/{PROJECT_ID}/jobs/{JOB_NAME}"
            f":run?location={REGION}"
        )
        overrides = {
            "overrides": {
                "containerOverrides": [
                    {
                        "env": [
                            {"name": "TARGET_DATE", "value": str(TARGET_DATE)},
                        ]
                    }
                ]
            }
        }
        resp = session.post(url, json=overrides, timeout=60)
        self.assertIn(resp.status_code, (200, 201), resp.text[:800])
        execution = resp.json()
        name = execution["metadata"]["name"]
        import time

        deadline = time.time() + 600
        status_url = (
            f"https://run.googleapis.com/v1/namespaces/{PROJECT_ID}/executions/"
            f"{name.split('/')[-1]}?location={REGION}"
        )
        # executions.get wants the full resource name
        get_url = (
            f"https://run.googleapis.com/v1/{name}?location={REGION}"
        )
        while time.time() < deadline:
            body = GcpHelpers.get_json(get_url)
            completion = body.get("status", {}).get("completionTime")
            if completion:
                cond = body["status"]["conditions"][0]
                self.assertEqual(cond.get("status"), "True", cond)
                return
            time.sleep(10)
        self.fail("Cloud Run Job が 600s 以内に完了しない")


@skip_without_gcp()
class WorkflowDefinitionTest(unittest.TestCase):
    def test_workflow_source_contains_contracts(self):
        url = (
            f"https://workflows.googleapis.com/v1/projects/{PROJECT_ID}"
            f"/locations/{REGION}/workflows/{WORKFLOW_NAME}"
        )
        body = GcpHelpers.get_json(url)
        source = body.get("sourceContents") or ""
        if not source:
            self.skipTest("sourceContents が API 応答に含まれない")
        self.assertIn("location", source)
        self.assertIn("completionTime", source)
        self.assertIn("daily_batch", source)
        self.assertIn("main", source)
        self.assertNotIn("initial_setup", source)


@skip_without_gcp()
class BigQueryMlArtifactsTest(unittest.TestCase):
    def _table_exists(self, table: str) -> bool:
        from google.api_core import exceptions as gcp_exceptions

        try:
            GcpHelpers.bq().get_table(f"{PROJECT_ID}.{DATASET}.{table}")
            return True
        except gcp_exceptions.NotFound:
            return False

    def test_model_exists_or_skip(self):
        from google.api_core import exceptions as gcp_exceptions

        try:
            GcpHelpers.bq().get_model(f"{PROJECT_ID}.{DATASET}.ulb_fraud_detection_model")
        except gcp_exceptions.NotFound:
            self.skipTest("ulb_fraud_detection_model 未作成。initial_setup が必要")

    def test_predictions_date_range(self):
        if not self._table_exists("ulb_fraud_detection_predictions"):
            self.skipTest("predictions テーブル未作成")
        sql = f"""
        SELECT
          COUNT(*) AS n,
          COUNTIF(Date < 1 OR Date > 31) AS out_of_range
        FROM `{PROJECT_ID}.{DATASET}.ulb_fraud_detection_predictions`
        """
        row = list(GcpHelpers.bq().query(sql, location=REGION).result())[0]
        self.assertGreater(row["n"], 0)
        self.assertEqual(row["out_of_range"], 0)

    def test_evaluation_matrix_categories(self):
        if not self._table_exists("ulb_fraud_detection_evaluation_matrix"):
            self.skipTest("evaluation_matrix 未作成")
        sql = f"""
        SELECT COUNT(DISTINCT category_id) AS cats
        FROM `{PROJECT_ID}.{DATASET}.ulb_fraud_detection_evaluation_matrix`
        """
        cats = list(GcpHelpers.bq().query(sql, location=REGION).result())[0]["cats"]
        self.assertGreaterEqual(cats, 3)

    def test_imbalance_metrics_have_pr_auc(self):
        if not self._table_exists("ulb_fraud_detection_imbalance_metrics"):
            self.skipTest("imbalance_metrics 未作成")
        table = GcpHelpers.bq().get_table(
            f"{PROJECT_ID}.{DATASET}.ulb_fraud_detection_imbalance_metrics"
        )
        names = {field.name for field in table.schema}
        self.assertIn("pr_auc", names)

    def test_psi_rating_domain(self):
        if not self._table_exists("ulb_fraud_detection_psi"):
            self.skipTest("psi テーブル未作成")
        sql = f"""
        SELECT DISTINCT psi_rating AS rating
        FROM `{PROJECT_ID}.{DATASET}.ulb_fraud_detection_psi`
        """
        ratings = {row["rating"] for row in GcpHelpers.bq().query(sql, location=REGION).result()}
        self.assertTrue(ratings.issubset({"Green", "Yellow", "Red"}))

    def test_audit_shap_flags(self):
        if not self._table_exists("ulb_fraud_detection_global_explain"):
            self.skipTest("global_explain 未作成")
        sql = f"""
        SELECT COUNTIF(is_audit_variable) AS n_audit
        FROM `{PROJECT_ID}.{DATASET}.ulb_fraud_detection_global_explain`
        WHERE feature IN ("V14", "V17", "V12")
        """
        n = list(GcpHelpers.bq().query(sql, location=REGION).result())[0]["n_audit"]
        self.assertGreaterEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
