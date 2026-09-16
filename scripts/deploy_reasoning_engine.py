"""FraudInvestigationAgent を Vertex AI Agent Engine へデプロイする。

【Agent Engine 上の位置づけ】
ローカルの ``FraudInvestigationAgent`` インスタンスを ``agent_engines.create`` に渡し、
リモートの ``set_up()`` / ``query()`` エンドポイントを作る。pickle されるのはクラスの
スカラーだけなので、実行時 import に必要な ``agent/`` ``evaluate/`` ``knowledge/`` を
extra_packages で同梱する。evaluate を落とすと Analyzer が SOP カーネルを読めない。

SDK 名は Agent Engine ← Reasoning Engine へ移行中のため、新 API 失敗時は preview
の ReasoningEngine.create へ倒す。失敗を即終了にすると Path B の構築手順が止まる。

【主な関数構成】
- parse_args: プロジェクト / バケット / モデル
- deploy: vertexai.init → create → リソース名を表示
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.agent import FraudInvestigationAgent


REQUIREMENTS = [
    "google-cloud-aiplatform>=1.70.0",
    "google-cloud-bigquery>=3.25.0",
    "langchain-google-vertexai>=2.0.0",
    "langchain-core>=0.3.0",
    "langgraph>=0.2.0",
    "numpy>=1.24.0",
    "pandas>=2.0.0",
    "scipy>=1.11.0",
]


def parse_args() -> argparse.Namespace:
    """デプロイ先と表示名を環境変数から取る。IaC と CLI で同じ変数名を共有するため。

    Returns:
        argparse.Namespace（project_id, location, staging_bucket, model_name, dataset, display_name）。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--location", default=os.environ.get("LOCATION", "asia-northeast1"))
    parser.add_argument(
        "--staging-bucket",
        default=os.environ.get("STAGING_BUCKET"),
        help="gs://bucket-name",
    )
    parser.add_argument("--model-name", default=os.environ.get("MODEL_NAME", "gemini-2.0-flash"))
    parser.add_argument("--dataset", default=os.environ.get("DATASET_ID", "dwh_prod"))
    parser.add_argument("--display-name", default="fraud-investigation-agent")
    return parser.parse_args()


def deploy(args: argparse.Namespace):
    """エージェントを Agent Engine に登録し、Cloud Run が設定すべきリソース名を返す。

    staging_bucket 必須は、SDK がソースと依存を GCS 経由でリモートへ渡すため。
    backend は ``local`` 固定。リモート内側は常に Vertex+BQ であり、UI の
    reasoning_engine 切替とは別レイヤ。

    Args:
        args: parse_args() の結果。

    Returns:
        デプロイ済みリソース名（REASONING_ENGINE_RESOURCE_NAME に設定する値）。
    """
    if not args.project_id or not args.staging_bucket:
        raise SystemExit("PROJECT_ID と STAGING_BUCKET (gs://...) が必要です")

    import vertexai

    vertexai.init(
        project=args.project_id,
        location=args.location,
        staging_bucket=args.staging_bucket,
    )

    local_agent = FraudInvestigationAgent(
        project_id=args.project_id,
        location=args.location,
        model_name=args.model_name,
        dataset=args.dataset,
        backend="local",
    )

    extra = [str(ROOT / "agent"), str(ROOT / "evaluate"), str(ROOT / "knowledge")]

    try:
        from vertexai import agent_engines

        remote = agent_engines.create(
            local_agent,
            requirements=REQUIREMENTS,
            extra_packages=extra,
            display_name=args.display_name,
            description="Autonomous credit-card fraud investigation agent (LangGraph)",
        )
        resource_name = getattr(remote, "resource_name", None) or str(remote)
    except Exception as primary:
        print(f"agent_engines.create failed ({primary}); falling back to reasoning_engines")
        from vertexai.preview import reasoning_engines

        remote = reasoning_engines.ReasoningEngine.create(
            local_agent,
            requirements=REQUIREMENTS,
            extra_packages=extra,
            display_name=args.display_name,
            description="Autonomous credit-card fraud investigation agent (LangGraph)",
        )
        resource_name = remote.resource_name

    print(f"Deployed: {resource_name}")
    print("Cloud Run の環境変数 REASONING_ENGINE_RESOURCE_NAME にこの値を設定してください。")
    return resource_name


if __name__ == "__main__":
    deploy(parse_args())
