"""公開テーブルの地域コピー（GCP は呼ばない）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "batch_app"))

import copy_public_source  # noqa: E402
import daily_insert  # noqa: E402


class CopyPublicSourceTest(unittest.TestCase):
    def test_default_destination_matches_dataform_declaration(self):
        decl = (
            ROOT / "dataform" / "definitions" / "declarations" / "ulb_fraud_detection_public.sqlx"
        ).read_text(encoding="utf-8")
        self.assertIn(f'name: "{copy_public_source.DEFAULT_TABLE_NAME}"', decl)
        self.assertEqual(
            copy_public_source.default_destination_table("skir_sample_credit"),
            "skir_sample_credit.dwh_prod.ulb_fraud_detection_public",
        )

    def test_query_runs_in_us(self):
        self.assertEqual(daily_insert.BQ_QUERY_LOCATION, "US")
        src = (ROOT / "batch_app" / "copy_public_source.py").read_text(encoding="utf-8")
        self.assertIn("location=BQ_QUERY_LOCATION", src)
        self.assertIn("bigquery-public-data.ml_datasets.ulb_fraud_detection", src)

    def test_missing_project_id(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                copy_public_source.run_copy()


if __name__ == "__main__":
    unittest.main()
