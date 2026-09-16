"""LLM / SQL / ナレッジ実装を AgentConfig.backend から組み立てる工場。

【Agent Engine 上の位置づけ】
``FraudInvestigationAgent.set_up()`` が呼ぶ。pickle 後のリモートプロセスで初めて
ChatVertexAI と BigQuery Client を開くため、コンストラクタではなくここに置く。

``reasoning_engine`` を特別扱いしない。Path B は UI が遠隔 ``query()`` するだけで、
Agent Engine コンテナ内のグラフは常に Vertex + BigQuery（local 相当）で動く。
mock は CI と画面デモ専用で、Agent Engine には載せない。

【主な関数構成】
- default_knowledge_dir: リポジトリ同梱マニュアルの場所
- load_mock_knowledge: GCP なしで RAG を成立させる
- build_deps: backend に応じた RuntimeDeps を返す
"""

from __future__ import annotations

from pathlib import Path

from agent.config import AgentConfig, RuntimeDeps
from agent.llm import ScriptedLlm, VertexLlm
from agent.tools.bq_client import BigQuerySqlRunner, MockSqlRunner
from agent.tools.bq_vector_search import BigQueryKnowledgeStore, InMemoryKnowledgeStore
from agent.tools.knowledge_ingest import documents_from_directory


def default_knowledge_dir() -> Path:
    """パッケージではなくリポジトリルートの knowledge/ を返す。

    Agent Engine デプロイ時は extra_packages に knowledge を同梱する前提。
    エージェントコードだけを上げると mock / 初期投入のコーパスが空になる。

    Returns:
        ``knowledge/`` ディレクトリの絶対 Path。
    """
    return Path(__file__).resolve().parents[1] / "knowledge"


def load_mock_knowledge() -> InMemoryKnowledgeStore:
    """同梱 Markdown をメモリに載せ、CI と Streamlit mock が規程ヒットを再現できるようにする。

    Returns:
        初期マニュアルを upsert 済みの InMemoryKnowledgeStore。
    """
    store = InMemoryKnowledgeStore()
    knowledge_dir = default_knowledge_dir()
    if knowledge_dir.is_dir():
        store.upsert(documents_from_directory(knowledge_dir))
    return store


def build_deps(config: AgentConfig) -> RuntimeDeps:
    """backend に応じて、グラフが要求する 3 協作者（LLM / SQL / RAG）を揃える。

    mock は Vertex も BQ も呼ばない。これがないと unittest と PR CI が GCP 認証に依存する。
    それ以外は Agent Engine / Cloud Run 本番相当の VertexLlm + BigQuery 実装。

    Args:
        config: backend とプロジェクトを含む実行設定。

    Returns:
        LangGraph 構築に渡す RuntimeDeps。
    """
    backend = (config.backend or "local").lower()
    if backend == "mock":
        return RuntimeDeps(
            config=config,
            llm=ScriptedLlm(),
            sql_runner=MockSqlRunner(),
            knowledge=load_mock_knowledge(),
        )

    llm = VertexLlm(
        project_id=config.project_id,
        location=config.location,
        model_name=config.model_name,
    )
    return RuntimeDeps(
        config=config,
        llm=llm,
        sql_runner=BigQuerySqlRunner(config),
        knowledge=BigQueryKnowledgeStore(config),
    )
