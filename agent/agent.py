"""Vertex AI Agent Engine（旧 Reasoning Engine）のデプロイ単位。

【Agent Engine 上の位置づけ】
Agent Engine は本クラスを pickle し、リモートコンテナ起動時に ``set_up()`` を一度呼び、
以降の RPC では ``query(user_query)`` だけを呼ぶ。コンストラクタに BigQuery クライアント
や LangGraph を持たせるとシリアライズに失敗するため、スカラー設定だけを保持し、
重い依存は ``set_up()`` で組み立てる。

Cloud Run の Path A（プロセス内 invoke）も同じクラスを使う。Path B はデプロイ済み
リソースの ``query()`` を遠隔呼び出しするだけなので、戻り値の形をここで固定する。

【主な関数構成】
- FraudInvestigationAgent.__init__: pickle 可能な設定だけを保存する
- FraudInvestigationAgent.set_up: LLM / BQ / RAG / LangGraph を組み立てる
- FraudInvestigationAgent.query: 調査グラフを 1 回実行し、クライアント向けサブセットを返す
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agent.backends import build_deps
from agent.config import AgentConfig
from agent.graph import build_fraud_investigation_graph
from agent.state import initial_state


class FraudInvestigationAgent:
    """Agent Engine が要求する ``set_up()`` / ``query()`` 契約を満たすラッパ。

    Attributes:
        project_id: GCP プロジェクト。local / Agent Engine 双方で BQ・Vertex の宛先になる。
        location: リージョン。東京（asia-northeast1）に揃え FinOps とデータ所在地を一致させる。
        model_name: Gemini モデル ID。Agent Engine 側でも同じ ID を使う。
        dataset: 推論結果・ナレッジを置くデータセット（既定 dwh_prod）。
        backend: mock / local / reasoning_engine。グラフ構築時の依存切替に使う。
        config: テーブル FQDN やリトライ上限をまとめた設定。
        workflow: ``set_up()`` 後のコンパイル済み LangGraph。pickle 前は None。
    """

    def __init__(
        self,
        project_id: str,
        location: str = "asia-northeast1",
        model_name: str = "gemini-2.0-flash",
        dataset: str = "dwh_prod",
        backend: str = "local",
        config: Optional[AgentConfig] = None,
        deps=None,
    ):
        """シリアライズ可能なスカラーと、テスト注入用の deps だけを保持する。

        Agent Engine はコンストラクタ引数を pickle するため、ここで Client を開くと
        デプロイが失敗する。``deps`` は unittest / Streamlit mock が GCP を避けて
        グラフを回すための逃げ道であり、本番の Agent Engine パスでは使わない。

        Args:
            project_id: GCP プロジェクト ID。
            location: Vertex AI / BigQuery のロケーション。
            model_name: ChatVertexAI に渡す Gemini モデル名。
            dataset: 推論・ナレッジテーブルがあるデータセット。
            backend: ``local``（プロセス内 Vertex+BQ）、``mock``（CI/デモ）、
                ``reasoning_engine``（UI 側が遠隔 query する印。本体は local 相当）。
            config: 事前構築した設定。省略時は上記スカラーから生成する。
            deps: テストや mock UI が差し込む RuntimeDeps。Agent Engine では None。
        """
        self.project_id = project_id
        self.location = location
        self.model_name = model_name
        self.dataset = dataset
        self.backend = backend
        self.config = config or AgentConfig(
            project_id=project_id,
            location=location,
            model_name=model_name,
            dataset=dataset,
            backend=backend,
        )
        self._deps = deps
        self.workflow = None

    def set_up(self):
        """pickle 解除後に LangGraph と GCP クライアントを組み立てる。

        Agent Engine は起動時に一度だけ呼ぶ。Cloud Run Path A では ``query()`` が
        未初期化を検知して呼ぶ。戻り値を self にするのは、Reasoning Engine SDK が
        チェーン可能なセットアップを想定しているため。

        Returns:
            初期化済みの自身。Agent Engine ランタイムが保持する。
        """
        deps = self._deps or build_deps(self.config)
        self.workflow = build_fraud_investigation_graph(deps)
        return self

    def query(self, user_query: str) -> Dict[str, Any]:
        """調査グラフを同期実行し、UI / Agent Engine クライアントが使う結果だけ返す。

        AgentState 全体（最大 200 行の生データなど）を返すと、Agent Engine の RPC
        ペイロードと Streamlit 表示が肥大化する。規程本文は照合に足る 1200 文字に
        切り、生行は件数だけ残す。監査に必要な計画・SQL・統計・レポート・是正 SQL は残す。

        Args:
            user_query: アナリストの自然言語指示。グラフの ``user_query`` 初期値になる。

        Returns:
            計画、生成 SQL、統計サマリー、最終レポート、是正 SQL、規程ヒット抜粋、
            Reflection スコアとリトライ回数を含む dict。
        """
        if self.workflow is None:
            # Path A / 単体テストでは set_up を省略して query だけ呼ぶことがある。
            self.set_up()
        final_state = self.workflow.invoke(initial_state(user_query))
        return {
            "user_query": final_state.get("user_query"),
            "plan": final_state.get("plan"),
            "retrieved_policies": [
                {
                    "doc_id": p.get("doc_id"),
                    "category": p.get("category"),
                    "title": p.get("title"),
                    "distance": p.get("distance"),
                    "content": (p.get("content") or "")[:1200],
                }
                for p in (final_state.get("retrieved_policies") or [])
            ],
            "final_report": final_state.get("final_report"),
            "remediation_sql": final_state.get("remediation_sql"),
            "generated_sql": final_state.get("generated_sql"),
            "sql_error": final_state.get("sql_error"),
            "statistical_summary": final_state.get("statistical_summary"),
            "critique": final_state.get("critique"),
            "reflection_score": final_state.get("reflection_score"),
            "retry_count": final_state.get("retry_count"),
            "sql_retry_count": final_state.get("sql_retry_count"),
            "row_count": len(final_state.get("sql_execution_result") or []),
        }
