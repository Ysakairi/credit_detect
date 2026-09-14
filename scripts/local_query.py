#!/usr/bin/env python3
"""Run one investigation locally (mock or Vertex/BigQuery)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.agent import FraudInvestigationAgent
from agent.config import AgentConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--query",
        default=(
            "直近の推論データで fraud_probability>=0.85 かつ Amount>=200 の取引を調査し、"
            "規程に照らした監視SQLを提案して"
        ),
    )
    parser.add_argument("--backend", default=os.environ.get("AGENT_BACKEND", "mock"))
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID", "local-mock-project"))
    parser.add_argument("--location", default=os.environ.get("LOCATION", "asia-northeast1"))
    parser.add_argument("--model-name", default=os.environ.get("MODEL_NAME", "gemini-2.0-flash"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = AgentConfig(
        project_id=args.project_id,
        location=args.location,
        model_name=args.model_name,
        backend=args.backend,
    )
    agent = FraudInvestigationAgent(
        project_id=config.project_id,
        location=config.location,
        model_name=config.model_name,
        backend=config.backend,
        config=config,
    )
    result = agent.query(args.query)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
