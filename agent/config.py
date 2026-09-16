"""実行設定と、credit_detect の実テーブルカタログ。

【Agent Engine 上の位置づけ】
pickle されるのは ``FraudInvestigationAgent`` が持つスカラーだけだが、``set_up()`` 後の
LangGraph ノードは本設定を閉包で参照する。テーブル名をプロンプトに埋め込まないと、
LLM が設計メモのプレースホルダ（``credit_detect.daily_prediction`` や
``predicted_Class_probs``）を生成し、Agent Engine 上の Text-to-SQL が必ず失敗する。

定数のリトライ回数・行数・スキャン上限は FinOps 用。Agent Engine は従量課金のため、
ノード内にマジックナンバーを散らさずここで一箇所に置く。

【主な関数構成】
- AgentConfig: プロジェクト・モデル・テーブル FQDN・リトライ上限
- AgentConfig.from_env: Cloud Run / ローカルの環境変数から復元する
- schema_prompt: Planner / SQL Gen に実スキーマを注入する
- RuntimeDeps: GCP 非シリアライズオブジェクトをグラフへ注入する入れ物
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List


DEFAULT_LOCATION = "asia-northeast1"
DEFAULT_DATASET = "dwh_prod"
DEFAULT_MODEL_NAME = "gemini-2.0-flash"
# 設計メモは 1.5-flash-002。東京リージョンで安定して使える 2.0-flash を既定にする。
LEGACY_MODEL_NAME = "gemini-1.5-flash-002"
DEFAULT_EMBEDDING_MODEL = "text-embedding-004"
MAX_SQL_RETRIES = 2
MAX_REFLECTION_RETRIES = 2
MAX_QUERY_ROWS = 200
MAX_BYTES_BILLED = 10 * 1024 * 1024 * 1024  # 10 GiB。全表スキャン事故を Agent Engine 側で止める。
REFLECTION_PASS_SCORE = 80
EMBEDDING_DIMENSIONS = 768
VECTOR_TOP_K = 3


def _env(name: str, default: str = "") -> str:
    """空文字を「未設定」とみなし、Cloud Run の空 env で default が死なないようにする。

    Args:
        name: 環境変数名。
        default: 未設定または空白のみのときの代替値。

    Returns:
        strip 済みの値。未設定なら default。
    """
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


@dataclass(frozen=True)
class AgentConfig:
    """グラフとツールが共有する実行設定。frozen にしてノード間の意図しない上書きを防ぐ。

    Attributes:
        project_id: BQ / Vertex のプロジェクト。
        location: リージョン。データ所在地とモデルリージョンを一致させる。
        dataset: 推論・ナレッジのデータセット。
        model_name: Gemini モデル。
        embedding_model: RAG 用埋め込み。Vector Search Endpoint を使わない前提の 004。
        staging_bucket: Agent Engine デプロイ時の GCS。実行時グラフでは未使用。
        backend: mock / local / reasoning_engine。
        reasoning_engine_resource: Path B のリソース名。UI が読む。
        max_sql_retries: SQL 失敗の再生成上限。無制限だと Gemini+BQ が回り続ける。
        max_reflection_retries: 不足判定後の再調査上限。
        max_query_rows: SELECT 結果の行キャップ。Analyzer の CPU も抑える。
        max_bytes_billed: BQ ジョブのスキャン上限。
    """

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
        """プロンプトと SQL に使う完全修飾データセット名。"""
        return f"{self.project_id}.{self.dataset}"

    @property
    def predictions_table(self) -> str:
        """日次 BQML 推論結果。エージェントの主データ源。"""
        return f"{self.fq_dataset}.ulb_fraud_detection_predictions"

    @property
    def batch_table(self) -> str:
        """当日スライス。必要なら SQL Gen が参照する。"""
        return f"{self.fq_dataset}.ulb_fraud_detection_Batch"

    @property
    def converted_table(self) -> str:
        """Hour 付与済み特徴量。推論テーブルに無い列を取るときに使う。"""
        return f"{self.fq_dataset}.ulb_fraud_detection_daily_converted"

    @property
    def bqml_model(self) -> str:
        """EXPLAIN / FEATURE_IMPORTANCE を SQL から呼ぶためのモデル ID。"""
        return f"{self.fq_dataset}.ulb_fraud_detection_model"

    @property
    def knowledge_table(self) -> str:
        """Hybrid RAG のコーパス。Vertex AI Vector Search Endpoint の代替。"""
        return f"{self.fq_dataset}.fraud_investigation_knowledge"

    @property
    def embedding_bq_model(self) -> str:
        """BQ リモート埋め込みモデルを使う場合の予約名。PoC は Vertex SDK 直呼び。"""
        return f"{self.fq_dataset}.embedding_model"

    @property
    def public_source_table(self) -> str:
        """公開元。Date / 予測列が無いことをプロンプトで明示するために持つ。"""
        return "bigquery-public-data.ml_datasets.ulb_fraud_detection"

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Cloud Run / ローカルシェルの環境変数から復元する。

        PROJECT_ID が空でも mock デモを落とさないようプレースホルダを入れる。
        Agent Engine 本体はコンストラクタ引数で project を渡すため、ここは UI と
        スクリプト向け。

        Returns:
            環境変数を反映した AgentConfig。
        """
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


# Dataform daily_prediction.sqlx が実際に出す列。配列 predicted_Class_probs は残っていない。
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
    """Planner / Text-to-SQL に実在テーブルだけを見せ、幻覚テーブルを防ぐ。

    Agent Engine 上の Gemini は学習データ上の汎用スキーマを好みやすい。
    ここに FQDN と「UNNEST 不要」「昨日 = MAX(Date)」を書いておかないと、
    生成 SQL がガード通過後に BQ で落ち、SQL リトライ予算を食い潰す。

    Args:
        config: プロジェクト依存のテーブル FQDN を解決するための設定。

    Returns:
        プロンプトに連結するスキーマカタログ文字列。
    """
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
    """LangGraph に渡す実行時依存。GCP クライアントは pickle できないのでここに隔離する。

    unittest と mock UI が LLM/SQL/RAG を差し替え、Agent Engine 無しでグラフを検証するため。
    """

    config: AgentConfig
    llm: object = None
    sql_runner: object = None
    knowledge: object = None
    extra: Dict[str, object] = field(default_factory=dict)
