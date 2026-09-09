"""Runtime configuration and the live credit_detect schema catalog.

Table names follow the Dataform pipeline (`dwh_prod`), not the generic
`credit_detect.daily_prediction` placeholders in the original design note.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List


DEFAULT_LOCATION = "asia-northeast1"
DEFAULT_DATASET = "dwh_prod"
DEFAULT_MODEL_NAME = "gemini-2.0-flash"
# Design doc used gemini-1.5-flash-002. Prefer 2.0-flash on Tokyo (2026).
LEGACY_MODEL_NAME = "gemini-1.5-flash-002"
DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
MAX_SQL_RETRIES = 2
MAX_REFLECTION_RETRIES = 2
MAX_QUERY_ROWS = 200
MAX_BYTES_BILLED = 10 * 1024 * 1024 * 1024  # 10 GiB safety cap
REFLECTION_PASS_SCORE = 80
EMBEDDING_DIMENSIONS = 768
VECTOR_TOP_K = 3


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


@dataclass(frozen=True)
class AgentConfig:
    project_id: str
    location: str = DEFAULT_LOCATION
    dataset: str = DEFAULT_DATASET
    model_name: str = DEFAULT_MODEL_NAME
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    staging_bucket: str = ""
    backend: str = "local"  # local | reasoning_engine | mock
    reasoning_engine_resource: str = ""
    max_sql_retries: int = MAX_SQL_RETRIES
    max_reflection_retries: int = MAX_REFLECTION_RETRIES
    max_query_rows: int = MAX_QUERY_ROWS
    max_bytes_billed: int = MAX_BYTES_BILLED

    @property
    def fq_dataset(self) -> str:
        return f"{self.project_id}.{self.dataset}"

    @property
    def predictions_table(self) -> str:
        return f"{self.fq_dataset}.ulb_fraud_detection_predictions"

    @property
    def batch_table(self) -> str:
        return f"{self.fq_dataset}.ulb_fraud_detection_Batch"

    @property
    def converted_table(self) -> str:
        return f"{self.fq_dataset}.ulb_fraud_detection_daily_converted"

    @property
    def bqml_model(self) -> str:
        return f"{self.fq_dataset}.ulb_fraud_detection_model"

    @property
    def knowledge_table(self) -> str:
        return f"{self.fq_dataset}.fraud_investigation_knowledge"

    @property
    def embedding_bq_model(self) -> str:
        return f"{self.fq_dataset}.embedding_model"

    @property
    def public_source_table(self) -> str:
        return "bigquery-public-data.ml_datasets.ulb_fraud_detection"

    @classmethod
    def from_env(cls) -> "AgentConfig":
        project_id = _env("PROJECT_ID") or _env("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            project_id = "local-mock-project"
        return cls(
            project_id=project_id,
            location=_env("LOCATION", DEFAULT_LOCATION),
            dataset=_env("DATASET_ID", DEFAULT_DATASET),
            model_name=_env("MODEL_NAME", DEFAULT_MODEL_NAME),
            embedding_model=_env("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            staging_bucket=_env("STAGING_BUCKET"),
            backend=_env("AGENT_BACKEND", "local"),
            reasoning_engine_resource=_env("REASONING_ENGINE_RESOURCE_NAME"),
        )


# Columns actually produced by Dataform daily_prediction.sqlx.
PREDICTION_COLUMNS: List[Dict[str, str]] = [
    {"name": "Time", "type": "FLOAT64", "desc": "Seconds elapsed from the first transaction in the source set"},
    {"name": "Amount", "type": "FLOAT64", "desc": "Transaction amount in USD"},
    {"name": "Class", "type": "INT64", "desc": "Ground-truth label: 1 = fraud, 0 = legitimate"},
    {"name": "Hour", "type": "INT64", "desc": "Hour of day derived from Time (0-23)"},
    {"name": "Date", "type": "INT64", "desc": "Pseudo-day slice 1-50 used by the ingest job"},
    {"name": "predicted_Class", "type": "INT64", "desc": "BQML predicted class"},
    {
        "name": "fraud_probability",
        "type": "FLOAT64",
        "desc": "P(Class=1) extracted from predicted_Class_probs; use this instead of UNNEST",
    },
]

PCA_FEATURES = [f"V{i}" for i in range(1, 29)]
AUDIT_FEATURES = ["V14", "V17", "V12"]

EVALUATION_TABLES: Dict[str, str] = {
    "evaluation_matrix": "ulb_fraud_detection_evaluation_matrix",
    "feature_separation": "ulb_fraud_detection_feature_separation",
    "feature_importance": "ulb_fraud_detection_feature_importance",
    "global_explain": "ulb_fraud_detection_global_explain",
    "local_explain": "ulb_fraud_detection_local_explain",
    "imbalance_metrics": "ulb_fraud_detection_imbalance_metrics",
    "psi": "ulb_fraud_detection_psi",
}


def schema_prompt(config: AgentConfig) -> str:
    """Injected into the Text-to-SQL prompt so the LLM uses real table names."""
    pred_cols = "\n".join(
        f"    - {c['name']} {c['type']}: {c['desc']}" for c in PREDICTION_COLUMNS
    )
    eval_lines = "\n".join(
        f"    - `{config.fq_dataset}.{table}`" for table in EVALUATION_TABLES.values()
    )
    pca = ", ".join(PCA_FEATURES)
    return f"""
【credit_detect 実テーブル（必ずこの識別子を使う）】
1. `{config.predictions_table}`
   日次 BQML 推論結果。不正確率はスカラー列 fraud_probability。
   predicted_Class_probs 配列は本テーブルには残っていない。
{pred_cols}
   PCA 特徴量: {pca}

2. `{config.converted_table}`
   日次スライスの特徴量（Hour 付与済み）。V1〜V28, Amount, Time, Class, Date, Hour。

3. `{config.batch_table}`
   Cloud Run Jobs が WRITE_TRUNCATE する当日スライス。

4. `{config.public_source_table}`
   公開元データ。Time, V1〜V28, Amount, Class。Date / Hour / 予測列は無い。

5. BQML モデル: `{config.bqml_model}`
   BOOSTED_TREE_CLASSIFIER。ML.EXPLAIN_PREDICT / ML.FEATURE_IMPORTANCE / ML.GLOBAL_EXPLAIN が利用可能。

6. 評価テーブル（SOP-MLOPS-2026-002）:
{eval_lines}

【SQL 制約】
- BigQuery 標準SQL。SELECT または WITH のみ。DML/DDL 禁止。
- 結果は LIMIT {config.max_query_rows} 以下。
- 高リスク抽出の既定: fraud_probability >= 0.85 または Amount 閾値。
- 擬似日付は Date 列（1〜50）。「昨日」は MAX(Date) を使う。
- 監査重点変数: {", ".join(AUDIT_FEATURES)}。
""".strip()


@dataclass
class RuntimeDeps:
    """Injectable collaborators so the graph can run without GCP in tests."""

    config: AgentConfig
    llm: object = None
    sql_runner: object = None
    knowledge: object = None
    extra: Dict[str, object] = field(default_factory=dict)
