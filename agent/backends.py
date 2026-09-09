"""Assemble LLM / SQL / knowledge backends from AgentConfig."""

from __future__ import annotations

from pathlib import Path

from agent.config import AgentConfig, RuntimeDeps
from agent.llm import ScriptedLlm, VertexLlm
from agent.tools.bq_client import BigQuerySqlRunner, MockSqlRunner
from agent.tools.bq_vector_search import BigQueryKnowledgeStore, InMemoryKnowledgeStore
from agent.tools.knowledge_ingest import documents_from_directory


def default_knowledge_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "knowledge"


def load_mock_knowledge() -> InMemoryKnowledgeStore:
    store = InMemoryKnowledgeStore()
    knowledge_dir = default_knowledge_dir()
    if knowledge_dir.is_dir():
        store.upsert(documents_from_directory(knowledge_dir))
    return store


def build_deps(config: AgentConfig) -> RuntimeDeps:
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
