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

    def test_public_copy_is_declaration(self):
        text = read(
            "dataform/definitions/declarations/ulb_fraud_detection_public.sqlx"
        )
        self.assertIn('type: "declaration"', text)
        self.assertIn('name: "ulb_fraud_detection_public"', text)

    def test_initial_converted_reads_local_public_copy(self):
        text = read("dataform/definitions/initial_setup/initial_converted.sqlx")
        self.assertIn('ref("ulb_fraud_detection_public")', text)
        self.assertNotIn("bigquery-public-data", text)

    def test_audit_features(self):
        text = read("dataform/includes/features.js")
        self.assertIn('"V14"', text)
        self.assertIn('"V17"', text)
        self.assertIn('"V12"', text)

    LOOKER_HISTORY_TABLES = {
        "ulb_fraud_detection_evaluation": "v_detection_evaluation",
        "ulb_fraud_detection_feature_separation": "v_detection_feature_separation",
        "ulb_fraud_detection_woe_bins": "v_detection_woe_bins",
        "ulb_fraud_detection_feature_importance": "v_detection_feature_importance",
        "ulb_fraud_detection_global_explain": "v_detection_global_explain",
        "ulb_fraud_detection_local_explain": "v_detection_local_explain",
        "ulb_fraud_detection_imbalance_metrics": "v_detection_imbalance_metrics",
        "ulb_fraud_detection_cost_curve": "v_detection_cost_curve",
        "ulb_fraud_detection_psi": "v_detection_psi",
    }

    def test_history_backup_sqlx_before_daily_replace(self):
        backup_js = read("dataform/includes/backup.js")
        self.assertIn("CREATE TABLE IF NOT EXISTS", backup_js)
        self.assertIn("INFORMATION_SCHEMA.TABLES", backup_js)
        self.assertIn("evaluation_date", backup_js)

        evaluate_md = read("EVALUATE.md")
        daily_dir = ROOT / "dataform" / "definitions" / "daily_batch"
        for source, backup in self.LOOKER_HISTORY_TABLES.items():
            path = daily_dir / f"{backup}.sqlx"
            self.assertTrue(path.exists(), msg=f"missing {path.name}")
            text = path.read_text(encoding="utf-8")
            self.assertIn('type: "operations"', text)
            self.assertIn(f'name: "{backup}"', text)
            self.assertIn('tags: ["daily_batch"]', text)
            self.assertIn(f'"{source}"', text)
            self.assertNotIn(f'ref("{source}")', text)
            self.assertIn(f"`{backup}`", evaluate_md)

            daily_matches = [
                p
                for p in daily_dir.glob("daily_*.sqlx")
                if f'name: "{source}"' in p.read_text(encoding="utf-8")
            ]
            self.assertEqual(len(daily_matches), 1, msg=source)
            daily_text = daily_matches[0].read_text(encoding="utf-8")
            self.assertIn(f'"{backup}"', daily_text)

    def test_dataform_project_consistency(self):
        settings = read("dataform/workflow_settings.yaml")
        project = re.search(r"^defaultProject:\s*(\S+)", settings, re.M).group(1)
        dataset = re.search(r"^defaultDataset:\s*(\S+)", settings, re.M).group(1)
        location = re.search(r"^defaultLocation:\s*(\S+)", settings, re.M).group(1)
        self.assertEqual(project, "skir-sample-credit")
        self.assertNotIn("_", project)
        self.assertEqual(dataset, "dwh_prod")
        self.assertEqual(location, "asia-northeast1")

    def test_dataform_json_is_absent(self):
        # Dataform core 3.0 rejects dataform.json alongside workflow_settings.yaml.
        self.assertFalse(
            (ROOT / "dataform" / "dataform.json").exists(),
            msg="dataform.json is deprecated and cannot sit next to workflow_settings.yaml",
        )

    def test_package_json_pins_dataform_core(self):
        pkg = json.loads(read("dataform/package.json"))
        self.assertEqual(pkg["dependencies"]["@dataform/core"], "3.0.0")


