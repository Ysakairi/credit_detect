"""Dataform / Workflows / Terraform / Dockerfile の静的単体試験。

試験書: test/UNIT_TEST.md 節 4.3 / 4.4
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def sqlx_files() -> list[Path]:
    return sorted((ROOT / "dataform" / "definitions").rglob("*.sqlx"))


class DataformSqlTest(unittest.TestCase):
    def test_no_invalid_less_equal_operator(self):
        for path in sqlx_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                text,
                r"Date\s*=<",
                msg=f"invalid BigQuery operator in {path}",
            )

    def test_prediction_and_evaluation_use_between_1_and_31(self):
        for rel in (
            "dataform/definitions/daily_batch/daily_prediction.sqlx",
            "dataform/definitions/daily_batch/daily_evaluation.sqlx",
        ):
            text = read(rel)
            self.assertIn("Date BETWEEN 1 AND 31", text)

    def test_train_and_validation_date_ranges(self):
        self.assertIn(
            "Date BETWEEN 37 AND 50",
            read("dataform/definitions/initial_setup/initial_train.sqlx"),
        )
        self.assertIn(
            "Date BETWEEN 32 AND 36",
            read("dataform/definitions/initial_setup/initial_validation.sqlx"),
        )

    def test_daily_convert_uses_distinct_action_name(self):
        daily = read("dataform/definitions/daily_batch/daily_convert.sqlx")
        initial = read("dataform/definitions/initial_setup/initial_converted.sqlx")
        self.assertIn('name: "ulb_fraud_detection_daily_converted"', daily)
        self.assertIn('name: "ulb_fraud_detection_converted"', initial)
        self.assertNotEqual(
            re.search(r'name:\s*"([^"]+)"', daily).group(1),
            re.search(r'name:\s*"([^"]+)"', initial).group(1),
        )

    def test_model_refs_lowercase_train(self):
        model = read("dataform/definitions/initial_setup/initial_detection_model.sqlx")
        self.assertIn('ref("ulb_fraud_detection_train")', model)
        self.assertNotIn("ulb_fraud_detection_Train", model)
        self.assertIn("ENABLE_GLOBAL_EXPLAIN = TRUE", model)
        self.assertIn("AUTO_CLASS_WEIGHTS = TRUE", model)

    def test_time_is_not_null_filters(self):
        for rel in (
            "dataform/definitions/daily_batch/daily_convert.sqlx",
            "dataform/definitions/initial_setup/initial_converted.sqlx",
        ):
            self.assertIn("WHERE", read(rel))
            self.assertIn("Time IS NOT NULL", read(rel))

    def test_initial_converted_tie_break(self):
        text = read("dataform/definitions/initial_setup/initial_converted.sqlx")
        self.assertIn("ORDER BY Time, Amount, V1", text)
        self.assertIn(", 50)", text)

    def test_tags(self):
        daily_dir = ROOT / "dataform" / "definitions" / "daily_batch"
        for path in daily_dir.glob("*.sqlx"):
            self.assertIn('tags: ["daily_batch"]', path.read_text(encoding="utf-8"), path.name)
        setup_dir = ROOT / "dataform" / "definitions" / "initial_setup"
        for path in setup_dir.glob("*.sqlx"):
            self.assertIn('tags: ["initial_setup"]', path.read_text(encoding="utf-8"), path.name)

    def test_batch_is_declaration(self):
        text = read("dataform/definitions/declarations/ulb_fraud_detection_Batch.sqlx")
        self.assertIn('type: "declaration"', text)

    def test_audit_features(self):
        text = read("dataform/includes/features.js")
        self.assertIn('"V14"', text)
        self.assertIn('"V17"', text)
        self.assertIn('"V12"', text)

    def test_dataform_project_consistency(self):
        dataform_json = json.loads(read("dataform/dataform.json"))
        settings = read("dataform/workflow_settings.yaml")
        project = re.search(r"^defaultProject:\s*(\S+)", settings, re.M).group(1)
        dataset = re.search(r"^defaultDataset:\s*(\S+)", settings, re.M).group(1)
        self.assertEqual(dataform_json["defaultDatabase"], project)
        self.assertEqual(dataform_json["defaultSchema"], dataset)
        self.assertEqual(dataform_json["defaultLocation"], "asia-northeast1")


class WorkflowTerraformTest(unittest.TestCase):
    def setUp(self):
        self.workflow = read("terraform/workflow.yaml")
        self.tf = read("terraform/main.tf")
        self.dockerfile = read("batch_app/Dockerfile")
        self.gitignore = read(".gitignore")

    def test_templatefile_escapes_workflow_expressions(self):
        self.assertIn("$${job_execution.metadata.name}", self.workflow)
        self.assertIn("$${compilation_result.name}", self.workflow)
        self.assertIn("$${compilation_status.name}", self.workflow)
        self.assertIn("$${invocation_result.name}", self.workflow)
        self.assertNotRegex(self.workflow, r"(?<!\$)\$\{compilation_result")
        self.assertNotRegex(self.workflow, r"(?<!\$)\$\{job_execution")
        self.assertNotRegex(self.workflow, r"(?<!\$)\$\{invocation_")

    def test_raise_messages_with_colon_are_yaml_quoted(self):
        """YAML は未引用の ': ' でスカラーを切る。Workflows の raise 式は単引用符で囲む。"""
        self.assertIn(
            """raise: '$${"Cloud Run Job failed: " """,
            self.workflow,
        )
        self.assertIn(
            """raise: '$${"Dataform compilation failed: " """,
            self.workflow,
        )
        self.assertIn(
            """raise: '$${"Dataform invocation failed: " """,
            self.workflow,
        )
        for line in self.workflow.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("raise: $${"):
                self.fail(f"raise 式は YAML 単引用符で囲む: {stripped}")

    def test_job_has_location(self):
        self.assertIn("location: ${region}", self.workflow)
        self.assertIn("namespaces/${project_id}/jobs/daily-ingest-job", self.workflow)

    def test_job_completion_polling(self):
        self.assertIn("completionTime", self.workflow)
        self.assertIn("max_polls: 60", self.workflow)
        self.assertIn("Timed out waiting for Cloud Run Job to complete", self.workflow)

    def test_dataform_async_wait(self):
        self.assertIn('compilation_status.state == "SUCCEEDED"', self.workflow)
        self.assertIn('invocation_status.state == "SUCCEEDED"', self.workflow)
        self.assertIn("FAILED", self.workflow)

    def test_daily_batch_tag_only(self):
        self.assertIn("daily_batch", self.workflow)
        self.assertNotIn("initial_setup", self.workflow)
        self.assertIn('gitCommitish: "main"', self.workflow)

    def test_transient_retries(self):
        self.assertIn("retry.transient_errors", self.workflow)
        self.assertGreaterEqual(self.workflow.count("retry.transient_errors"), 3)

    def test_dataform_repository_resource(self):
        self.assertIn("google_dataform_repository", self.tf)
        self.assertIn("fraud-pipeline-repo", self.tf)

    def test_run_jobs_iam(self):
        self.assertIn("roles/bigquery.dataEditor", self.tf)
        self.assertIn("roles/bigquery.jobUser", self.tf)
        self.assertIn("sa-run-jobs-executor", self.tf)

    def test_scheduler_sa_separated(self):
        self.assertIn("sa-scheduler-trigger", self.tf)
        self.assertIn("roles/workflows.invoker", self.tf)
        self.assertIn("sa-workflows-orchestrator", self.tf)

    def test_job_developer_for_polling(self):
        self.assertIn("roles/run.developer", self.tf)

    def test_dataform_sa_bq_access(self):
        self.assertIn("google_project_service_identity", self.tf)
        self.assertIn('service    = "dataform.googleapis.com"', self.tf)

    def test_cloud_run_timeout_and_retries(self):
        self.assertIn('timeout         = "600s"', self.tf)
        self.assertIn("max_retries     = 3", self.tf)
        self.assertIn('memory = "1Gi"', self.tf)

    def test_dockerfile_non_root(self):
        self.assertIn("--uid 1001", self.dockerfile)
        self.assertIsNotNone(re.search(r"^USER appuser\s*$", self.dockerfile, re.M))

    def test_no_embedded_private_keys(self):
        scan_roots = [
            ROOT / "batch_app",
            ROOT / "dataform",
            ROOT / "terraform",
            ROOT / "evaluate",
        ]
        for scan_root in scan_roots:
            for path in scan_root.rglob("*"):
                if path.is_dir() or path.suffix in {".png", ".pyc"}:
                    continue
                if path.name.startswith("test_"):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                self.assertNotIn("BEGIN PRIVATE KEY", text, msg=str(path))
                self.assertNotIn('"type": "service_account"', text, msg=str(path))

    def test_gitignore_hides_state(self):
        self.assertIn("*.tfstate", self.gitignore)
        self.assertIn("*.tfvars", self.gitignore)
        self.assertIn("!**/example.tfvars", self.gitignore)


if __name__ == "__main__":
    unittest.main()