class WorkflowTerraformTest(unittest.TestCase):
    def setUp(self):
        self.workflow = read("terraform/workflow.yaml")
        self.tf = read("terraform/main.tf")
        self.dockerfile = read("batch_app/Dockerfile")
        self.gitignore = read(".gitignore")

    def test_templatefile_escapes_workflow_expressions(self):
        self.assertIn("job_execution.metadata.name", self.workflow)
        self.assertIn(
            '$${"namespaces/" + job_execution.metadata.namespace + "/executions/"',
            self.workflow,
        )
        self.assertIn("compilation_result.body.name", self.workflow)
        self.assertIn("$${compilation_status.body.name}", self.workflow)
        self.assertIn("invocation_result.body.name", self.workflow)
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

    def test_check_job_status_is_switch_only(self):
        """Workflows は 1 ステップを switch と raise の両方にできない。失敗は switch 枝で raise する。"""
        self.assertNotRegex(
            self.workflow,
            r"- check_job_status:\n(?:.*\n)*?        switch:\n(?:.*\n)*?        raise:",
        )
        self.assertIn("            raise: '$${\"Cloud Run Job failed: \"", self.workflow)

    def test_job_has_location(self):
        self.assertIn("location: ${region}", self.workflow)
        self.assertIn("namespaces/${project_id}/jobs/daily-ingest-job", self.workflow)

    def test_job_completion_polling(self):
        self.assertIn("completionTime", self.workflow)
        self.assertIn("max_polls: 60", self.workflow)
        self.assertIn("Timed out waiting for Cloud Run Job to complete", self.workflow)
        self.assertIn("job_execution.metadata.namespace", self.workflow)
        self.assertIn('"/executions/" + job_execution.metadata.name', self.workflow)
        self.assertNotIn(
            "name: $${job_execution.metadata.name}",
            self.workflow,
        )

    def test_dataform_uses_http_v1_not_missing_connector(self):
        """Workflows に Dataform コネクタは無い。公式は dataform.googleapis.com/v1 + OAuth2。"""
        self.assertNotIn("googleapis.dataform", self.workflow)
        self.assertIn("https://dataform.googleapis.com/v1/", self.workflow)
        self.assertIn("type: OAuth2", self.workflow)
        self.assertIn("/compilationResults", self.workflow)
        self.assertIn("/workflowInvocations", self.workflow)
        # CompilationResult v1 has no state; WorkflowInvocation still does.
        self.assertNotIn("compilation_status.body.state", self.workflow)
        self.assertIn("compilation_status.body.compilationErrors", self.workflow)
        self.assertIn("compilation_status.body.dataformCoreVersion", self.workflow)
        self.assertIn("compilation_status.body.resolvedGitCommitSha", self.workflow)
        self.assertIn('invocation_status.body.state == "SUCCEEDED"', self.workflow)
        self.assertIn("FAILED", self.workflow)

    def test_daily_batch_tag_only(self):
        self.assertIn("daily_batch", self.workflow)
        self.assertNotIn("initial_setup", self.workflow)
        self.assertIn('gitCommitish: "main"', self.workflow)

    def test_transient_retries(self):
        self.assertIn("$${http.default_retry_predicate}", self.workflow)
        self.assertGreaterEqual(self.workflow.count("$${http.default_retry_predicate}"), 3)
        self.assertNotIn("retry.transient_errors", self.workflow)
        self.assertIn("initial_delay:", self.workflow)
        self.assertIn("max_delay:", self.workflow)

    def test_dataform_repository_resource(self):
        self.assertIn("google_dataform_repository", self.tf)
        self.assertIn("fraud-pipeline-repo", self.tf)

    def test_dataform_git_url_defaults_to_dataform_repo(self):
        self.assertIn(
            'default     = "https://github.com/Ysakairi/credit_detect_dataform.git"',
            self.tf,
        )
        self.assertNotIn(
            'default     = "https://github.com/Ysakairi/credit_detect.git"',
            self.tf,
        )
        self.assertIn(
            "Dataform Git URL must be credit_detect_dataform",
            self.tf,
        )
        example = read("terraform/example.tfvars")
        self.assertIn("credit_detect_dataform.git", example)
        self.assertNotIn("credit_detect.git", example)

    def test_dataform_git_remote_always_attached(self):
        """apply が Git を常に宣言し、空トークンで接続を消さない。"""
        self.assertNotIn("ignore_changes = [git_remote_settings]", self.tf)
        self.assertIn("git_remote_settings {", self.tf)
        self.assertIn('default_branch                      = "main"', self.tf)
        self.assertIn(
            "projects/${data.google_project.current.number}/secrets/dataform-github-token/versions/latest",
            self.tf,
        )
        self.assertIn('data "google_project" "current"', self.tf)
        self.assertIn("google_secret_manager_secret_iam_member.dataform_github_token_accessor", self.tf)

    def test_dataform_secret_accessor_iam(self):
        self.assertIn("google_secret_manager_secret_iam_member", self.tf)
        self.assertIn("dataform_github_token_accessor", self.tf)
        self.assertIn("roles/secretmanager.secretAccessor", self.tf)
        self.assertIn("dataform_github_token_secret_id", self.tf)
        self.assertIn(
            'split("/", local.dataform_github_token_secret)[3]',
            self.tf,
        )
        accessor = self.tf.split("dataform_github_token_accessor")[1].split("resource ")[0]
        self.assertNotIn("count", accessor)

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
        self.assertIn("dataform_bq_job_user", self.tf)
        self.assertIn("dataform_bq_data_viewer", self.tf)
        self.assertIn("roles/bigquery.dataViewer", self.tf)

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
                if "node_modules" in path.parts:
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
